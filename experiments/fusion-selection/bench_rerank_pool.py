"""#30 池曲线（新融合 · rerank 链）：一次重排，离析各 pool。

`ADR-0022` 的池默认 14 是**旧（关键词优先）融合**上的结论，已标 provisional。
本脚本在**新融合（加法关键词增强，#30）**上重跑「池大小 vs 质量」。

省算力的关键：**候选集对 pool 单调包含**——pool 越小，候选越少，且都含在大 pool 里；
交叉编码器对 (query, doc) **独立打分**，与池大小无关。故只对「大池并集」**真重排一次**，
把 (query, 截断文本)→分数缓存，再对每个 pool 用**生产代码路径**
（`MemoryRetriever` + `DefaultRetrievalStrategy`）重放排序。小 pool 的重排调用全部命中缓存。

用法（worktree 里用主树 venv 绝对路径；需 BGE-M3 + bge-reranker，CPU ~15 分钟）：
    set MEMORY_INDEX_DIR=...\\Agent-Knowledge-Base\\memory_agent\\vector_db
    <venv>\\python.exe experiments/fusion-selection/bench_rerank_pool.py `
        --out experiments/fusion-selection/rerank_pool_curve.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

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
from memory_agent.memory.retrieval import MemoryRetriever, default_reranker_factory  # noqa: E402
from ragcore.strategies.default import DefaultRetrievalStrategy  # noqa: E402

DEFAULT_EVAL_SET = os.path.join(REPO_ROOT, "memory_agent", "eval", "retrieval_eval_set.json")


class CachingReranker:
    """按 (query, 截断文本) 缓存重排分数——池越小候选越少，重复调用全部命中缓存。"""

    def __init__(self, inner):
        self.inner = inner
        self.cache: dict[tuple[str, str], float] = {}
        self.pairs = 0
        self.seconds = 0.0

    def rerank(self, query, documents, top_k=5):
        missing = [doc for doc in documents if (query, doc) not in self.cache]
        if missing:
            started = time.time()
            for score, text in self.inner.rerank(query, missing, top_k=len(missing)):
                self.cache[(query, text)] = float(score)
            self.seconds += time.time() - started
            self.pairs += len(missing)
        ranked = [(self.cache[(query, doc)], doc)
                  for doc in documents if (query, doc) in self.cache]
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked[:top_k]


def run_pool(store, queries, reranker, pool: int, k: int) -> list[dict]:
    retriever = MemoryRetriever(
        store, strategy=DefaultRetrievalStrategy(enable_keyword=True),
        reranker=reranker, pool_size=pool)
    records = []
    for query in queries:
        hits = retriever.retrieve(query["query"], k=k)
        records.append({
            "id": query["id"],
            "query": query["query"],
            "relevant": list(query.get("relevant") or []),
            "ranked": [(meta or {}).get("entry_id") for _, _, meta in hits],
        })
    return records


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="#30 rerank pool curve on new fusion")
    parser.add_argument("--eval-set", default=DEFAULT_EVAL_SET)
    parser.add_argument("--pools", type=int, nargs="+", default=[8, 10, 12, 14, 16, 20])
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    eval_set = load_eval_set(args.eval_set)
    queries = eval_set["queries"]

    index = MemoryIndex()
    known = set(index.known_ids())
    missing = check_relevant_ids(queries, known)
    if missing:
        raise SystemExit(f"评测集有 {len(missing)} 个相关条目不在索引 gen={index.gen}：{missing[:10]}")

    index.store.warmup()
    index.search("warmup", k=1)
    reranker = CachingReranker(default_reranker_factory())

    # 先跑最大池：把并集重排一次，后续小池全命中缓存。
    pools = sorted(set(args.pools), reverse=True)
    results = {}
    for pool in pools:
        started = time.time()
        records = run_pool(index.store, queries, reranker, pool, args.k)
        report = evaluate(records, ks=(1, 3, 5, 10), ndcg_k=10)
        agg = report["aggregate"]
        results[f"pool{pool}"] = {
            "recall": agg["recall"],
            "recall_1": agg["recall"]["1"],
            "ndcg@10": agg["ndcg@10"],
            "mrr": agg["mrr"],
            "misses": agg["misses"],
            "elapsed_s": round(time.time() - started, 2),
            "per_query": {p["id"]: {"ranked": p["ranked"], "recall_1": p["recall"]["1"]}
                          for p in report["per_query"]},
        }
        print(f"pool={pool:>3}  recall@1={agg['recall']['1']:.4f}  "
              f"nDCG@10={agg['ndcg@10']:.4f}  MRR={agg['mrr']:.4f}  "
              f"misses={len(agg['misses'])}  ({time.time() - started:.1f}s)", file=sys.stderr)

    meta = {
        "gen": index.gen,
        "queries_total": len(queries),
        "answerable": sum(1 for q in queries if q.get("relevant")),
        "pools": pools,
        "k": args.k,
        "rerank_pairs": reranker.pairs,
        "rerank_seconds": round(reranker.seconds, 1),
        "seconds_per_pair": round(reranker.seconds / reranker.pairs, 3) if reranker.pairs else None,
    }
    lines = ["# #30 新融合 · rerank 池曲线（一次重排 + 缓存离析）", ""]
    lines.append(f"- 索引 gen={meta['gen']}；{meta['answerable']} 有答案；k={args.k}")
    lines.append(f"- 真实重排 pairs={meta['rerank_pairs']}，耗时 {meta['rerank_seconds']}s"
                 f"（s/pair={meta['seconds_per_pair']}）")
    lines.append("")
    lines.append("| pool | recall@1 | recall@3 | recall@5 | recall@10 | nDCG@10 | MRR | misses |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for pool in sorted(results):
        res = results[pool]
        lines.append(
            f"| {pool.replace('pool', '')} | {res['recall_1']:.4f} | "
            f"{res['recall']['3']:.4f} | {res['recall']['5']:.4f} | "
            f"{res['recall']['10']:.4f} | {res['ndcg@10']:.4f} | {res['mrr']:.4f} | "
            f"{len(res['misses'])} |")
    lines.append("")
    report_md = "\n".join(lines)
    print(report_md)

    if args.out:
        payload = {"meta": meta, "results": results}
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        with open(os.path.splitext(args.out)[0] + ".md", "w", encoding="utf-8") as handle:
            handle.write(report_md + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
