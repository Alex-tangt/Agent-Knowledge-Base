"""#28 A/B：reranker 序列长度上限（token 级）。

两个子命令，都在**本机 CPU**上跑、都不调 LLM：

- `memory-census`：证明 token 上限对**记忆检索**是否 binding。复刻 #24 目标链路的
  候选池（策略召回 + `MEMORY_RERANK_MAX_CHARS` 字符截断），用 reranker 的 tokenizer
  统计每个 query→doc 对的 token 长度，与各上限比较。
- `legal-anchor`：在**法律链路的真实候选池**上逐档测 rerank 延迟 + 分数标定
  （`RELEVANCE_THRESHOLD=0.85` 判分漂移）+ 排名漂移（top-8 重合 / Kendall tau）。

环境（脚本不自带 .env，需外部喂；见 results 文件复现段）：
- `MEMORY_INDEX_DIR` / `MEMORY_READONLY_REPOS_CONFIG`：memory-census 需要
- `VECTOR_DB_PATH`：legal-anchor 需要（指向 legal_web/vector_db）

用法：
    venv\\Scripts\\python.exe experiments/rerank-latency-survey/bench_rerank_maxlen.py memory-census
    venv\\Scripts\\python.exe experiments/rerank-latency-survey/bench_rerank_maxlen.py legal-anchor
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
RAGCORE = os.path.join(REPO_ROOT, "ragcore")
if RAGCORE not in sys.path:
    sys.path.insert(0, RAGCORE)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

CAPS = (8192, 1024, 512, 256)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def _pct(values, pct):
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * pct), len(ordered) - 1)]


def _latency_summary(values):
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 3),
        "p50": round(_pct(values, 0.50), 3),
        "p95": round(_pct(values, 0.95), 3),
        "max": round(max(values), 3),
    }


def _kendall_tau(a, b):
    """两个等长排名（文档 id 列表）的 Kendall tau（宽松版，平局按 0.5）。"""
    pos_b = {doc: i for i, doc in enumerate(b)}
    conc = disc = 0
    n = len(a)
    for i in range(n):
        for j in range(i + 1, n):
            if a[i] not in pos_b or a[j] not in pos_b:
                continue
            if pos_b[a[i]] < pos_b[a[j]]:
                conc += 1
            elif pos_b[a[i]] > pos_b[a[j]]:
                disc += 1
    total = conc + disc
    return round((conc - disc) / total, 4) if total else 1.0


# --------------------------------------------------------------- memory census

def memory_census() -> dict:
    import statistics as _st  # noqa: F401

    from memory_agent.eval.retrieval_eval import (
        build_retriever_factory, load_eval_set, DEFAULT_EVAL_SET,
    )
    from memory_agent.memory.index import MemoryIndex
    from memory_agent.settings import RERANK_MODEL
    from transformers import AutoTokenizer

    index = MemoryIndex(retriever_factory=build_retriever_factory("hybrid-rerank"))
    retriever = index.retriever
    strategy = retriever.strategy
    max_chars = retriever.rerank_max_chars

    tokenizer = AutoTokenizer.from_pretrained(RERANK_MODEL, local_files_only=True)
    eval_set = load_eval_set(DEFAULT_EVAL_SET)

    rows = []
    over = {cap: 0 for cap in CAPS}
    total_pairs = 0
    max_len = 0
    for query in eval_set["queries"]:
        result = strategy.retrieve(
            query["query"], index.store, pool_size=retriever.pool_size)
        docs = result["documents"][0] if result.get("documents") else []
        unique = {}
        for doc in docs:
            unique.setdefault(doc[:max_chars], doc)
        lens = [len(tokenizer(query["query"], doc)["input_ids"]) for doc in unique]
        total_pairs += len(lens)
        if lens:
            max_len = max(max_len, max(lens))
        for cap in CAPS:
            over[cap] += sum(1 for L in lens if L > cap)
        rows.append({
            "id": query["id"],
            "pool": len(unique),
            "max_pair_tokens": max(lens) if lens else 0,
            "mean_pair_tokens": round(statistics.mean(lens), 1) if lens else 0,
        })

    ab_caps = (8192, 1024, 512)
    result = {
        "rerank_model": RERANK_MODEL,
        "rerank_max_chars": max_chars,
        "queries": len(rows),
        "pairs": total_pairs,
        "max_pair_tokens": max_len,
        "pairs_over_cap": over,
        "ab_caps": list(ab_caps),
        "ab_caps_binding": any(over[c] for c in ab_caps),
        "per_query": rows,
    }
    print(json.dumps({k: v for k, v in result.items() if k != "per_query"},
                     ensure_ascii=False, indent=2))
    if not result["ab_caps_binding"]:
        print(f"结论：A/B 三档 {ab_caps} 全部 no-op——{total_pairs} 个 query→doc 对的 token "
              f"长度都 <= {min(ab_caps)}（max={max_len}），真正 binding 的是 "
              f"{max_chars} 字符截断。")
    return result


# ---------------------------------------------------------------- legal anchor

def _load_questions(limit=50):
    path = os.path.join(REPO_ROOT, "legal_web", "tests", "questions.md")
    out = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            m = re.match(r"^-\s*\[([^\]]+)\]\s*(.+)$", line)
            if m:
                out.append((m.group(1).strip(), m.group(2).strip()))
    return out[:limit]


def _sample(rows, n):
    if len(rows) <= n:
        return rows
    step = len(rows) / n
    return [rows[int(i * step)] for i in range(n)]


def legal_anchor(n_queries: int = 20) -> dict:
    from config.config import ADAPTIVE_POOL, ADAPTIVE_MAX, RELEVANCE_THRESHOLD
    from services.vector_store_service import VectorStoreService
    from services.reranker_service import RerankerService
    from strategies.legal import LegalRetrievalStrategy

    vs = VectorStoreService(collection_name="documents")
    strategy = LegalRetrievalStrategy()
    questions = _sample(_load_questions(), n_queries)
    print(f"legal anchor: {len(questions)} queries, pool={ADAPTIVE_POOL}, "
          f"threshold={RELEVANCE_THRESHOLD}")

    pools = []
    for qtype, question in questions:
        res = strategy.retrieve(question, vs, pool_size=ADAPTIVE_POOL)
        docs = res["documents"][0] if res.get("documents") else []
        pools.append({"qtype": qtype, "question": question, "docs": docs})

    per_cap = {}
    for cap in CAPS:
        service = RerankerService(max_seq_length=cap)
        rows = []
        for item in pools:
            question, docs = item["question"], item["docs"]
            timestamp = time.time()
            ranked = service.rerank(question, docs, top_k=len(docs)) if docs else []
            elapsed = time.time() - timestamp
            dists = [round(1.0 - float(score), 4) for score, _ in ranked]
            rows.append({
                "id": item["question"][:24],
                "qtype": item["qtype"],
                "pool": len(docs),
                "elapsed_s": round(elapsed, 3),
                "min_dist": min(dists) if dists else None,
                "top_dist": dists[0] if dists else None,
                "refuse": bool(dists and min(dists) > RELEVANCE_THRESHOLD),
                "top_ids": [ranked[i][1] for i in range(min(ADAPTIVE_MAX, len(ranked)))],
            })
        per_cap[cap] = {
            "latency": _latency_summary([r["elapsed_s"] for r in rows]),
            "refuse_count": sum(1 for r in rows if r["refuse"]),
            "min_dist_mean": round(statistics.mean(
                [r["min_dist"] for r in rows if r["min_dist"] is not None]), 4),
            "top_dist_mean": round(statistics.mean(
                [r["top_dist"] for r in rows if r["top_dist"] is not None]), 4),
            "rows": rows,
        }

    base = per_cap[CAPS[0]]
    for cap in CAPS[1:]:
        rows = per_cap[cap]["rows"]
        overlaps = []
        taus = []
        for r_base, r_cap in zip(base["rows"], rows):
            set_b = set(r_base["top_ids"])
            set_c = set(r_cap["top_ids"])
            overlaps.append(len(set_b & set_c) / len(set_b) if set_b else 1.0)
            taus.append(_kendall_tau(r_base["top_ids"], r_cap["top_ids"]))
        per_cap[cap]["topk_overlap_vs_%d" % CAPS[0]] = round(statistics.mean(overlaps), 4)
        per_cap[cap]["kendall_tau_vs_%d" % CAPS[0]] = round(statistics.mean(taus), 4)
        del per_cap[cap]["rows"]
    del base["rows"]

    result = {
        "queries": len(questions),
        "pool_size": ADAPTIVE_POOL,
        "relevance_threshold": RELEVANCE_THRESHOLD,
        "caps": list(CAPS),
        "per_cap": {str(c): per_cap[c] for c in CAPS},
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="#28 rerank max_seq_length A/B")
    parser.add_argument("command", choices=["memory-census", "legal-anchor"])
    parser.add_argument("--n", type=int, default=20, help="legal-anchor 取样问题数")
    parser.add_argument("--out", default=None, help="把结果 JSON 写到该路径")
    args = parser.parse_args()

    result = memory_census() if args.command == "memory-census" else legal_anchor(args.n)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
        print(f"written -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
