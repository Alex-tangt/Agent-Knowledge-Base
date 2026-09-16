"""#40：把 gen-2 的**纯 dense** 集合迁成 **dense + sparse** hybrid 集合（免重嵌）。

为什么能免重嵌：新集合只需要「同名 dense + sparse」两路；dense 向量可从旧集合
`scroll(with_vectors=True)` 原样取回，**不用再跑 BGE-M3**。只有 sparse 是新算的
（tfidf 零依赖 / bm25 fastembed）——这把「换词法编码器」这一变量的成本压到分钟级。

用法：
    python build_hybrid.py --src <gen-2>/qdrant --out <dir> --manifest-src <gen-2>/manifest.json \
        --sparse-backend bm25
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from qdrant_client import QdrantClient  # noqa: E402
from qdrant_client.models import (  # noqa: E402
    Distance,
    Modifier,
    PointStruct,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from memory_agent.memory.store import _resolve_sparse_encoders  # noqa: E402
from memory_agent.settings import COLLECTION_NAME, SPARSE_BM25_MODEL  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="#40 build hybrid collection from dense")
    parser.add_argument("--src", required=True, help="源（纯 dense）qdrant 目录")
    parser.add_argument("--out", required=True, help="目标（hybrid）qdrant 目录")
    parser.add_argument("--manifest-src", required=True, help="源 manifest.json")
    parser.add_argument("--collection", default=COLLECTION_NAME)
    parser.add_argument("--sparse-backend", choices=["tfidf", "bm25"], default="tfidf")
    args = parser.parse_args(argv)

    doc_encoder, _ = _resolve_sparse_encoders(args.sparse_backend, SPARSE_BM25_MODEL, None)

    source = QdrantClient(path=args.src)
    records, _ = source.scroll(
        collection_name=args.collection, limit=10000, with_payload=True, with_vectors=True)
    if not records:
        raise SystemExit(f"源集合为空：{args.src}")
    dim = len(records[0].vector)
    print(f"scroll {len(records)} points, dense dim={dim}, sparse={args.sparse_backend}",
          file=sys.stderr)

    if os.path.isdir(args.out):
        shutil.rmtree(args.out)
    os.makedirs(args.out, exist_ok=True)
    target = QdrantClient(path=args.out)
    target.create_collection(
        collection_name=args.collection,
        vectors_config={"dense": VectorParams(size=dim, distance=Distance.COSINE)},
        sparse_vectors_config={"sparse": SparseVectorParams(modifier=Modifier.IDF)},
    )

    points = []
    for record in records:
        text = (record.payload or {}).get("text", "")
        indices, values = doc_encoder(text)
        points.append(PointStruct(
            id=record.id,
            vector={"dense": record.vector,
                    "sparse": SparseVector(indices=indices, values=values)},
            payload=record.payload,
        ))
    target.upsert(collection_name=args.collection, points=points)
    count = target.get_collection(args.collection).points_count
    target.close()
    source.close()
    print(f"built hybrid collection with {count} points -> {args.out}")

    manifest_out = os.path.join(os.path.dirname(args.out), "manifest.json")
    shutil.copyfile(args.manifest_src, manifest_out)
    print(f"manifest copied -> {manifest_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
