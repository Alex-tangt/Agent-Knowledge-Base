"""录制 **rerank 分数矩阵** R（51×134，全量）——蒸馏方案的监督信号（一次性资产）。

- 打分器：jina-reranker-v2 int8 ONNX（`OnnxReranker`，零新依赖；owner 约定"实验用新模型"）。
- 送排正文：`doc[:MEMORY_RERANK_MAX_CHARS]`（与生产一致）。
- 去重：同 query 下**截断后文本相同**的条目只算一次，再按文本回填（分数是文本的函数，合法）。
- **逐 query checkpoint**（`<out>.partial.npz`）——崩了不丢已算的。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

JINA = "jinaai/jina-reranker-v2-base-multilingual"


def _log(msg: str) -> None:
    print(f"[rerank] {msg}", file=sys.stderr, flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="录制 rerank 全量分数矩阵")
    parser.add_argument("--index-dir", required=True)
    parser.add_argument("--eval-set", default="memory_agent/eval/retrieval_eval_set.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--gen", default=None)
    parser.add_argument("--model", default=JINA)
    parser.add_argument("--onnx-file", default="onnx/model_int8.onnx")
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--max-chars", type=int, default=512)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题（冒烟用）")
    parser.add_argument("--text-source", default="qdrant", choices=["qdrant", "file"],
                        help="正文来源（默认 qdrant = 生产一致）")
    args = parser.parse_args(argv)

    gen = args.gen
    if not gen:
        with open(os.path.join(args.index_dir, "CURRENT"), encoding="utf-8") as fh:
            gen = fh.read().strip()
    from docs_source import load_texts

    doc_ids, full_texts = load_texts(args.index_dir, gen, args.text_source)
    texts = [t[:args.max_chars] for t in full_texts]
    _frontmatter = sum(1 for t in full_texts if t.lstrip().startswith("---"))
    assert _frontmatter == 0, f"{_frontmatter} 条正文仍是原始 frontmatter → 文本来源不对"
    with open(args.eval_set, encoding="utf-8") as fh:
        queries = json.load(fh)["queries"]
    if args.limit:
        queries = queries[:args.limit]
    _log(f"gen={gen} docs={len(doc_ids)} queries={len(queries)} max_chars={args.max_chars}")

    from memory_agent.memory.onnx_reranker import OnnxReranker

    reranker = OnnxReranker(args.model, onnx_file=args.onnx_file, max_length=args.max_length,
                            batch_size=args.batch_size, allow_download=False)
    _log(f"reranker ready: {args.model} / {args.onnx_file}")

    scores = np.full((len(queries), len(doc_ids)), np.nan, dtype=np.float32)
    partial = args.out + ".partial.npz"
    t_start = time.time()
    unique_saved = 0
    for i, q in enumerate(queries):
        t0 = time.time()
        unique: dict[str, list[int]] = {}
        for j, text in enumerate(texts):
            unique.setdefault(text, []).append(j)
        unique_saved += len(texts) - len(unique)
        ranked = reranker.rerank(q["query"], list(unique.keys()), top_k=len(unique))
        by_text = {text: score for score, text in ranked}
        for text, idxs in unique.items():
            value = by_text.get(text)
            if value is None:
                continue
            for j in idxs:
                scores[i, j] = value
        np.savez_compressed(partial, scores=scores, doc_ids=np.array(doc_ids),
                            qids=np.array([x["id"] for x in queries]))
        _log(f"{q['id']} {i + 1}/{len(queries)}  pairs={len(unique)}  {time.time() - t0:.1f}s  "
             f"elapsed={(time.time() - t_start) / 60:.1f}min")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    np.savez_compressed(args.out, scores=scores, doc_ids=np.array(doc_ids),
                        qids=np.array([x["id"] for x in queries]))
    meta = {"model": args.model, "onnx_file": args.onnx_file, "max_length": args.max_length,
            "max_chars": args.max_chars, "queries": len(queries), "docs": len(doc_ids),
            "scored": int(np.isfinite(scores).sum()), "dedup_saved": unique_saved,
            "seconds": round(time.time() - t_start, 1)}
    with open(os.path.splitext(args.out)[0] + ".meta.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    if os.path.exists(partial):
        os.remove(partial)
    _log(f"saved -> {args.out}  {json.dumps(meta, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
