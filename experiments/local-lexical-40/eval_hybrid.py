"""#40：在**固定 hybrid 集合**上对照本地词法路（store 原生 hybrid / dense 单路）。

`MemoryIndex(store=..., manifest_path=...)` = 显式模式（跳过 `_maybe_refresh`），
故指数固定、不触发重嵌；`native_hybrid=True` 时 `MemoryRetriever` 退化为薄封装，
检索由 store 提供（ADR-0019 D4），融合方式由 `--fusion` 选。

用法：
    python eval_hybrid.py --store-dir <dir> --manifest <dir>/manifest.json \
        --sparse-backend bm25 --fusion rrf --out bm25_rrf.json
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
from memory_agent.memory.retrieval import MemoryRetriever  # noqa: E402
from memory_agent.memory.store import open_store  # noqa: E402

FUSIONS = ("rrf", "dbsf", "dense")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="#40 local lexical comparison")
    parser.add_argument("--store-dir", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--sparse-backend", choices=["tfidf", "bm25"], default="tfidf")
    parser.add_argument("--fusion", choices=FUSIONS, default="rrf")
    parser.add_argument("--eval-set", default=DEFAULT_EVAL_SET)
    parser.add_argument("--out", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--k", type=int, nargs="+", default=[1, 3, 5, 10])
    parser.add_argument("--ndcg-k", type=int, default=10)
    args = parser.parse_args(argv)

    eval_set = load_eval_set(args.eval_set)
    queries = eval_set["queries"]
    if args.limit is not None:
        queries = queries[:args.limit]
    top_k = max(max(args.k), args.ndcg_k)

    store = open_store(db_path=args.store_dir, hybrid=True, fusion=args.fusion,
                       sparse_backend=args.sparse_backend)
    index = MemoryIndex(store=store, manifest_path=args.manifest,
                        retriever_factory=lambda s: MemoryRetriever(s))
    known = set(index.known_ids())
    missing = sorted({e for q in queries for e in (q.get("relevant") or [])
                      if e not in known})
    if missing:
        raise SystemExit(f"评测集相关条目不在索引里（{len(missing)} 个）：{missing[:10]}")

    index.search("warmup", k=1)
    started = time.time()
    records = run(index, queries, top_k=top_k, trace_path=None)
    report = evaluate(records, ks=tuple(args.k), ndcg_k=args.ndcg_k)

    meta = {
        "sparse_backend": args.sparse_backend,
        "fusion": args.fusion,
        "native_hybrid": True,
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
    print_summary(report, {**meta, "mode": f"{args.sparse_backend}/{args.fusion}",
                           "index_gen": "explicit"})
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
