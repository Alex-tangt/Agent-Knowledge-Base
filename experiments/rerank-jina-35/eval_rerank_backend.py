"""#35：在**固定显式索引**上对照 reranker 后端（m3-torch vs jina-onnx），不调 LLM。

为什么要显式索引模式：指针模式（生产）在 `manifest` 缺指纹时会触发 `_maybe_refresh`
（把运行时语料扫描结果灌进当前代）——对照实验要**冻结同一批 134 条候选**，故用
`MemoryIndex(store=..., manifest_path=...)`（`_explicit=True`，跳过刷新）。

环境（脚本不自带 .env）：
- `MEMORY_RERANK_BACKEND` = torch | onnx（reranker 后端，`--no-rerank` 时忽略）
- `MEMORY_RERANK_MODEL`    = 模型名（默认 m3 / 传 jina）
用法：
    python eval_rerank_backend.py --qdrant-dir <snap>/gen-2/qdrant \
        --manifest <snap>/gen-2/manifest.json --no-rerank --out retriever_only.json
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

from memory_agent import _bootstrap  # noqa: E402

_bootstrap.configure_stderr_logging()

from memory_agent.eval.metrics import evaluate  # noqa: E402
from memory_agent.eval.retrieval_eval import (  # noqa: E402
    DEFAULT_EVAL_SET,
    latency_stats,
    load_eval_set,
    print_summary,
    run,
    run_hash,
)
from memory_agent.memory.index import MemoryIndex  # noqa: E402
from memory_agent.memory.retrieval import MemoryRetriever, default_reranker_factory  # noqa: E402
from memory_agent.memory.store import open_store  # noqa: E402
from ragcore.strategies.default import DefaultRetrievalStrategy  # noqa: E402


def build_index(args) -> MemoryIndex:
    store = open_store(db_path=args.qdrant_dir, hybrid=False)

    def factory(store):
        reranker = None if args.no_rerank else default_reranker_factory()
        return MemoryRetriever(
            store,
            strategy=DefaultRetrievalStrategy(enable_keyword=True),
            reranker=reranker,
        )

    return MemoryIndex(store=store, manifest_path=args.manifest, retriever_factory=factory)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="#35 reranker backend comparison")
    parser.add_argument("--qdrant-dir", required=True, help="显式索引的 qdrant 目录")
    parser.add_argument("--manifest", required=True, help="显式索引的 manifest.json")
    parser.add_argument("--eval-set", default=DEFAULT_EVAL_SET)
    parser.add_argument("--out", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-rerank", action="store_true", help="只用 fused 召回（不重排）")
    parser.add_argument("--k", type=int, nargs="+", default=[1, 3, 5, 10])
    parser.add_argument("--ndcg-k", type=int, default=10)
    args = parser.parse_args(argv)

    eval_set = load_eval_set(args.eval_set)
    queries = eval_set["queries"]
    if args.limit is not None:
        queries = queries[:args.limit]
    top_k = max(max(args.k), args.ndcg_k)

    index = build_index(args)
    known = set(index.known_ids())
    missing = sorted({e for q in queries for e in (q.get("relevant") or [])
                      if e not in known})
    if missing:
        raise SystemExit(f"评测集相关条目不在索引里（{len(missing)} 个）：{missing[:10]}")

    index.search("warmup", k=1)
    started = time.time()
    records = run(index, queries, top_k=top_k, trace_path=None)
    report = evaluate(records, ks=tuple(args.k), ndcg_k=args.ndcg_k)

    from memory_agent.settings import RERANK_BACKEND, RERANK_MODEL
    meta = {
        "rerank": not args.no_rerank,
        "backend": None if args.no_rerank else RERANK_BACKEND,
        "model": None if args.no_rerank else RERANK_MODEL,
        "index_gen": index.gen or "explicit",
        "indexed_entries": len(known),
        "eval_set": os.path.relpath(os.path.abspath(args.eval_set), REPO_ROOT),
        "elapsed_s": round(time.time() - started, 2),
        "latency_s": latency_stats(records),
    }
    meta["run_hash"] = run_hash(report)
    full = {"meta": meta, "aggregate": report["aggregate"],
            "per_query": report["per_query"], "no_answer": report["no_answer"]}
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(full, handle, ensure_ascii=False, indent=2)
    print_summary(report, {**meta, "mode": meta["backend"] or "fused-only"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
