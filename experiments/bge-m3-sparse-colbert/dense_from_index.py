"""用**索引里那份 ST dense**（生产同源）算 (51,134) 余弦矩阵，供与 colbert/sparse 同口径融合。

目的：消除「FlagEmbedding dense vs 生产 ST dense」的实现差异，让融合对照与生产基线可比。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from memory_agent import settings  # noqa: E402
from ragcore.services.vector_store_service import VectorStoreService  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="索引 ST dense 打分矩阵")
    parser.add_argument("--index-dir", required=True)
    parser.add_argument("--eval-set", default="memory_agent/eval/retrieval_eval_set.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--gen", default=None)
    args = parser.parse_args(argv)

    gen = args.gen
    if not gen:
        with open(os.path.join(args.index_dir, "CURRENT"), encoding="utf-8") as fh:
            gen = fh.read().strip()
    db_path = os.path.join(args.index_dir, gen, "qdrant")

    with open(os.path.join(args.index_dir, gen, "manifest.json"), encoding="utf-8") as fh:
        doc_ids = list(json.load(fh)["entries"].keys())
    with open(args.eval_set, encoding="utf-8") as fh:
        queries = json.load(fh)["queries"]

    service = VectorStoreService(collection_name=settings.COLLECTION_NAME, db_path=db_path)
    index = {doc_id: j for j, doc_id in enumerate(doc_ids)}
    matrix = np.full((len(queries), len(doc_ids)), -1.0, dtype=np.float32)
    for i, q in enumerate(queries):
        res = service.search_dense_documents(q["query"], k=len(doc_ids) + 10)
        for meta, score in zip(res["metadatas"][0], res["distances"][0]):
            entry_id = (meta or {}).get("entry_id")
            if entry_id in index:
                matrix[i, index[entry_id]] = float(score)
        print(f"[dense] {q['id']} filled {int((matrix[i] > -1).sum())}/{len(doc_ids)}", file=sys.stderr, flush=True)
    service.close()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    np.savez_compressed(args.out, doc_ids=np.array(doc_ids),
                        qids=np.array([q["id"] for q in queries]), dense_st=matrix)
    print(f"written -> {args.out}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
