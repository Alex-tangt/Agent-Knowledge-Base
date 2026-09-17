"""编码 gen-2 语料 + #24 题集，产出三套表示的**打分矩阵**（不落表示本身，省 ~2GB）。

- dense  : 内积（FlagEmbedding dense 已 L2 归一 → 余弦）
- sparse : `Σ_{token∈q∩d} q_w·d_w`（官方 compute_lexical_matching_score 语义）
- colbert: MaxSim 按 query token 数取平均（官方 colbert_score 语义），**前缀 128 / 512 / full**

一次前向出三头 → 之后的融合消融全部在 `scores.npz`（几 KB）上秒级重算。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np


def _log(msg: str) -> None:
    print(f"[encode] {msg}", file=sys.stderr, flush=True)


def _resolve_gen(index_dir: str, gen: str | None) -> str:
    if gen:
        return gen
    with open(os.path.join(index_dir, "CURRENT"), encoding="utf-8") as fh:
        return fh.read().strip()


def _load_docs(index_dir: str, gen: str) -> tuple[list[str], list[str]]:
    manifest_path = os.path.join(index_dir, gen, "manifest.json")
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    ids, texts = [], []
    for entry_id, meta in manifest["entries"].items():
        path = meta.get("path")
        if not path or not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            texts.append(fh.read())
        ids.append(entry_id)
    return ids, texts


def _load_queries(eval_set: str) -> list[dict]:
    with open(eval_set, encoding="utf-8") as fh:
        return json.load(fh)["queries"]


def _sparse_scores(q_weights, d_weights) -> np.ndarray:
    out = np.zeros((len(q_weights), len(d_weights)), dtype=np.float32)
    for i, qw in enumerate(q_weights):
        for j, dw in enumerate(d_weights):
            if len(dw) < len(qw):
                score = sum(w * dw[t] for t, w in qw.items() if t in dw)
            else:
                score = sum(dw[t] * w for t, w in qw.items() if t in dw)
            out[i, j] = score
    return out


def _colbert_scores(q_vecs, d_vecs, prefix: int | None) -> np.ndarray:
    out = np.zeros((len(q_vecs), len(d_vecs)), dtype=np.float32)
    for i, qv in enumerate(q_vecs):
        q = np.asarray(qv, dtype=np.float32)
        for j, dv in enumerate(d_vecs):
            d = np.asarray(dv, dtype=np.float32)
            if prefix is not None:
                d = d[:prefix]
            sim = q @ d.T                      # (q_tok, d_tok)
            out[i, j] = float(sim.max(axis=1).sum() / q.shape[0])   # MaxSim 按 query token 平均
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="BGE-M3 三表示打分矩阵")
    parser.add_argument("--index-dir", required=True)
    parser.add_argument("--eval-set", default="memory_agent/eval/retrieval_eval_set.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--gen", default=None)
    parser.add_argument("--max-length", type=int, default=8192)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--prefixes", default="128,512", help="colbert 前缀截断（full 总会算）")
    args = parser.parse_args(argv)

    gen = _resolve_gen(args.index_dir, args.gen)
    doc_ids, doc_texts = _load_docs(args.index_dir, gen)
    queries = _load_queries(args.eval_set)
    _log(f"gen={gen} docs={len(doc_ids)} queries={len(queries)}")

    from FlagEmbedding import BGEM3FlagModel

    t0 = time.time()
    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False)
    _log(f"model loaded in {time.time() - t0:.1f}s")

    def encode(texts: list[str]) -> dict:
        return model.encode(texts, batch_size=args.batch_size, max_length=args.max_length,
                            return_dense=True, return_sparse=True, return_colbert_vecs=True)

    t0 = time.time()
    d_out = encode(doc_texts)
    _log(f"docs encoded in {time.time() - t0:.1f}s")
    t0 = time.time()
    q_out = encode([q["query"] for q in queries])
    _log(f"queries encoded in {time.time() - t0:.1f}s")

    d_dense = np.asarray(d_out["dense_vecs"], dtype=np.float32)
    q_dense = np.asarray(q_out["dense_vecs"], dtype=np.float32)
    dense = q_dense @ d_dense.T
    _log(f"dense matrix {dense.shape}")

    t0 = time.time()
    sparse = _sparse_scores(q_out["lexical_weights"], d_out["lexical_weights"])
    _log(f"sparse matrix done in {time.time() - t0:.1f}s")

    matrices = {"dense": dense, "sparse": sparse}
    prefixes = [int(p) for p in args.prefixes.split(",") if p.strip()]
    for prefix in prefixes:
        t0 = time.time()
        matrices[f"colbert{prefix}"] = _colbert_scores(q_out["colbert_vecs"], d_out["colbert_vecs"], prefix)
        _log(f"colbert{prefix} done in {time.time() - t0:.1f}s")
    t0 = time.time()
    matrices["colbertfull"] = _colbert_scores(q_out["colbert_vecs"], d_out["colbert_vecs"], None)
    _log(f"colbertfull done in {time.time() - t0:.1f}s")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    np.savez_compressed(args.out, doc_ids=np.array(doc_ids),
                        qids=np.array([q["id"] for q in queries]),
                        d_tokens=np.array([len(v) for v in d_out["colbert_vecs"]]),
                        q_tokens=np.array([len(v) for v in q_out["colbert_vecs"]]),
                        **matrices)
    _log(f"saved -> {args.out}")

    stats = {
        "gen": gen, "docs": len(doc_ids), "queries": len(queries), "max_length": args.max_length,
        "d_tokens_mean": float(np.mean([len(v) for v in d_out["colbert_vecs"]])),
        "d_tokens_max": int(np.max([len(v) for v in d_out["colbert_vecs"]])),
        "d_tokens_over_512": int(np.sum([len(v) > 512 for v in d_out["colbert_vecs"]])),
        "d_tokens_over_8192": int(np.sum([len(v) > 8192 for v in d_out["colbert_vecs"]])),
        "sparse_nonzero_mean": float(np.mean([len(w) for w in d_out["lexical_weights"]])),
        "colbert_bytes_full": int(sum(len(v) * 1024 * 4 for v in d_out["colbert_vecs"])),
        "colbert_bytes_128": int(sum(min(len(v), 128) * 1024 * 4 for v in d_out["colbert_vecs"])),
    }
    stats_path = os.path.splitext(args.out)[0] + ".stats.json"
    with open(stats_path, "w", encoding="utf-8") as fh:
        json.dump(stats, fh, ensure_ascii=False, indent=2)
    _log(f"stats -> {json.dumps(stats, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
