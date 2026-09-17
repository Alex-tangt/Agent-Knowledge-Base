"""信号消融：**四路（+sparse）到底需不需要？** 以及每路的增量成本。

只读已缓存资产 + `fit_results.json`（不重跑模型）：
- 对 3 路（colbert±+dense+kw）与 4 路（+sparse）各取最优，比较 L2/L3，并做 paired bootstrap；
- 报每路的**增量成本**（存储 / 索引期 / 查询期）。
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fit_fusion import (KS, PROD, distill_ndcg, normalize, qrels, rank_ids,  # noqa: E402
                        spearman_mean)

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")


def load(cache: str):
    dense = np.load(os.path.join(cache, "dense_st.npz"), allow_pickle=True)
    doc_ids = [str(x) for x in dense["doc_ids"]]
    qids = [str(x) for x in dense["qids"]]
    sc = np.load(os.path.join(cache, "scores_qd.npz"), allow_pickle=True)
    raw = {"dense_st": dense["dense_st"].astype(np.float32),
           "sparse": sc["sparse"].astype(np.float32),
           "colbert512": sc["colbert512"].astype(np.float32),
           "colbertfull": sc["colbertfull"].astype(np.float32),
           "kw": np.load(os.path.join(cache, "kw.npz"), allow_pickle=True)["kw"].astype(np.float32)}
    R = np.load(os.path.join(cache, "rerank_scores.npz"), allow_pickle=True)["scores"].astype(np.float32)
    return raw, R, doc_ids, qids


def grid_best(raw, R, doc_ids, keys, norm, step=0.05):
    rhat = normalize(R, "minmax", doc_ids)
    mats = {k: normalize(raw[k], norm, doc_ids) for k in keys}
    ticks = int(round(1.0 / step))
    best = None
    for combo in itertools.product(range(1, ticks + 1), repeat=len(keys)):
        if sum(combo) != ticks:
            continue
        w = np.array(combo, dtype=np.float64) / ticks
        F = np.zeros_like(R)
        for k, wv in zip(keys, w):
            F += wv * mats[k]
        val = float(np.mean(distill_ndcg(F, rhat, 5)))
        if best is None or val > best[0]:
            best = (val, w, F)
    return best


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="信号消融 + 增量成本")
    parser.add_argument("--cache", default=CACHE)
    parser.add_argument("--eval-set", default="memory_agent/eval/retrieval_eval_set.json")
    parser.add_argument("--bootstrap", type=int, default=2000)
    args = parser.parse_args(argv)

    raw, R, doc_ids, qids = load(args.cache)
    with open(args.eval_set, encoding="utf-8") as fh:
        queries = {q["id"]: q for q in json.load(fh)["queries"]}

    groups = {
        "3 路 colbertfull+dense+kw": ("colbertfull", "dense_st", "kw"),
        "3 路 colbert512+dense+kw": ("colbert512", "dense_st", "kw"),
        "4 路 +sparse（full）": ("colbertfull", "dense_st", "kw", "sparse"),
        "4 路 +sparse（512）": ("colbert512", "dense_st", "kw", "sparse"),
        "2 路 colbertfull+dense": ("colbertfull", "dense_st"),
        "生产 dense+0.05kw（锚点）": ("dense_st",),
    }
    results = {}
    for name, keys in groups.items():
        for norm in ("zscore", "minmax", "none"):
            val, w, F = grid_best(raw, R, doc_ids, keys, norm)
            qm = qrels(F, doc_ids, qids, queries)
            key = f"{name} / {norm}"
            results[key] = {"distill5": val, "w": [round(float(x), 3) for x in w],
                            "spearman": round(spearman_mean(F, R), 4), **qm, "_F": F}
    best3 = max([(k, v) for k, v in results.items() if k.startswith("3 路")], key=lambda kv: kv[1]["distill5"])
    best4 = max([(k, v) for k, v in results.items() if k.startswith("4 路")], key=lambda kv: kv[1]["distill5"])

    # paired bootstrap：4 路 vs 3 路
    rng = np.random.default_rng(20260916)

    def r1(F):
        out = []
        for i, q in enumerate(qids):
            rel = set(queries[q].get("relevant") or [])
            out.append(1.0 if rel and rank_ids(F[i], doc_ids)[0] in rel else 0.0)
        return np.array(out)

    idx = rng.integers(0, len(qids), size=(args.bootstrap, len(qids)))
    d34 = r1(best4[1]["_F"])[idx].mean(1) - r1(best3[1]["_F"])[idx].mean(1)
    d3p = r1(best3[1]["_F"])[idx].mean(1) - r1(results["生产 dense+0.05kw（锚点） / zscore"]["_F"])[idx].mean(1)

    lines = [f"{'配置':44} {'distill5':8} {'Spearman':8} {'w':28} r@1    r@3    r@5    nDCG   MRR"]
    for k, v in sorted(results.items(), key=lambda kv: -kv[1]["distill5"]):
        lines.append(f"{k:44} {v['distill5']:.4f}  {v['spearman']:.4f}  {str(v['w']):28} "
                     f"{v['recall@1']:.4f} {v['recall@3']:.4f} {v['recall@5']:.4f} "
                     f"{v['nDCG@10']:.4f} {v['mrr']:.4f}")
    lines += ["",
              f"最优 3 路: {best3[0]}  r@1 {best3[1]['recall@1']:.4f}  nDCG {best3[1]['nDCG@10']:.4f}",
              f"最优 4 路: {best4[0]}  r@1 {best4[1]['recall@1']:.4f}  nDCG {best4[1]['nDCG@10']:.4f}",
              f"Δ(4−3) r@1 的 95% CI = [{np.percentile(d34, 2.5):+.4f}, {np.percentile(d34, 97.5):+.4f}]",
              f"对比：最优 3 路 − 生产 r@1 的 95% CI = [{np.percentile(d3p, 2.5):+.4f}, {np.percentile(d3p, 97.5):+.4f}]"]

    # ---- 增量成本（实测口径）----
    stats = json.load(open(os.path.join(args.cache, "scores_qd.stats.json"), encoding="utf-8"))
    tok = np.load(os.path.join(args.cache, "scores_qd.npz"), allow_pickle=True)["d_tokens"]
    n = len(tok)
    col_full = int(tok.sum() * 1024 * 4)
    col_512 = int(np.minimum(tok, 512).sum() * 1024 * 4)
    col_128 = int(np.minimum(tok, 128).sum() * 1024 * 4)
    sparse_nz = stats["sparse_nonzero_mean"]
    lines += ["", "增量成本（134 条语料实测；括号 = 折算每条）:",
              "  dense   : 索引已有（1024d 余弦）→ **增量 0**（该路已在生产）",
              "  kw      : 无模型 / 无向量，纯 payload 子串扫描（生产已在用）→ **增量 0**",
              f"  sparse  : 非零均值 {sparse_nz:.0f} 项 → **{n * sparse_nz * 8 / 1e6:.2f}MB**"
              f"（{sparse_nz * 8 / 1024:.1f}KB/条）；索引期与 dense 共用同一次前向 → 增量可忽略",
              f"  colbert : 每条 token 均值 {tok.mean():.0f} / max {tok.max()} × 1024d × 4B →",
              f"            全量 **{col_full / 1e6:.0f}MB**（{col_full / n / 1e6:.1f}MB/条）"
              f"｜512 截断 **{col_512 / 1e6:.0f}MB**（{col_512 / n / 1e6:.1f}MB/条）"
              f"｜128 截断 {col_128 / 1e6:.0f}MB",
              "            查询期：1 次前向（廉价）+ MaxSim（候选池 14 时 ~毫秒）"]
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(os.path.dirname(CACHE), "ablate_signals.md"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print("\nwritten -> experiments/bge-m3-sparse-colbert/ablate_signals.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
