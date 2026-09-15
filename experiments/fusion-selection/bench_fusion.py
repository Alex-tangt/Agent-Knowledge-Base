"""#30 融合对照 bench：默认链路（rerank 关）上比较若干召回融合方式。

对象 = **记忆检索**（#24 评测集）。**retriever-only，不载 reranker**。
一次跑把每个 query 的**原始两路召回**（向量池 + 关键词命中）抓下来，再**离线**套用
各融合函数 → 保证各变体面对的候选集合逐字相同，只差融合规则。

变体：
- `vector`：纯向量（对照；应复现 baseline 0.6407）
- `kw_first`：关键词优先（现状 `DefaultRetrievalStrategy` 语义，按文本去重；应复现 0.2500）
- `kw_first_entry`：同上但按条目 id 去重（去掉「跨仓库同文被折叠」的影响）
- `vec_first`：向量优先，关键词作池尾补充
- `rrf_k*`：倒数排名融合（Cormack 2009），关键词列表分别用全量 / cap
- `boost_b*`：`余弦 + beta * (matched/len(kw))` 加法增强（关键词只加分、不翻转）
- `weighted_a*`：`alpha*余弦 + (1-alpha)*(matched/len(kw))` 加权分数融合

池曲线：向量只搜一次（`--pool-max`），任意 pool=N 的向量列表 = 前 N 名截断
（top-N of top-M 性质），故一次运行即可给出所有 pool 的曲线。

用法（worktree 里用主树 venv 绝对路径；索引复用主树 gen-2）：
    set MEMORY_INDEX_DIR=D:\\...\\Agent-Knowledge-Base\\memory_agent\\vector_db
    <venv>\\python.exe experiments/fusion-selection/bench_fusion.py `
        --out experiments/fusion-selection/fusion_results.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from memory_agent import _bootstrap  # noqa: E402

_bootstrap.configure_stderr_logging()

from memory_agent.eval.metrics import evaluate  # noqa: E402
from memory_agent.eval.retrieval_eval import check_relevant_ids, load_eval_set  # noqa: E402
from memory_agent.memory.index import MemoryIndex  # noqa: E402
from ragcore.strategies.default import extract_keywords  # noqa: E402

DEFAULT_EVAL_SET = os.path.join(REPO_ROOT, "memory_agent", "eval", "retrieval_eval_set.json")
KEYWORD_CAP = 6
MAX_KEYWORDS = 12


# -------------------------------------------------------------------- retrieval

def fetch_raw(index: MemoryIndex, queries: list[dict], pool_max: int) -> list[dict]:
    """逐题抓原始两路召回：向量 top-N（N=pool_max）+ 关键词命中（全量、已排序）。

    只做**一次**嵌入/向量检索 + 一次关键词扫描，各融合变体全部离线套用。
    """
    store = index.store

    raw = []
    for i, query in enumerate(queries, start=1):
        q = query["query"]
        started = time.time()
        vec = store.search_documents(q, k=pool_max)
        vec_docs = vec["documents"][0] if vec.get("documents") else []
        vec_metas = vec["metadatas"][0] if vec.get("metadatas") else []
        vec_scores = vec["distances"][0] if vec.get("distances") else []

        keywords = extract_keywords(q, max_keywords=MAX_KEYWORDS)
        kw_hits = store.search_by_keywords(keywords) if keywords else []

        raw.append({
            "id": query["id"],
            "query": q,
            "relevant": list(query.get("relevant") or []),
            "keywords": keywords,
            "vector": [
                {
                    "id": (meta or {}).get("entry_id"),
                    "text": doc,
                    "score": float(score),
                }
                for doc, meta, score in zip(vec_docs, vec_metas, vec_scores)
            ],
            "keyword": [
                {
                    "id": (hit.get("metadata") or {}).get("entry_id"),
                    "text": hit.get("document", ""),
                    "matched": int(hit.get("matched", 0)),
                    "count": int(hit.get("score", 0)),
                }
                for hit in kw_hits
            ],
            "elapsed_s": round(time.time() - started, 3),
        })
        print(f"[{i}/{len(queries)}] {query['id']} {raw[-1]['elapsed_s']:.2f}s", file=sys.stderr)
    return raw


# ---------------------------------------------------------------------- fusions

def _dedup(items: list[dict], key) -> list[dict]:
    seen = set()
    out = []
    for item in items:
        value = key(item)
        if value in seen:
            continue
        seen.add(value)
        out.append(item)
    return out


def fuse_vector(rec: dict, pool: int, n_kw: list[str]) -> list[str]:
    return [it["id"] for it in rec["vector"][:pool]]


def fuse_kw_first(rec: dict, pool: int, kw_total: int, *, by_entry: bool) -> list[str]:
    """现状语义：关键词批次先入（分数>1），向量批次按去重键追加。"""
    kw_items = _dedup(rec["keyword"], key=lambda it: it["id"] if by_entry else it["text"])[:KEYWORD_CAP]
    key = (lambda it: it["id"]) if by_entry else (lambda it: it["text"])
    seen = {key(it) for it in kw_items}
    out = [it["id"] for it in kw_items]
    for it in rec["vector"][:pool]:
        k = key(it)
        if k in seen:
            continue
        seen.add(k)
        out.append(it["id"])
    return out


def fuse_vec_first(rec: dict, pool: int, kw_total: int) -> list[str]:
    vec_items = _dedup(rec["vector"][:pool], key=lambda it: it["id"])
    seen = {it["id"] for it in vec_items}
    out = [it["id"] for it in vec_items]
    for it in _dedup(rec["keyword"], key=lambda it: it["id"])[:KEYWORD_CAP]:
        if it["id"] not in seen:
            seen.add(it["id"])
            out.append(it["id"])
    return out


def fuse_rrf(rec: dict, pool: int, kw_total: int, *, k: int, kw_cap: int | None) -> list[str]:
    vec_ids = _unique([it["id"] for it in rec["vector"][:pool]])
    kw_ids = _unique([it["id"] for it in rec["keyword"]])
    if kw_cap is not None:
        kw_ids = kw_ids[:kw_cap]
    scores: dict[str, float] = defaultdict(float)
    for lists in (vec_ids, kw_ids):
        for rank, doc in enumerate(lists):
            scores[doc] += 1.0 / (k + rank + 1)
    return [doc for doc, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]


def fuse_boost(rec: dict, pool: int, kw_total: int, *, beta: float) -> list[str]:
    """余弦 + beta * (matched/len(kw))：关键词只加分，不翻转余弦序（beta 有界）。"""
    kw_norm = {}
    for it in rec["keyword"]:
        kw_norm[it["id"]] = max(kw_norm.get(it["id"], 0.0), it["matched"] / max(1, kw_total))
    vec_items = _dedup(rec["vector"][:pool], key=lambda it: it["id"])
    scores: dict[str, float] = {}
    for it in vec_items:
        scores[it["id"]] = it["score"] + beta * kw_norm.get(it["id"], 0.0)
    for it in _dedup(rec["keyword"], key=lambda it: it["id"])[:KEYWORD_CAP]:
        if it["id"] not in scores:
            scores[it["id"]] = beta * kw_norm.get(it["id"], 0.0)
    return [doc for doc, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]


def fuse_weighted(rec: dict, pool: int, kw_total: int, *, alpha: float) -> list[str]:
    """alpha * 余弦 + (1-alpha) * (matched/len(kw))（各自用原始尺度）。"""
    kw_norm = {}
    for it in rec["keyword"]:
        kw_norm[it["id"]] = max(kw_norm.get(it["id"], 0.0), it["matched"] / max(1, kw_total))
    vec_items = _dedup(rec["vector"][:pool], key=lambda it: it["id"])
    scores: dict[str, float] = {}
    for it in vec_items:
        scores[it["id"]] = alpha * it["score"]
    for it in _dedup(rec["keyword"], key=lambda it: it["id"])[:KEYWORD_CAP]:
        scores[it["id"]] = scores.get(it["id"], 0.0) + (1 - alpha) * kw_norm.get(it["id"], 0.0)
    return [doc for doc, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]


def _unique(items):
    seen = set()
    out = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


ALPHAS = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0)
BETAS = (0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.1, 0.15, 0.2, 0.3, 0.5)
RRF_KS = (1, 10, 60)
RRF_KW_CAPS = (None, 20, 6)


def build_variants(rec: dict, pool: int) -> dict[str, list[str]]:
    kw_total = len(rec["keywords"]) or MAX_KEYWORDS
    variants = {
        "vector": fuse_vector(rec, pool, None),
        "kw_first": fuse_kw_first(rec, pool, kw_total, by_entry=False),
        "kw_first_entry": fuse_kw_first(rec, pool, kw_total, by_entry=True),
        "vec_first": fuse_vec_first(rec, pool, kw_total),
    }
    for k in RRF_KS:
        for cap in RRF_KW_CAPS:
            tag = "all" if cap is None else f"cap{cap}"
            variants[f"rrf_k{k}_{tag}"] = fuse_rrf(rec, pool, kw_total, k=k, kw_cap=cap)
    for beta in BETAS:
        variants[f"boost_b{beta}"] = fuse_boost(rec, pool, kw_total, beta=beta)
    for alpha in ALPHAS:
        variants[f"weighted_a{alpha}"] = fuse_weighted(rec, pool, kw_total, alpha=alpha)
    return variants


# ---------------------------------------------------------------------- reporting

def evaluate_variant(records: list[dict], ranked_by_query: dict[str, list[str]],
                     ks=(1, 3, 5, 10), ndcg_k: int = 10) -> dict:
    rows = []
    for rec in records:
        rows.append({
            "id": rec["id"],
            "query": rec["query"],
            "relevant": rec["relevant"],
            "ranked": ranked_by_query[rec["id"]],
        })
    report = evaluate(rows, ks=ks, ndcg_k=ndcg_k)
    agg = report["aggregate"]
    return {
        "recall": agg["recall"],
        "recall_1": agg["recall"]["1"],
        "ndcg@10": agg["ndcg@10"],
        "mrr": agg["mrr"],
        "misses": len(agg["misses"]),
        "miss_ids": agg["misses"],
        "per_query": {p["id"]: {"recall_1": p["recall"]["1"], "mrr": p["mrr"],
                                "ndcg@10": p["ndcg@10"], "first_hit_rank": p["first_hit_rank"]}
                      for p in report["per_query"]},
    }


def fmt(value) -> str:
    return f"{value:.4f}" if isinstance(value, float) else str(value)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="#30 fusion comparison (retriever-only)")
    parser.add_argument("--eval-set", default=DEFAULT_EVAL_SET)
    parser.add_argument("--pool-max", type=int, default=40)
    parser.add_argument("--pool", type=int, default=None,
                        help="只评估该池大小；默认对 pool-curve 列表逐个评估")
    parser.add_argument("--pool-curve", type=int, nargs="+", default=[8, 10, 12, 14, 16, 20, 40])
    parser.add_argument("--out", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--raw", default=None, help="把原始召回写到该 JSON（便于离线复用）")
    args = parser.parse_args(argv)

    eval_set = load_eval_set(args.eval_set)
    queries = eval_set["queries"]
    if args.limit:
        queries = queries[:args.limit]

    index = MemoryIndex()
    known = set(index.known_ids())
    missing = check_relevant_ids(queries, known)
    if missing:
        raise SystemExit(f"评测集有 {len(missing)} 个相关条目不在索引 gen={index.gen}：{missing[:10]}")

    try:
        index.store.warmup()
    except Exception as exc:
        print(f"warmup 跳过：{exc}", file=sys.stderr)
    index.search("warmup", k=1)

    started = time.time()
    raw = fetch_raw(index, queries, pool_max=args.pool_max)
    retrieval_s = round(time.time() - started, 2)

    if args.raw:
        with open(args.raw, "w", encoding="utf-8") as handle:
            json.dump({"pool_max": args.pool_max, "records": raw}, handle,
                      ensure_ascii=False, indent=2)

    answerable = [r for r in raw if r["relevant"]]
    pools = [args.pool] if args.pool else args.pool_curve

    results: dict[str, dict] = {}
    for pool in pools:
        table = {}
        for rec in answerable:
            for name, ranked in build_variants(rec, pool).items():
                table.setdefault(name, {})[rec["id"]] = ranked
        for name, ranked_by_query in table.items():
            results[f"pool{pool}/{name}"] = evaluate_variant(answerable, ranked_by_query)

    # 一致性校验：pool=20 时离线 vector / kw_first 应复现 baseline（#24）
    # 注：kw_first 的 MRR 用 0.4936（全排名）而非历史 0.4917——历史 ablation 只存 top-10，
    # q042 的首个 gold 在 rank 12 被截断成 miss（见 pool_results.md），本 bench 存全排名。
    BASELINE_REF = {
        "vector": {"recall_1": 0.6407, "ndcg@10": 0.8524, "mrr": 0.8136},
        "kw_first": {"recall_1": 0.2500, "ndcg@10": 0.5739, "mrr": 0.4936},
    }
    mismatch = {}
    if "pool20/vector" in results:
        for name, ref in BASELINE_REF.items():
            got = results[f"pool20/{name}"]
            match = all(abs(got[key] - value) <= 0.0005 for key, value in ref.items())
            mismatch[name] = {
                "match": match,
                "got": {key: got[key] for key in ref},
                "ref": ref,
            }

    # 汇总打印：pool 表中的每个变体，仅行 = 变体
    lines = []
    lines.append("# #30 融合对照（retriever-only，rerank 关）")
    lines.append("")
    lines.append(f"- 索引 gen={index.gen}，{len(known)} 条；评测集 {len(queries)} 题"
                 f"（有答案 {len(answerable)}）；pool_max={args.pool_max}；"
                 f"关键抽取 max={MAX_KEYWORDS}，关键词批次 cap={KEYWORD_CAP}")
    if mismatch:
        summary = "，".join(f"{n}:{'OK' if v['match'] else 'MISMATCH'}"
                            for n, v in mismatch.items())
        lines.append(f"- baseline 复现（pool=20）：{summary}")
    lines.append(f"- 召回阶段耗时 {retrieval_s}s（不含模型加载）")
    lines.append("")

    for pool in pools:
        lines.append(f"## pool={pool}")
        lines.append("")
        lines.append("| 变体 | recall@1 | recall@3 | recall@5 | recall@10 | nDCG@10 | MRR | misses |")
        lines.append("|---|---|---|---|---|---|---|---|")
        names = [k.split("/", 1)[1] for k in results if k.startswith(f"pool{pool}/")]
        ordered = sorted(names, key=lambda n: (
            -results[f"pool{pool}/{n}"]["recall_1"],
            -results[f"pool{pool}/{n}"]["mrr"],
        ))
        for name in ordered:
            res = results[f"pool{pool}/{name}"]
            lines.append(
                f"| {name} | {fmt(res['recall_1'])} | {fmt(res['recall']['3'])} | "
                f"{fmt(res['recall']['5'])} | {fmt(res['recall']['10'])} | "
                f"{fmt(res['ndcg@10'])} | {fmt(res['mrr'])} | {res['misses']} |")
        lines.append("")

    report_md = "\n".join(lines)
    print(report_md)

    if args.out:
        payload = {
            "meta": {
                "gen": index.gen,
                "indexed_entries": len(known),
                "queries_total": len(queries),
                "answerable": len(answerable),
                "pool_max": args.pool_max,
                "pools": pools,
                "keyword_cap": KEYWORD_CAP,
                "max_keywords": MAX_KEYWORDS,
                "retrieval_s": retrieval_s,
                "baseline_check": mismatch,
                "run_hash": hashlib.sha256(
                    json.dumps(results, sort_keys=True, ensure_ascii=False).encode("utf-8")
                ).hexdigest()[:16],
            },
            "results": results,
        }
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        md_path = os.path.splitext(args.out)[0] + ".md"
        with open(md_path, "w", encoding="utf-8") as handle:
            handle.write(report_md + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
