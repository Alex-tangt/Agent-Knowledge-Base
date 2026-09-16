"""#33 共享平面原生检索消融：dense / RRF / DBSF（同一集合、同一评测集）。

回答"store 原生 hybrid 在该语料上是不是最佳"：三种原生通道都在**服务端**完成（dense 单路、
prefetch 双路 + RRF、prefetch 双路 + DBSF），不在 Python 里融合。分数量纲按平面（D6）。
用法：
    $env:MEMORY_STORE_URL = "http://127.0.0.1:6333"
    <repo>/venv/Scripts/python.exe experiments/networked-store-33/fusion_ablation.py \
        --collection memory_eval_33 --out fusion_ablation.json
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
from memory_agent.memory.store import QdrantNetworkStore  # noqa: E402
from memory_agent.settings import load_env_file  # noqa: E402

MODES = ("dense", "rrf", "dbsf")


def _search(store: QdrantNetworkStore, mode: str, query: str, k: int) -> dict:
    if mode == "dense":
        return store.search_dense(query, k=k)
    return store.search_hybrid(query, k=k, fusion=mode)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("MEMORY_STORE_URL", "http://127.0.0.1:6333"))
    ap.add_argument("--collection", default="memory_eval_33")
    ap.add_argument("--eval-set", default=os.path.join(
        REPO_ROOT, "memory_agent", "eval", "retrieval_eval_set.json"))
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--out", default=os.path.join(HERE, "fusion_ablation.json"))
    args = ap.parse_args(argv)

    load_env_file()
    with open(args.eval_set, "r", encoding="utf-8") as handle:
        queries = json.load(handle)["queries"]
    store = QdrantNetworkStore(url=args.url, collection_name=args.collection)
    store.warmup()

    results = {}
    for mode in MODES:
        records = []
        for q in queries:
            started = time.perf_counter()
            hits = _search(store, mode, q["query"], k=args.top_k)
            elapsed = time.perf_counter() - started
            metas = hits["metadatas"][0] if hits.get("metadatas") else []
            records.append({
                "id": q["id"], "query": q["query"], "relevant": list(q.get("relevant") or []),
                "ranked": [m.get("entry_id") for m in metas],
                "ranked_scores": list(hits["distances"][0]) if hits.get("distances") else [],
                "elapsed_s": round(elapsed, 4),
            })
        report = evaluate(records, ks=(1, 3, 5, 10), ndcg_k=10)
        agg = report["aggregate"]
        results[mode] = {
            "recall@1": agg["recall"]["1"], "recall@5": agg["recall"]["5"],
            "nDCG@10": agg["ndcg@10"], "mrr": agg["mrr"],
            "misses": len(agg["misses"]),
        }
        print(f"{mode:6s} recall@1={agg['recall']['1']:.4f} recall@5={agg['recall']['5']:.4f} "
              f"nDCG@10={agg['ndcg@10']:.4f} MRR={agg['mrr']:.4f} miss={len(agg['misses'])}")

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump({"collection": args.collection, "url": args.url,
                   "top_k": args.top_k, "results": results}, handle,
                  ensure_ascii=False, indent=2)
    store.close()
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
