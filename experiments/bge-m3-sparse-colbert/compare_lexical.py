"""三条**词法路**选一条：手写关键词 `kw` vs **BM25** vs BGE-M3 **原生 sparse**。

生态位相同 → 融合里**只放一条**：`dense + L`（L ∈ {kw, bm25, sparse}）。
因为量纲差异，**归一化是候选维度**（none / minmax / zscore / rrf）。
目标函数沿用蒸馏口径（对全量 rerank 分数做 top-5 蒸馏 nDCG），qrels 只作 L3 验证 + bootstrap。
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

from fit_fusion import PROD, distill_ndcg, normalize, qrels, rank_ids, spearman_mean  # noqa: E402

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
LEXICAL = ("kw", "bm25", "sparse")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="词法路三方对照")
    parser.add_argument("--cache", default=CACHE)
    parser.add_argument("--eval-set", default="memory_agent/eval/retrieval_eval_set.json")
    parser.add_argument("--out", default=os.path.join(os.path.dirname(CACHE), "compare_lexical.json"))
    parser.add_argument("--table", default=os.path.join(os.path.dirname(CACHE), "compare_lexical.md"))
    parser.add_argument("--step", type=float, default=0.05)
    parser.add_argument("--bootstrap", type=int, default=2000)
    args = parser.parse_args(argv)

    dense = np.load(os.path.join(args.cache, "dense_st.npz"), allow_pickle=True)
    doc_ids = [str(x) for x in dense["doc_ids"]]
    qids = [str(x) for x in dense["qids"]]
    sc = np.load(os.path.join(args.cache, "scores_qd.npz"), allow_pickle=True)
    raw = {"dense_st": dense["dense_st"].astype(np.float32),
           "kw": np.load(os.path.join(args.cache, "kw.npz"), allow_pickle=True)["kw"].astype(np.float32),
           "bm25": np.load(os.path.join(args.cache, "bm25.npz"), allow_pickle=True)["bm25"].astype(np.float32),
           "sparse": sc["sparse"].astype(np.float32)}
    R = np.load(os.path.join(args.cache, "rerank_scores.npz"), allow_pickle=True)["scores"].astype(np.float32)
    with open(args.eval_set, encoding="utf-8") as fh:
        queries = {q["id"]: q for q in json.load(fh)["queries"]}

    norms = ("none", "minmax", "zscore", "rrf")
    mats = {n: {k: normalize(v, n, doc_ids) for k, v in raw.items()} for n in norms}
    rhat = normalize(R, "minmax", doc_ids)
    ticks = int(round(1.0 / args.step))

    def grid(keys, norm):
        best = None
        for combo in itertools.product(range(1, ticks + 1), repeat=len(keys)):
            if sum(combo) != ticks:
                continue
            w = np.array(combo, dtype=np.float64) / ticks
            F = np.zeros_like(R)
            for k, wv in zip(keys, w):
                F += wv * mats[norm][k]
            val = float(np.mean(distill_ndcg(F, rhat, 5)))
            if best is None or val > best[0]:
                best = (val, w, F)
        return best

    results = {}
    # 单路（词法各自独跑）
    for L in LEXICAL:
        F = mats["minmax"][L]
        results[f"单路 {L}"] = {"keys": [L], "norm": "minmax", "w": [1.0],
                                "distill5": round(float(np.mean(distill_ndcg(F, rhat, 5))), 6),
                                "spearman": round(spearman_mean(F, R), 4),
                                **qrels(F, doc_ids, qids, queries)}
    results["单路 dense"] = {"keys": ["dense_st"], "norm": "minmax", "w": [1.0],
                             "distill5": round(float(np.mean(distill_ndcg(mats["minmax"]["dense_st"], rhat, 5))), 6),
                             "spearman": round(spearman_mean(mats["minmax"]["dense_st"], R), 4),
                             **qrels(mats["minmax"]["dense_st"], doc_ids, qids, queries)}
    # 生产锚点（原始口径，不归一化）：dense + 0.05·kw
    F_prod = raw["dense_st"] + 0.05 * raw["kw"]
    results["生产 dense+0.05kw（锚点）"] = {"keys": ["dense_st", "kw"], "norm": "raw",
                                            "w": [1.0, 0.05],
                                            "distill5": round(float(np.mean(distill_ndcg(F_prod, rhat, 5))), 6),
                                            "spearman": round(spearman_mean(F_prod, R), 4),
                                            **qrels(F_prod, doc_ids, qids, queries)}
    # dense + 一条词法
    for L in LEXICAL:
        for norm in norms:
            val, w, F = grid(("dense_st", L), norm)
            results[f"dense + {L} / {norm}"] = {"keys": ["dense_st", L], "norm": norm,
                                                "w": [round(float(x), 3) for x in w],
                                                "distill5": round(val, 6),
                                                "spearman": round(spearman_mean(F, R), 4),
                                                **qrels(F, doc_ids, qids, queries)}
    # 参考：dense + 两条词法（验证"生态位相同、加第二条无用"）
    for pair in (("kw", "bm25"), ("kw", "sparse"), ("bm25", "sparse")):
        best = None
        for norm in norms:
            val, w, F = grid(("dense_st",) + pair, norm)
            if best is None or val > best[0][0]:
                best = ((val, w, F), norm)
        (val, w, F), norm = best
        results[f"dense + {pair[0]} + {pair[1]} / {norm}"] = {
            "keys": ["dense_st", *pair], "norm": norm, "w": [round(float(x), 3) for x in w],
            "distill5": round(val, 6), "spearman": round(spearman_mean(F, R), 4),
            **qrels(F, doc_ids, qids, queries)}

    # 每条词法的最优（按 L2）
    best_per_lex = {}
    for L in LEXICAL:
        cands = [(k, v) for k, v in results.items() if k.startswith(f"dense + {L} /")]
        best_per_lex[L] = max(cands, key=lambda kv: kv[1]["distill5"])

    rng = np.random.default_rng(20260916)

    def r1vec(F):
        out = []
        for i, q in enumerate(qids):
            rel = set(queries[q].get("relevant") or [])
            out.append(1.0 if rel and rank_ids(F[i], doc_ids)[0] in rel else 0.0)
        return np.array(out)

    mats_best = {}
    for L in LEXICAL:
        _, v = best_per_lex[L]
        keys, norm = v["keys"], v["norm"]
        F = np.zeros_like(R)
        for k, wv in zip(keys, v["w"]):
            F += wv * mats[norm][k]
        mats_best[L] = F
    idx = rng.integers(0, len(qids), size=(args.bootstrap, len(qids)))

    def ci(a, b):
        d = r1vec(a)[idx].mean(1) - r1vec(b)[idx].mean(1)
        return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))

    winner = max(LEXICAL, key=lambda L: best_per_lex[L][1]["distill5"])
    lines = [f"{'配置':40} {'distill5':8} {'Spearman':8} {'w':22} r@1    r@3    r@5    nDCG   MRR"]
    for k, v in sorted(results.items(), key=lambda kv: -kv[1]["distill5"]):
        lines.append(f"{k:40} {v['distill5']:.4f}  {v['spearman']:.4f}  {str(v['w']):22} "
                     f"{v['recall@1']:.4f} {v['recall@3']:.4f} {v['recall@5']:.4f} "
                     f"{v['nDCG@10']:.4f} {v['mrr']:.4f}")
    lines += ["", "每条词法的最优（dense + L）:"]
    for L in LEXICAL:
        name, v = best_per_lex[L]
        lines.append(f"  {L:7} {name:38} r@1 {v['recall@1']:.4f}  r@5 {v['recall@5']:.4f}  "
                     f"nDCG {v['nDCG@10']:.4f}  MRR {v['mrr']:.4f}  w={v['w']}")
    lines += ["", f"胜者 = {winner}（按蒸馏 nDCG@5）",
              f"  Δ({winner} − 生产) r@1 95% CI = [{ci(mats_best[winner], F_prod)[0]:+.4f}, "
              f"{ci(mats_best[winner], F_prod)[1]:+.4f}]"]
    for a, b in itertools.combinations(LEXICAL, 2):
        lo, hi = ci(mats_best[a], mats_best[b])
        lines.append(f"  Δ({a} − {b}) r@1 95% CI = [{lo:+.4f}, {hi:+.4f}]"
                     f"{'  ← 显著' if (lo > 0 or hi < 0) else '  （不显著）'}")
    text = "\n".join(lines)
    print(text)
    with open(args.table, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    print(f"\nwritten -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
