"""#29 rerank 模型横评：质量-延迟帕累托。

回答「rerank 值不值得默认开 / 换哪个模型」。对象 = **重排调用**（不含 embedding /
向量检索 / 端到端）；质量用 #24 确定性评测集（记忆检索，条目级二值）+ legal 锚点
（排名一致度，无金标）。不调 LLM。

设计（见 HANDOFF-29）：
- 候选池**离线缓存一次**（`memory-pools` / `legal-pools`，含 BGE-M3；两者分开进程跑，
  同一时刻只起一份 BGE-M3），之后按模型评测只加载**候选重排器**，不再起 BGE-M3。
- `memory` / `legal`：把选中的模型**全部载入同一进程、逐轮交错**测延迟（抵消机器漂移），
  同时记录加载 dtype / `max_seq_length` / 峰值 RSS / 逐模型 token 长度。
- 换模型只走本脚本的 `models.json` + 构造参数，不碰 `ragcore/**` 与
  `memory_agent/eval/retrieval_eval.py`。

环境（脚本不自带 .env，需外部喂）：
- `MEMORY_INDEX_DIR`        → 主树的 `memory_agent/vector_db`（生成 gen-2 所在的根）
- `MEMORY_READONLY_REPOS_CONFIG`（可选）
- `VECTOR_DB_PATH`          → 主树的 `legal_web/vector_db`（legal 用）

用法：
    # 0) 拉取候选权重到 HF 缓存（只做一次，走网络）
    python bench_rerank_models.py fetch
    # 1) 各起一次进程构建候选池缓存（各起一份 BGE-M3）
    python bench_rerank_models.py memory-pools
    python bench_rerank_models.py legal-pools
    # 2) 读缓存、换模型评测（不碰 BGE-M3）
    python bench_rerank_models.py memory --out memory_results.json
    python bench_rerank_models.py legal  --out legal_results.json
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import re
import statistics
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
RAGCORE = os.path.join(REPO_ROOT, "ragcore")
for path in (RAGCORE, REPO_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

CACHE_DIR = os.path.join(HERE, "cache")
MEMORY_POOLS = os.path.join(CACHE_DIR, "memory_pools.json")
LEGAL_POOLS = os.path.join(CACHE_DIR, "legal_pools.json")
MODELS_JSON = os.path.join(HERE, "models.json")

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# --------------------------------------------------------------------- utils

def _pct(values, pct):
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * pct), len(ordered) - 1)]


def _latency_summary(values):
    if not values:
        return None
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 3),
        "p50": round(_pct(values, 0.50), 3),
        "p95": round(_pct(values, 0.95), 3),
        "max": round(max(values), 3),
        "sd": round(statistics.pstdev(values), 3) if len(values) > 1 else 0.0,
    }


def _kendall_tau(a, b):
    pos_b = {doc: i for i, doc in enumerate(b)}
    conc = disc = 0
    for i in range(len(a)):
        for j in range(i + 1, len(a)):
            if a[i] not in pos_b or a[j] not in pos_b:
                continue
            if pos_b[a[i]] < pos_b[a[j]]:
                conc += 1
            elif pos_b[a[i]] > pos_b[a[j]]:
                disc += 1
    total = conc + disc
    return round((conc - disc) / total, 4) if total else 1.0


def _md5(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


def load_registry():
    with open(MODELS_JSON, "r", encoding="utf-8") as handle:
        return json.load(handle)["models"]


def select_models(keys):
    models = load_registry()
    if not keys:
        return models
    wanted = {k.strip() for k in keys.split(",") if k.strip()}
    chosen = [m for m in models if m["key"] in wanted]
    missing = wanted - {m["key"] for m in chosen}
    if missing:
        raise SystemExit(f"models.json 里没有这些 key：{sorted(missing)}")
    return chosen


class PeakRSSMonitor:
    """后台采样进程峰值 RSS（psutil 不可用时静默降级）。"""

    def __init__(self, interval=0.2):
        self.interval = interval
        self.peak = 0
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        try:
            import psutil  # noqa: F401
        except Exception:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        import psutil
        proc = psutil.Process()
        while not self._stop.is_set():
            try:
                self.peak = max(self.peak, proc.memory_info().rss)
            except Exception:
                break
            time.sleep(self.interval)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        return round(self.peak / 1e9, 3) if self.peak else None


def _rss():
    try:
        import psutil
        return psutil.Process().memory_info().rss
    except Exception:
        return 0


# ------------------------------------------------------------------ encoder

def build_encoder(spec, max_seq_length, local_files_only):
    """按 models.json 构造 CrossEncoder（生产 RerankerService 的同形参数 + dtype 覆盖）。"""
    import torch
    from sentence_transformers import CrossEncoder

    kwargs = {
        "trust_remote_code": bool(spec.get("trust_remote_code", False)),
        "local_files_only": local_files_only,
    }
    if max_seq_length is not None:
        kwargs["max_length"] = int(max_seq_length)

    model_kwargs = {}
    if spec.get("backend") == "onnx":
        kwargs["backend"] = "onnx"
        if spec.get("onnx_file"):
            model_kwargs["file_name"] = spec["onnx_file"]
    else:
        kwargs["device"] = "cpu"
        if spec.get("torch_dtype"):
            model_kwargs["torch_dtype"] = getattr(torch, spec["torch_dtype"])
    if model_kwargs:
        kwargs["model_kwargs"] = model_kwargs

    return CrossEncoder(spec["id"], **kwargs)


def encoder_dtype(encoder):
    try:
        return str(next(encoder.model.parameters()).dtype)
    except Exception:
        return None


def encoder_max_seq_length(encoder):
    return getattr(encoder, "max_seq_length", None)


def rerank_pairs(encoder, query, docs, top_k=None):
    """与 RerankerService.rerank 同形：predict 全部候选，按分数降序，取 top_k。"""
    if not docs:
        return []
    scores = encoder.predict([[query, doc] for doc in docs], convert_to_tensor=False)
    ranked = sorted(zip((float(s) for s in scores), docs), key=lambda x: x[0], reverse=True)
    return ranked if top_k is None else ranked[:top_k]


# ------------------------------------------------------- memory pools (BGE-M3)

def cmd_memory_pools(args):
    from memory_agent.eval.retrieval_eval import DEFAULT_EVAL_SET, load_eval_set
    from memory_agent.memory.index import MemoryIndex
    from memory_agent.settings import RERANK_MAX_CHARS, RETRIEVAL_POOL
    from strategies.default import DefaultRetrievalStrategy

    eval_set = load_eval_set(args.eval_set or DEFAULT_EVAL_SET)
    queries = eval_set["queries"]
    if args.limit:
        queries = queries[:args.limit]

    index = MemoryIndex()  # 解析 CURRENT 指针到当前代；只读
    store = index.store
    strategy = DefaultRetrievalStrategy(enable_keyword=True)
    max_chars = RERANK_MAX_CHARS
    pool_size = RETRIEVAL_POOL

    pools = []
    for i, query in enumerate(queries, start=1):
        result = strategy.retrieve(query["query"], store, pool_size=pool_size)
        docs = result["documents"][0] if result.get("documents") else []
        metas = result["metadatas"][0] if result.get("metadatas") else []
        # 复刻 MemoryRetriever._rerank 的去重：截断文本唯一、先到先得
        unique: dict[str, dict] = {}
        for doc, meta in zip(docs, metas):
            text = doc[:max_chars]
            if text not in unique:
                unique[text] = {
                    "text": text,
                    "entry_id": (meta or {}).get("entry_id"),
                    "source": (meta or {}).get("source"),
                    "full_chars": len(doc),
                }
        pools.append({
            "id": query["id"],
            "query": query["query"],
            "relevant": list(query.get("relevant") or []),
            "pool": list(unique.values()),
        })
        print(f"[{i}/{len(queries)}] {query['id']} pool={len(unique)}", file=sys.stderr)

    payload = {
        "meta": {
            "eval_set": os.path.relpath(os.path.abspath(args.eval_set or DEFAULT_EVAL_SET), REPO_ROOT),
            "eval_set_name": eval_set.get("name"),
            "eval_set_version": eval_set.get("version"),
            "index_gen": index.gen,
            "pool_size": pool_size,
            "rerank_max_chars": max_chars,
            "queries": len(pools),
            "pairs": sum(len(p["pool"]) for p in pools),
        },
        "queries": pools,
    }
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(MEMORY_POOLS, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print(json.dumps(payload["meta"], ensure_ascii=False, indent=2))
    print(f"written -> {MEMORY_POOLS}")
    return payload


# -------------------------------------------------------- legal pools (BGE-M3)

def _load_questions(path, limit=50):
    out = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            m = re.match(r"^-\s*\[([^\]]+)\]\s*(.+)$", line.strip())
            if m:
                out.append((m.group(1).strip(), m.group(2).strip()))
    return out[:limit]


def _sample(rows, n):
    if len(rows) <= n:
        return rows
    step = len(rows) / n
    return [rows[int(i * step)] for i in range(n)]


def cmd_legal_pools(args):
    from config.config import ADAPTIVE_POOL
    from services.vector_store_service import VectorStoreService
    from strategies.legal import LegalRetrievalStrategy

    questions = _sample(_load_questions(os.path.join(REPO_ROOT, "legal_web", "tests", "questions.md")),
                        args.n)
    vs = VectorStoreService(collection_name="documents")
    strategy = LegalRetrievalStrategy()

    pools = []
    for i, (qtype, question) in enumerate(questions, start=1):
        res = strategy.retrieve(question, vs, pool_size=ADAPTIVE_POOL)
        docs = res["documents"][0] if res.get("documents") else []
        uniq = {}
        for doc in docs:
            h = _md5(doc)
            if h not in uniq:
                uniq[h] = {"hash": h, "text": doc, "chars": len(doc)}
        pools.append({"qtype": qtype, "question": question, "pool": list(uniq.values())})
        print(f"[{i}/{len(questions)}] pool={len(uniq)}", file=sys.stderr)

    payload = {
        "meta": {
            "questions_file": "legal_web/tests/questions.md",
            "sampled": len(pools),
            "pool_size": ADAPTIVE_POOL,
            "pairs": sum(len(p["pool"]) for p in pools),
        },
        "queries": pools,
    }
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(LEGAL_POOLS, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print(json.dumps(payload["meta"], ensure_ascii=False, indent=2))
    print(f"written -> {LEGAL_POOLS}")
    return payload


# --------------------------------------------------------------- model loads

def load_all(models, max_seq_length, local_files_only):
    loaded = []
    for spec in models:
        print(f"loading {spec['key']} ({spec['id']}) ...", file=sys.stderr)
        rss0 = _rss()
        started = time.time()
        encoder = build_encoder(spec, max_seq_length, local_files_only)
        load_s = time.time() - started
        loaded.append({
            "key": spec["key"],
            "id": spec["id"],
            "spec": spec,
            "encoder": encoder,
            "load_s": round(load_s, 2),
            "rss_delta_gb": round((_rss() - rss0) / 1e9, 3),
            "dtype": encoder_dtype(encoder),
            "max_seq_length": encoder_max_seq_length(encoder),
        })
        print(f"  loaded in {load_s:.1f}s dtype={loaded[-1]['dtype']} "
              f"max_seq_length={loaded[-1]['max_seq_length']} "
              f"rss_delta={loaded[-1]['rss_delta_gb']}GB", file=sys.stderr)
    return loaded


def token_stats(encoder, pairs):
    """用模型自己的 tokenizer 统计 pair 长度（max / mean / >上限数）。"""
    tokenizer = getattr(encoder, "tokenizer", None)
    if tokenizer is None:
        return None
    cap = encoder_max_seq_length(encoder)
    lens = []
    for query, doc in pairs:
        try:
            lens.append(len(tokenizer(query, doc)["input_ids"]))
        except Exception:
            return None
    if not lens:
        return None
    over = sum(1 for L in lens if cap and L > cap)
    return {
        "pairs": len(lens),
        "max": max(lens),
        "mean": round(statistics.mean(lens), 1),
        "over_cap": over,
        "cap": cap,
    }


# ------------------------------------------------------------ memory quality

def cmd_memory(args):
    from memory_agent.eval.metrics import evaluate
    from memory_agent.eval.retrieval_eval import run_hash

    with open(MEMORY_POOLS, "r", encoding="utf-8") as handle:
        cached = json.load(handle)
    queries = cached["queries"]
    if args.limit:
        queries = queries[:args.limit]

    models = select_models(args.models)
    monitor = PeakRSSMonitor()
    monitor.start()
    loaded = load_all(models, args.max_seq_length, local_files_only=not args.fetch)

    # 预热（丢弃；只取前 2 条，够触发模型首次调用即可）
    for m in loaded:
        for q in queries[:2]:
            rerank_pairs(m["encoder"], q["query"], [c["text"] for c in q["pool"]])

    latency = {m["key"]: {q["id"]: [] for q in queries} for m in loaded}
    rankings = {m["key"]: {} for m in loaded}
    for round_index in range(args.rounds):
        for m in loaded:
            for q in queries:
                texts = [c["text"] for c in q["pool"]]
                started = time.perf_counter()
                ranked = rerank_pairs(m["encoder"], q["query"], texts)
                latency[m["key"]][q["id"]].append((time.perf_counter() - started) * 1000.0)
                if round_index == args.rounds - 1:
                    rankings[m["key"]][q["id"]] = ranked
            print(f"round {round_index + 1}/{args.rounds} {m['key']} done", file=sys.stderr)

    peak_rss = monitor.stop()
    results = {}
    anchor = None
    for m in loaded:
        by_text = {c["text"]: c["entry_id"] for q in queries for c in q["pool"]}
        records = []
        for q in queries:
            ranked = rankings[m["key"]][q["id"]]
            ids = [by_text.get(text) for _, text in ranked][:args.top_k]
            records.append({
                "id": q["id"], "query": q["query"], "relevant": q["relevant"],
                "ranked": ids, "ranked_scores": [score for score, _ in ranked][:args.top_k],
            })
        report = evaluate(records)
        all_times = [t for q in queries for t in latency[m["key"]][q["id"]]]
        pairs = sum(len(q["pool"]) for q in queries)
        token = token_stats(
            m["encoder"],
            [(q["query"], c["text"]) for q in queries for c in q["pool"]],
        )
        entry = {
            "model": m["id"], "key": m["key"], "license": m["spec"]["license"],
            "role": m["spec"]["role"], "dtype": m["dtype"],
            "max_seq_length": m["max_seq_length"], "load_s": m["load_s"],
            "rss_delta_gb": m["rss_delta_gb"], "peak_rss_gb": peak_rss,
            "pairs": pairs,
            "latency_ms": _latency_summary(all_times),
            "pairs_per_s": round(pairs * args.rounds / (sum(all_times) / 1000.0), 2)
                            if all_times else None,
            "tokens": token,
            "aggregate": report["aggregate"],
            "per_query": report["per_query"],
            "no_answer": report["no_answer"],
        }
        entry["run_hash"] = run_hash(report)
        results[m["key"]] = entry
        if m["key"] == "bge-v2-m3":
            anchor = entry

    payload = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "pools_meta": cached["meta"],
            "modes": "memory hybrid-rerank (deterministic, no LLM)",
            "rounds": args.rounds,
            "max_seq_length": args.max_seq_length,
            "peak_rss_gb": peak_rss,
            "baseline_run_hash_reference": "d9d2311f2342a7b8 (pool=20, #24)",
        },
        "models": results,
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(f"written -> {args.out}")

    _print_memory_table(results, anchor)
    return payload


def _print_memory_table(results, anchor):
    print("\n=== memory quality + latency ===")
    header = f"{'key':20} {'license':14} {'r@1':>7} {'MRR':>7} {'nDCG@10':>8} {'miss':>5} {'ms/query':>9} {'p50':>8} {'p95':>8} {'pairs/s':>8}"
    print(header)
    for key, r in results.items():
        agg = r["aggregate"]
        lat = r["latency_ms"]
        print(f"{key:20} {r['license']:14} {agg['recall']['1']:>7.4f} {agg['mrr']:>7.4f} "
              f"{agg['ndcg@10']:>8.4f} {len(agg['misses']):>5} {lat['mean']:>9.1f} "
              f"{lat['p50']:>8.1f} {lat['p95']:>8.1f} {r['pairs_per_s']:>8.1f}")
    if anchor:
        print(f"\nanchor bge-v2-m3 run_hash={anchor['run_hash']} "
              f"(reference d9d2311f2342a7b8 = pool20/#24)")


# --------------------------------------------------------------- legal anchor

def cmd_legal(args):
    from config.config import ADAPTIVE_MAX, RELEVANCE_THRESHOLD

    with open(LEGAL_POOLS, "r", encoding="utf-8") as handle:
        cached = json.load(handle)
    queries = cached["queries"]
    if args.limit:
        queries = queries[:args.limit]

    models = select_models(args.models)
    monitor = PeakRSSMonitor()
    monitor.start()
    loaded = load_all(models, args.max_seq_length, local_files_only=not args.fetch)

    for m in loaded:
        for q in queries[:2]:
            rerank_pairs(m["encoder"], q["question"], [c["text"] for c in q["pool"]])

    latency = {m["key"]: {i: [] for i in range(len(queries))} for m in loaded}
    rankings = {m["key"]: {} for m in loaded}
    for round_index in range(args.rounds):
        for m in loaded:
            for i, q in enumerate(queries):
                texts = [c["text"] for c in q["pool"]]
                started = time.perf_counter()
                ranked = rerank_pairs(m["encoder"], q["question"], texts)
                latency[m["key"]][i].append((time.perf_counter() - started) * 1000.0)
                if round_index == args.rounds - 1:
                    rankings[m["key"]][i] = ranked
            print(f"round {round_index + 1}/{args.rounds} {m['key']} done", file=sys.stderr)

    peak_rss = monitor.stop()
    hmap = {q["question"]: {c["text"]: c["hash"] for c in q["pool"]} for q in queries}
    results = {}
    baseline_key = args.baseline
    baseline_ranks = None
    for m in loaded:
        rows = []
        for i, q in enumerate(queries):
            ranked = rankings[m["key"]][i]
            top = [hmap[q["question"]][text] for _, text in ranked[:ADAPTIVE_MAX]]
            scores = [score for score, _ in ranked]
            dists = [1.0 - s for s in scores]
            rows.append({
                "i": i, "qtype": q["qtype"], "pool": len(q["pool"]),
                "top_ids": top, "min_dist": round(min(dists), 4) if dists else None,
                "refuse": bool(dists and min(dists) > RELEVANCE_THRESHOLD),
            })
        all_times = [t for i in range(len(queries)) for t in latency[m["key"]][i]]
        pairs = sum(len(q["pool"]) for q in queries)
        entry = {
            "model": m["id"], "key": m["key"], "license": m["spec"]["license"],
            "role": m["spec"]["role"], "dtype": m["dtype"],
            "max_seq_length": m["max_seq_length"], "load_s": m["load_s"],
            "rss_delta_gb": m["rss_delta_gb"], "peak_rss_gb": peak_rss,
            "pairs": pairs,
            "latency_ms": _latency_summary(all_times),
            "pairs_per_s": round(pairs * args.rounds / (sum(all_times) / 1000.0), 2)
                            if all_times else None,
            "refuse_count": sum(1 for r in rows if r["refuse"]),
            "min_dist_mean": round(statistics.mean(
                [r["min_dist"] for r in rows if r["min_dist"] is not None]), 4),
            "rows": rows,
        }
        results[m["key"]] = entry
        if m["key"] == baseline_key:
            baseline_ranks = {r["i"]: r["top_ids"] for r in rows}

    if baseline_ranks:
        for key, entry in results.items():
            overlaps, taus = [], []
            for r in entry.pop("rows"):
                base = baseline_ranks[r["i"]]
                overlaps.append(len(set(base) & set(r["top_ids"])) / len(base) if base else 1.0)
                taus.append(_kendall_tau(base, r["top_ids"]))
            entry["topk_overlap_vs_baseline"] = round(statistics.mean(overlaps), 4)
            entry["kendall_tau_vs_baseline"] = round(statistics.mean(taus), 4)
    for key, entry in results.items():
        entry.pop("rows", None)

    payload = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "pools_meta": cached["meta"],
            "baseline": baseline_key,
            "relevance_threshold": RELEVANCE_THRESHOLD,
            "rounds": args.rounds, "max_seq_length": args.max_seq_length,
            "peak_rss_gb": peak_rss,
            "note": "legal 无金标：质量 = 相对 baseline 的 top-8 重合 / Kendall tau；"
                    "refuse/min_dist 跨模型阈值不可比，仅描述",
        },
        "models": results,
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(f"written -> {args.out}")

    _print_legal_table(results)
    return payload


def _print_legal_table(results):
    print("\n=== legal anchor (rank agreement vs baseline) ===")
    header = (f"{'key':20} {'license':14} {'overlap':>8} {'tau':>7} {'refuse':>7} "
              f"{'ms/query':>9} {'p50':>8} {'p95':>8} {'pairs/s':>8}")
    print(header)
    for key, r in results.items():
        lat = r["latency_ms"]
        print(f"{key:20} {r['license']:14} "
              f"{r.get('topk_overlap_vs_baseline', float('nan')):>8.4f} "
              f"{r.get('kendall_tau_vs_baseline', float('nan')):>7.4f} "
              f"{r['refuse_count']:>7} {lat['mean']:>9.1f} {lat['p50']:>8.1f} "
              f"{lat['p95']:>8.1f} {r['pairs_per_s']:>8.1f}")


# --------------------------------------------------- interleaved latency bench

def cmd_latency(args):
    """同场、交错、重复：只计时 rerank 调用（沿用 pool_latency_bench.md 的做法）。

    在缓存候选池的**子集**上跑（默认前 8 条），把选中的模型全部载入同一进程，
    逐轮交错抵消机器漂移。
    """
    cache_path = MEMORY_POOLS if args.corpus == "memory" else LEGAL_POOLS
    field = "query" if args.corpus == "memory" else "question"
    with open(cache_path, "r", encoding="utf-8") as handle:
        cached = json.load(handle)
    all_queries = cached["queries"]
    if args.indices:
        indices = [int(x) for x in args.indices.split(",") if x.strip()]
    else:
        indices = list(range(min(args.n_queries, len(all_queries))))
    subset = [all_queries[i] for i in indices]
    print(f"latency: corpus={args.corpus} queries={indices} rounds={args.rounds}",
          file=sys.stderr)

    models = select_models(args.models)
    monitor = PeakRSSMonitor()
    monitor.start()
    loaded = load_all(models, args.max_seq_length, local_files_only=not args.fetch)

    for m in loaded:
        q = subset[0]
        rerank_pairs(m["encoder"], q[field], [c["text"] for c in q["pool"]])

    times = {m["key"]: {i: [] for i in range(len(subset))} for m in loaded}
    for round_index in range(args.rounds):
        for m in loaded:
            for i, q in enumerate(subset):
                texts = [c["text"] for c in q["pool"]]
                started = time.perf_counter()
                rerank_pairs(m["encoder"], q[field], texts)
                times[m["key"]][i].append((time.perf_counter() - started) * 1000.0)
    peak_rss = monitor.stop()

    results = {}
    for m in loaded:
        all_times = [t for i in range(len(subset)) for t in times[m["key"]][i]]
        pairs = sum(len(q["pool"]) for q in subset)
        results[m["key"]] = {
            "model": m["id"], "key": m["key"], "license": m["spec"]["license"],
            "role": m["spec"]["role"], "dtype": m["dtype"],
            "max_seq_length": m["max_seq_length"], "load_s": m["load_s"],
            "rss_delta_gb": m["rss_delta_gb"], "peak_rss_gb": peak_rss,
            "pairs_per_call": round(pairs / len(subset), 2),
            "latency_ms": _latency_summary(all_times),
            "s_per_pair": round(sum(all_times) / 1000.0 / (pairs * args.rounds), 4),
        }
    payload = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "corpus": args.corpus, "indices": indices, "rounds": args.rounds,
            "max_seq_length": args.max_seq_length, "peak_rss_gb": peak_rss,
            "protocol": "same-field interleaved: round -> model -> query; only rerank call timed",
        },
        "models": results,
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(f"written -> {args.out}")

    print(f"\n=== interleaved latency ({args.corpus}, {len(subset)} queries x {args.rounds}) ===")
    print(f"{'key':20} {'license':14} {'mean':>9} {'p50':>8} {'p95':>8} {'sd':>7} "
          f"{'s/pair':>7} {'pairs/call':>10}")
    for key, r in results.items():
        lat = r["latency_ms"]
        print(f"{key:20} {r['license']:14} {lat['mean']:>9.1f} {lat['p50']:>8.1f} "
              f"{lat['p95']:>8.1f} {lat['sd']:>7.1f} {r['s_per_pair']:>7.3f} "
              f"{r['pairs_per_call']:>10.1f}")
    return payload


# --------------------------------------------------------------------- fetch

def cmd_fetch(args):
    models = select_models(args.models)
    for spec in models:
        print(f"fetch {spec['key']} ({spec['id']}) backend={spec.get('backend', 'torch')}",
              file=sys.stderr)
        encoder = build_encoder(spec, args.max_seq_length, local_files_only=False)
        print(f"  ok dtype={encoder_dtype(encoder)} "
              f"max_seq_length={encoder_max_seq_length(encoder)}", file=sys.stderr)
        del encoder
        gc.collect()
    print("fetch done")


# ---------------------------------------------------------------------- main

def main(argv=None):
    parser = argparse.ArgumentParser(description="#29 rerank model survey")
    parser.add_argument("command", choices=[
        "fetch", "memory-pools", "legal-pools", "memory", "legal", "latency"])
    parser.add_argument("--models", default=None, help="逗号分隔的 key；默认全部")
    parser.add_argument("--eval-set", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--max-seq-length", type=int, default=512)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--n", type=int, default=20, help="legal 取样问题数")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条（冒烟用）")
    parser.add_argument("--corpus", choices=["memory", "legal"], default="memory",
                        help="latency 子命令用哪个缓存池")
    parser.add_argument("--indices", default=None, help="latency 用的题目下标，逗号分隔")
    parser.add_argument("--n-queries", type=int, default=8,
                        help="latency 默认取样题目数（交错测量用）")
    parser.add_argument("--baseline", default="bge-v2-m3", help="legal 排名一致度的参照 key")
    parser.add_argument("--fetch", action="store_true",
                        help="允许联网下载（默认 local_files_only=True）")
    args = parser.parse_args(argv)

    if args.command == "fetch":
        return cmd_fetch(args)
    if args.command == "memory-pools":
        cmd_memory_pools(args)
    elif args.command == "legal-pools":
        cmd_legal_pools(args)
    elif args.command == "memory":
        cmd_memory(args)
    elif args.command == "legal":
        cmd_legal(args)
    elif args.command == "latency":
        cmd_latency(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
