"""构建 **BM25 分数矩阵**（51×134）——走生产同路径：fastembed 编码 + Qdrant `modifier=Idf`。

为什么不自己算 IDF：Qdrant 的 `modifier=Idf` 用集合统计施加 IDF（`ln((N−n+0.5)/(n+0.5)+1)`），
文档侧存 BM25 的 tf 饱和权重、查询侧用 query 权重 —— 这条路径就是 #40 的生产 BM25 语义。
这里建一个**临时 sparse-only 集合**，把 134 条渲染正文灌进去，再对 51 个 query 取全量分数。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from docs_source import load_texts, resolve_gen  # noqa: E402

COLLECTION = "bm25_probe"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="BM25 分数矩阵（Qdrant IDF 口径）")
    parser.add_argument("--index-dir", required=True)
    parser.add_argument("--eval-set", default="memory_agent/eval/retrieval_eval_set.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--gen", default=None)
    parser.add_argument("--model", default="Qdrant/bm25")
    args = parser.parse_args(argv)

    gen = resolve_gen(args.index_dir, args.gen)
    doc_ids, texts = load_texts(args.index_dir, gen, "qdrant")
    with open(args.eval_set, encoding="utf-8") as fh:
        queries = json.load(fh)["queries"]
    print(f"[bm25] gen={gen} docs={len(doc_ids)} queries={len(queries)}", file=sys.stderr, flush=True)

    from memory_agent.memory.bm25 import Bm25Encoder
    from qdrant_client import QdrantClient, models

    encoder = Bm25Encoder(args.model)
    workdir = tempfile.mkdtemp(prefix="bm25probe-")
    try:
        client = QdrantClient(path=workdir)
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config={},
            sparse_vectors_config={
                "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
        points = []
        for i, (entry_id, text) in enumerate(zip(doc_ids, texts)):
            idx, val = encoder.encode_document(text)
            points.append(models.PointStruct(
                id=i, vector={"sparse": models.SparseVector(indices=idx, values=val)},
                payload={"entry_id": entry_id}))
        client.upsert(collection_name=COLLECTION, points=points, wait=True)
        print(f"[bm25] upserted {len(points)} docs", file=sys.stderr, flush=True)

        index = {d: j for j, d in enumerate(doc_ids)}
        matrix = np.zeros((len(queries), len(doc_ids)), dtype=np.float32)
        for i, q in enumerate(queries):
            idx, val = encoder.encode_query(q["query"])
            res = client.query_points(
                collection_name=COLLECTION,
                query=models.SparseVector(indices=idx, values=val),
                using="sparse", limit=len(doc_ids), with_payload=True).points
            for p in res:
                entry_id = (p.payload or {}).get("entry_id")
                if entry_id in index:
                    matrix[i, index[entry_id]] = float(p.score)
            print(f"[bm25] {q['id']} nonzero={int((matrix[i] > 0).sum())}", file=sys.stderr, flush=True)
        client.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    np.savez_compressed(args.out, bm25=matrix, doc_ids=np.array(doc_ids),
                        qids=np.array([q["id"] for q in queries]))
    stats = {"model": args.model, "docs": len(doc_ids), "queries": len(queries),
             "nonzero_mean": float((matrix > 0).sum(axis=1).mean()),
             "score_mean": float(matrix.mean()), "score_max": float(matrix.max())}
    with open(os.path.splitext(args.out)[0] + ".stats.json", "w", encoding="utf-8") as fh:
        json.dump(stats, fh, ensure_ascii=False, indent=2)
    print(f"written -> {args.out}  {json.dumps(stats, ensure_ascii=False)}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
