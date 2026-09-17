"""在 `scores.npz` 上做融合消融（纯 numpy，秒级）。

臂 = 官方公式（含官方权重） + 单路对照。官方权重见 `README.md` §2.2。
排名用**全部 134 条** → `recall@14/20` 即"池上限"（ceiling）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from memory_agent.eval.metrics import evaluate  # noqa: E402

KS = (1, 3, 5, 10, 14, 20)

# 官方权重：模型卡示例 vs 源码缺省
OFFICIAL = (0.4, 0.2, 0.4)
DEFAULT = (1.0, 1.0, 1.0)

ARMS = [
    ("A0 dense-only", ("dense",), (1.0,), None),
    ("A1 sparse-only", ("sparse",), (1.0,), None),
    ("A2 colbert-only (128)", ("colbert128",), (1.0,), None),
    ("A2 colbert-only (512)", ("colbert512",), (1.0,), None),
    ("A2 colbert-only (full)", ("colbertfull",), (1.0,), None),
    ("A3 OFFICIAL raw .4/.2/.4 (colbertfull)", ("dense", "sparse", "colbertfull"), OFFICIAL, None),
    ("A4 DEFAULT raw 1:1:1 (colbertfull)", ("dense", "sparse", "colbertfull"), DEFAULT, None),
    ("A5 raw dense.4+sparse.2", ("dense", "sparse"), (0.4, 0.2), None),
    # --- 归一化 / 无标度融合（官方配方在本语料量纲失衡，见 README §2.3） ---
    ("B1 minmax OFFICIAL .4/.2/.4 (full)", ("dense", "sparse", "colbertfull"), OFFICIAL, "minmax"),
    ("B2 zscore OFFICIAL .4/.2/.4 (full)", ("dense", "sparse", "colbertfull"), OFFICIAL, "zscore"),
    ("B3 RRF(60) OFFICIAL .4/.2/.4 (full)", ("dense", "sparse", "colbertfull"), OFFICIAL, "rrf"),
    ("B4 raw dense+colbert .5/.5", ("dense", "colbertfull"), (0.5, 0.5), None),
    ("B5 minmax dense+colbert .5/.5", ("dense", "colbertfull"), (0.5, 0.5), "minmax"),
    ("B6 zscore dense+colbert .5/.5", ("dense", "colbertfull"), (0.5, 0.5), "zscore"),
    ("B7 minmax dense+sparse .5/.5", ("dense", "sparse"), (0.5, 0.5), "minmax"),
    ("B8 RRF(60) dense+sparse .5/.5", ("dense", "sparse"), (0.5, 0.5), "rrf"),
    # --- 生产同源 ST dense（与 0.7074 基线同口径） ---
    ("C0 ST-dense only（锚点，应=0.6407）", ("dense_st",), (1.0,), None),
    ("C1 ST-dense + colbertfull raw .5/.5", ("dense_st", "colbertfull"), (0.5, 0.5), None),
    ("C2 ST-dense + colbertfull minmax .5/.5", ("dense_st", "colbertfull"), (0.5, 0.5), "minmax"),
    ("C3 ST-dense + colbertfull zscore .5/.5", ("dense_st", "colbertfull"), (0.5, 0.5), "zscore"),
    ("C4 minmax ST-dense.4/sparse.2/colbert.4", ("dense_st", "sparse", "colbertfull"), OFFICIAL, "minmax"),
    ("C5 minmax ST-dense+sparse .5/.5", ("dense_st", "sparse"), (0.5, 0.5), "minmax"),
    ("C6 minmax ST-dense+colbert512 .5/.5", ("dense_st", "colbert512"), (0.5, 0.5), "minmax"),
]

RRF_K = 60


def _normalize(mat: np.ndarray, mode: str) -> np.ndarray:
    if mode == "minmax":
        lo = mat.min(axis=1, keepdims=True)
        hi = mat.max(axis=1, keepdims=True)
        return (mat - lo) / np.where(hi - lo == 0, 1.0, hi - lo)
    if mode == "zscore":
        mean = mat.mean(axis=1, keepdims=True)
        std = mat.std(axis=1, keepdims=True)
        return (mat - mean) / np.where(std == 0, 1.0, std)
    raise ValueError(mode)


def _rrf(mat: np.ndarray, doc_ids: list[str]) -> np.ndarray:
    out = np.zeros_like(mat)
    for i in range(mat.shape[0]):
        order = sorted(range(len(doc_ids)), key=lambda j: (-float(mat[i, j]), doc_ids[j]))
        for rank, j in enumerate(order, start=1):
            out[i, j] = 1.0 / (RRF_K + rank)
    return out


def _rank(scores: np.ndarray, doc_ids: list[str]) -> list[str]:
    order = sorted(range(len(doc_ids)), key=lambda j: (-float(scores[j]), doc_ids[j]))
    return [doc_ids[j] for j in order]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="BGE-M3 表示融合消融")
    parser.add_argument("--scores", required=True)
    parser.add_argument("--dense-st", default=None, help="索引 ST dense 矩阵（dense_st.npz）")
    parser.add_argument("--eval-set", default="memory_agent/eval/retrieval_eval_set.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--table", default=None)
    args = parser.parse_args(argv)

    data = dict(np.load(args.scores, allow_pickle=True))
    if args.dense_st:
        data.update({"dense_st": np.load(args.dense_st, allow_pickle=True)["dense_st"]})
    doc_ids = [str(x) for x in data["doc_ids"]]
    qids = [str(x) for x in data["qids"]]
    with open(args.eval_set, encoding="utf-8") as fh:
        queries = {q["id"]: q for q in json.load(fh)["queries"]}

    results = {}
    for label, keys, weights, norm in ARMS:
        combined = np.zeros_like(data[keys[0]], dtype=np.float32)
        for key, weight in zip(keys, weights):
            mat = np.asarray(data[key], dtype=np.float32)
            if norm == "rrf":
                mat = _rrf(mat, doc_ids)
            elif norm:
                mat = _normalize(mat, norm)
            combined += weight * mat
        combined /= sum(weights)
        records = []
        for i, qid in enumerate(qids):
            q = queries[qid]
            records.append({"id": qid, "query": q["query"],
                            "relevant": q.get("relevant") or [],
                            "ranked": _rank(combined[i], doc_ids)})
        scored = evaluate(records, ks=KS, ndcg_k=10)
        ranks = [p["first_hit_rank"] for p in scored["per_query"]]
        scored["aggregate"]["gold_rank"] = {
            f"<={k}": sum(1 for r in ranks if r is not None and r <= k) for k in KS
        }
        results[label] = {"aggregate": scored["aggregate"], "per_query": scored["per_query"]}

    header = f"{'arm':52} " + " ".join(f"r@{k:<2}" for k in KS) + "   nDCG@10   MRR    miss"
    lines = [header]
    for label, res in results.items():
        agg = res["aggregate"]
        row = " ".join(f"{agg['recall'][str(k)]:.3f}" for k in KS)
        lines.append(f"{label:52} {row}   {agg['ndcg@10']:.4f}   {agg['mrr']:.4f}  {len(agg['misses'])}")
    lines.append("")
    lines.append("gold 命中位次分布（45 题，题数）：")
    for label, res in results.items():
        g = res["aggregate"]["gold_rank"]
        lines.append(f"  {label:52} " + "  ".join(f"top{k}={v}" for k, v in g.items()))
    lines.append("")
    lines.append("参照（生产默认：dense + 手写关键词加法，gen-2/池14）："
                 "r@1 0.7074  r@3 0.8685  r@5 0.9185  r@10 0.9741  nDCG@10 0.8817  MRR 0.8731  miss 0")
    text = "\n".join(lines)
    print(text)

    # 落盘裁剪：去掉 per_query 里的完整 ranked（134 条 × 51 题 × 22 臂 ≈ 10MB），留指标 + 命中位次
    trimmed = {}
    for label, res in results.items():
        per_q = [{k: v for k, v in p.items() if k != "ranked"} for p in res["per_query"]]
        trimmed[label] = {"aggregate": res["aggregate"], "per_query": per_q}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(trimmed, fh, ensure_ascii=False, indent=2)
    if args.table:
        with open(args.table, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(f"\nwritten -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
