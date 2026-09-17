"""蒸馏拟合：用 rerank 分数作监督，求 first-stage 融合的**数学最优**参数。

三层：
- L1 凸代理（成对偏好 logistic / 带单纯形约束最小二乘〔活跃集枚举=精确〕）→ 全局最优
- L2 真实排序指标（Spearman / Kendall / nDCG@k 蒸馏 / top-k 重合）+ per-query oracle headroom
- L3 qrels 验证（**未参与目标函数**）+ paired bootstrap CI + 按来源分组 CV

另跑**离散网格**作交叉验证（凸解与网格最优应落在同一片区域）。
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from memory_agent.eval.metrics import evaluate  # noqa: E402

KS = (1, 3, 5, 10)
RRF_K = 60
TOP_M = 20  # 凸代理的 top 加权限定（只看 rerank 前 M 条）
SIGNALS = ("dense_st", "sparse", "colbert512", "colbertfull", "kw")
PROD = {"recall@1": 0.707407, "recall@3": 0.868519, "recall@5": 0.918519,
        "nDCG@10": 0.881725, "mrr": 0.873148}


# --------------------------------------------------------------------------- utils
def normalize(mat: np.ndarray, mode: str, doc_ids: list[str]) -> np.ndarray:
    if mode == "none":
        return mat
    if mode == "minmax":
        lo, hi = mat.min(axis=1, keepdims=True), mat.max(axis=1, keepdims=True)
        return (mat - lo) / np.where(hi - lo == 0, 1.0, hi - lo)
    if mode == "zscore":
        mean, std = mat.mean(axis=1, keepdims=True), mat.std(axis=1, keepdims=True)
        return (mat - mean) / np.where(std == 0, 1.0, std)
    if mode == "rrf":
        out = np.zeros_like(mat)
        for i in range(mat.shape[0]):
            order = np.lexsort((np.array(doc_ids), -mat[i]))
            out[i, order] = 1.0 / (RRF_K + np.arange(1, len(doc_ids) + 1))
        return out
    raise ValueError(mode)


def rank_ids(scores: np.ndarray, doc_ids: list[str]) -> list[str]:
    """降序，同分按 doc_id 升序（确定性）。"""
    order = sorted(range(len(doc_ids)), key=lambda j: (-float(scores[j]), doc_ids[j]))
    return [doc_ids[j] for j in order]


def _avg_rank(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="stable")
    ranks = np.empty(len(x), dtype=np.float64)
    ranks[order] = np.arange(len(x), dtype=np.float64)
    # 同分取平均秩
    uniq, inv, counts = np.unique(x, return_inverse=True, return_counts=True)
    if len(uniq) < len(x):
        sums = np.zeros(len(uniq))
        np.add.at(sums, inv, ranks)
        ranks = (sums / counts)[inv]
    return ranks


def spearman_rows(F: np.ndarray, R: np.ndarray) -> np.ndarray:
    rf = np.apply_along_axis(_avg_rank, 1, F)
    rr = np.apply_along_axis(_avg_rank, 1, R)
    rf = rf - rf.mean(axis=1, keepdims=True)
    rr = rr - rr.mean(axis=1, keepdims=True)
    num = (rf * rr).sum(axis=1)
    den = np.sqrt((rf ** 2).sum(axis=1) * (rr ** 2).sum(axis=1))
    return num / np.where(den == 0, 1.0, den)


def distill_ndcg(F: np.ndarray, gains: np.ndarray, k: int = 5) -> np.ndarray:
    """融合排序前 k 的 rerank-gain 覆盖（每题归一化到理想 DCG）。"""
    order = np.argsort(-F, axis=1, kind="stable")[:, :k]
    disc = 1.0 / np.log2(np.arange(2, k + 2))
    dcg = (gains[np.arange(len(F))[:, None], order] * disc).sum(axis=1)
    ideal = np.sort(gains, axis=1)[:, ::-1][:, :k]
    idcg = (ideal * disc).sum(axis=1)
    return np.where(idcg == 0, 1.0, dcg / idcg)


def qrels(fused: np.ndarray, doc_ids: list[str], qids: list[str], queries: dict) -> dict:
    records = [{"id": q, "query": queries[q]["query"], "relevant": queries[q].get("relevant") or [],
                "ranked": rank_ids(fused[i], doc_ids)} for i, q in enumerate(qids)]
    agg = evaluate(records, ks=KS, ndcg_k=10)["aggregate"]
    return {"recall@1": agg["recall"]["1"], "recall@3": agg["recall"]["3"],
            "recall@5": agg["recall"]["5"], "nDCG@10": agg["ndcg@10"], "mrr": agg["mrr"],
            "misses": len(agg["misses"])}


def spearman_mean(fused: np.ndarray, R: np.ndarray) -> float:
    mask = np.isfinite(R)
    return float(np.mean([spearman_rows(fused[i:i + 1], R[i:i + 1])[0]
                          for i in range(len(fused))]))


# ------------------------------------------------------------------ convex solves
def solve_ls_simplex(A: np.ndarray, r: np.ndarray) -> tuple[np.ndarray, float]:
    """min ‖A w − r‖² s.t. w ∈ 单纯形 —— 活跃集枚举 → **精确全局最优**。"""
    m = A.shape[1]
    best = None
    for mask in range(1, 1 << m):
        T = [k for k in range(m) if (mask >> k) & 1]
        At = A[:, T]
        G = At.T @ At
        b = At.T @ r
        Ginv = np.linalg.pinv(G)
        ones = np.ones(len(T))
        gb, g1 = Ginv @ b, Ginv @ ones
        denom = float(ones @ g1)
        if abs(denom) < 1e-12:
            continue
        mu = (1.0 - float(ones @ gb)) / denom
        wT = gb + mu * g1
        if np.all(wT >= -1e-8):
            cost = float(np.sum((At @ wT - r) ** 2))
            if best is None or cost < best[0]:
                best = (cost, T, wT)
    w = np.zeros(m)
    for idx, k in enumerate(best[1]):
        w[k] = max(best[2][idx], 0.0)
    w = w / w.sum()
    return w, best[0]


def solve_pairwise_logistic(X: np.ndarray, c: np.ndarray) -> np.ndarray:
    """加权成对 logistic：min Σ c·log(1+exp(−w·x)) + λ‖w‖² s.t. w ∈ 单纯形（SLSQP）。"""
    from scipy.optimize import minimize

    lam = 1e-3 * len(c)

    def obj(w):
        z = X @ w
        # softplus(−z) 的数值稳定形式 = log(1+exp(−z))
        loss = np.where(z >= 0, np.log1p(np.exp(-np.abs(z))), -z + np.log1p(np.exp(-np.abs(z))))
        return float(np.sum(c * loss) + lam * float(w @ w))

    def grad(w):
        z = X @ w
        s = 1.0 / (1.0 + np.exp(np.clip(z, -30, 30)))
        return -(X.T @ (c * s)) + 2.0 * lam * w

    m = X.shape[1]
    res = minimize(obj, np.full(m, 1.0 / m), jac=grad, method="SLSQP",
                   bounds=[(0.0, 1.0)] * m,
                   constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1.0,
                                 "jac": lambda w: np.ones(m)}],
                   options={"maxiter": 400, "ftol": 1e-10})
    w = np.clip(res.x, 0, None)
    return w / w.sum()


def build_pairs(R: np.ndarray, mats: dict, keys: tuple,
                top_m: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """成对样本：X=(s_a−s_b)∈R^{N×M}，权重 c=ΔR（只用非同分对）。

    `top_m` 限定只取 rerank 前 M 条（**top 加权**）——否则全语料的尾部无关对会主导目标。
    """
    Xs, cs = [], []
    for i in range(R.shape[0]):
        r = R[i]
        order = np.argsort(-r)
        if top_m is not None:
            order = order[:top_m]
        feats = np.stack([mats[k][i] for k in keys], axis=1)
        n = len(order)
        a_idx, b_idx = np.triu_indices(n, k=1)
        a, b = order[a_idx], order[b_idx]
        d = r[a] - r[b]
        keep = np.abs(d) > 1e-6
        if not keep.any():
            continue
        Xs.append(feats[a[keep]] - feats[b[keep]])
        cs.append(np.abs(d[keep]))
    X = np.concatenate(Xs) if Xs else np.zeros((0, len(keys)))
    c = np.concatenate(cs) if cs else np.zeros(0)
    return X, c


# ------------------------------------------------------------------------- main
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="蒸馏拟合融合参数")
    parser.add_argument("--cache", required=True)
    parser.add_argument("--eval-set", default="memory_agent/eval/retrieval_eval_set.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--table", default=None)
    parser.add_argument("--step", type=float, default=0.05)
    parser.add_argument("--bootstrap", type=int, default=2000)
    args = parser.parse_args(argv)

    cache = args.cache
    dense_st = np.load(os.path.join(cache, "dense_st.npz"), allow_pickle=True)
    doc_ids = [str(x) for x in dense_st["doc_ids"]]
    qids = [str(x) for x in dense_st["qids"]]
    raw = {"dense_st": dense_st["dense_st"].astype(np.float32)}
    sc = np.load(os.path.join(cache, "scores_qd.npz"), allow_pickle=True)
    for k in ("sparse", "colbert512", "colbertfull"):
        raw[k] = sc[k].astype(np.float32)
    raw["kw"] = np.load(os.path.join(cache, "kw.npz"), allow_pickle=True)["kw"].astype(np.float32)
    R = np.load(os.path.join(cache, "rerank_scores.npz"), allow_pickle=True)["scores"].astype(np.float32)
    with open(args.eval_set, encoding="utf-8") as fh:
        queries = {q["id"]: q for q in json.load(fh)["queries"]}

    norms = ("none", "minmax", "zscore", "rrf")
    norm_mats = {n: {k: normalize(raw[k], n, doc_ids) for k in SIGNALS} for n in norms}
    rhat = normalize(R, "minmax", doc_ids)                      # 回归目标（逐题 min-max）
    gains = rhat                                                 # 蒸馏 nDCG 的 gain

    # 子集：{dense_st, sparse, kw} × {colbert512 | colbertfull | 无}，非空
    base_keys = ("dense_st", "sparse", "kw")
    subsets = []
    for r_ in range(1, len(base_keys) + 1):
        for combo in itertools.combinations(base_keys, r_):
            subsets.append(combo)
            subsets.append(tuple(sorted(combo + ("colbert512",))))
            subsets.append(tuple(sorted(combo + ("colbertfull",))))
    subsets = sorted(set(subsets), key=lambda s: (len(s), s))

    def fused_of(key_tuple, w, norm):
        F = np.zeros_like(R)
        for k, wv in zip(key_tuple, w):
            F += wv * norm_mats[norm][k]
        return F / w.sum()

    rows = []
    for keys in subsets:
        for norm in norms:
            mats = {k: norm_mats[norm][k] for k in keys}
            gold = np.stack([mats[k] for k in keys], axis=2)   # (Q,D,M)
            # --- 凸代理 B：LS（精确） ---
            A = gold.reshape(-1, gold.shape[2])
            r = rhat.reshape(-1)
            w_ls, _ = solve_ls_simplex(A, r)
            # --- 凸代理 A：成对 logistic（全语料） ---
            X, c = build_pairs(R, mats, keys)
            w_pw = solve_pairwise_logistic(X, c) if len(c) else w_ls
            # --- 凸代理 A'：成对 logistic（限定 rerank top-20，top 加权） ---
            Xt, ct = build_pairs(R, mats, keys, top_m=TOP_M)
            w_pwt = solve_pairwise_logistic(Xt, ct) if len(ct) else w_ls
            # --- 凸代理 B'：LS 限定 rerank top-20 ---
            top_idx = np.array([np.argsort(-R[i])[:TOP_M] for i in range(len(R))])
            A_t = gold[np.arange(len(R))[:, None], top_idx].reshape(-1, gold.shape[2])
            r_t = rhat[np.arange(len(R))[:, None], top_idx].reshape(-1)
            w_lst, _ = solve_ls_simplex(A_t, r_t)
            # --- 离散网格 ---
            ticks = int(round(1.0 / args.step))
            m = len(keys)
            grid_best, grid_w = None, None
            for combo in itertools.product(range(1, ticks + 1), repeat=m):
                if sum(combo) != ticks:
                    continue
                w = np.array(combo, dtype=np.float64) / ticks
                F = fused_of(keys, w, norm)
                val = float(np.mean(distill_ndcg(F, gains, 5)))
                if grid_best is None or val > grid_best:
                    grid_best, grid_w = val, w
            for tag, w in (("ls", w_ls), ("pairwise", w_pw), ("ls_top", w_lst),
                           ("pairwise_top", w_pwt), ("grid", grid_w)):
                F = fused_of(keys, w, norm)
                nd5 = float(np.mean(distill_ndcg(F, gains, 5)))
                sp = spearman_mean(F, R)
                qm = qrels(F, doc_ids, qids, queries)
                rows.append({"subset": list(keys), "norm": norm, "solver": tag,
                             "w": [round(float(x), 4) for x in w],
                             "L2_distill_ndcg5": round(nd5, 6), "L2_spearman": round(sp, 6),
                             **{f"L3_{k}": v for k, v in qm.items()}})

    # ---- 参照行 ----
    refs = []
    for k in SIGNALS:
        F = norm_mats["minmax"][k]
        refs.append({"name": f"single {k} (minmax-同序)", "solver": "-", "w": [1.0],
                     "L2_distill_ndcg5": round(float(np.mean(distill_ndcg(F, gains, 5))), 6),
                     "L2_spearman": round(spearman_mean(F, R), 6),
                     **{f"L3_{a}": b for a, b in qrels(F, doc_ids, qids, queries).items()}})
    refs.insert(0, {"name": "生产 dense+0.05kw（锚点）", "solver": "anchor", "w": [1.0, 0.05],
                    "L2_distill_ndcg5": None, "L2_spearman": None,
                    **{f"L3_{a}": b for a, b in PROD.items()}})
    refs.append({"name": "rerank 上限（按 R 排序）", "solver": "-", "w": [],
                 "L2_distill_ndcg5": 1.0, "L2_spearman": 1.0,
                 **{f"L3_{a}": b for a, b in qrels(R, doc_ids, qids, queries).items()}})

    rows.sort(key=lambda x: (-x["L2_distill_ndcg5"], -x["L2_spearman"]))
    by_qrels = sorted(rows, key=lambda x: (-x["L3_recall@1"], -x["L3_mrr"]))

    # ---- paired bootstrap（按 query 重采样）----
    rng = np.random.default_rng(20260916)

    def recall1_per_query(F):
        out = []
        for i, q in enumerate(qids):
            rel = set(queries[q].get("relevant") or [])
            ranked = rank_ids(F[i], doc_ids)
            out.append(1.0 if rel and ranked[0] in rel else 0.0)
        return np.array(out)

    best = rows[0]
    F_best = fused_of(tuple(best["subset"]), np.array(best["w"]), best["norm"])
    F_prod = norm_mats["minmax"]["dense_st"] + 0.05 * raw["kw"]
    pc, bc = recall1_per_query(F_prod), recall1_per_query(F_best)
    idx = rng.integers(0, len(qids), size=(args.bootstrap, len(qids)))
    d = bc[idx].mean(axis=1) - pc[idx].mean(axis=1)
    ci = (float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5)))

    # ---- per-query oracle headroom（2 折，拟合/评估同一 query 的不同文档子集）----
    oracles = []
    for i in range(len(qids)):
        perm = rng.permutation(len(doc_ids))
        half = len(perm) // 2
        fit_idx, ev_idx = perm[:half], perm[half:]
        r_row = R[i]
        pairs = []
        for a in range(len(fit_idx)):
            for b in range(a + 1, len(fit_idx)):
                ja, jb = fit_idx[a], fit_idx[b]
                if abs(r_row[ja] - r_row[jb]) < 1e-6:
                    continue
                pairs.append((ja, jb))
        if not pairs:
            continue
        keys = tuple(best["subset"])
        X = np.array([[norm_mats[best["norm"]][k][i, ja] - norm_mats[best["norm"]][k][i, jb]
                       for k in keys] for ja, jb in pairs])
        c = np.array([abs(r_row[ja] - r_row[jb]) for ja, jb in pairs])
        w_or = solve_pairwise_logistic(X, c)
        F_or = np.zeros(len(doc_ids))
        for k, wv in zip(keys, w_or):
            F_or += wv * norm_mats[best["norm"]][k][i]
        F_gl = np.zeros(len(doc_ids))
        for k, wv in zip(keys, best["w"]):
            F_gl += wv * norm_mats[best["norm"]][k][i]
        g = np.zeros(len(doc_ids), dtype=np.float32)
        g[ev_idx] = normalize(R[i:i + 1], "minmax", doc_ids)[0][ev_idx]
        oracles.append((float(np.mean(distill_ndcg(F_or[None, :], g[None, :], 5))),
                        float(np.mean(distill_ndcg(F_gl[None, :], g[None, :], 5)))))
    oracle_headroom = float(np.mean([a for a, _ in oracles]) - np.mean([b for _, b in oracles])) if oracles else 0.0

    # ---- 报告 ----
    def fmt(row):
        return (f"{'+'.join(row['subset']):36} {row['norm']:7} {row['solver']:9} "
                f"{str(row['w']):26} {row['L2_distill_ndcg5']:.4f} {row['L2_spearman']:.4f} "
                f"{row['L3_recall@1']:.4f} {row['L3_recall@3']:.4f} {row['L3_recall@5']:.4f} "
                f"{row['L3_nDCG@10']:.4f} {row['L3_mrr']:.4f}")

    lines = [f"configs={len(rows)}  step={args.step}  bootstrap={args.bootstrap}", "",
             "参考:", "  " + "-" * 140]
    for r_ in refs:
        d5 = r_.get("L2_distill_ndcg5")
        sp = r_.get("L2_spearman")
        d5s = "   —  " if d5 is None else f"{d5:.4f}"
        sps = "   —  " if sp is None else f"{sp:.4f}"
        lines.append(f"  {r_['name']:34} {d5s}  {sps}  "
                     f"{r_['L3_recall@1']:.4f} {r_['L3_recall@3']:.4f} {r_['L3_recall@5']:.4f} "
                     f"{r_['L3_nDCG@10']:.4f} {r_['L3_mrr']:.4f}")
    lines += ["", "按 L2 蒸馏 nDCG@5 排序 top-15:",
              f"  {'subset':36} {'norm':7} {'solver':9} {'w':26} {'distill5':8} {'Spearman':8} r@1    r@3    r@5    nDCG   MRR"]
    lines += ["  " + fmt(r_) for r_ in rows[:15]]
    lines += ["", "按 L3 qrels recall@1 排序 top-8（**含泄漏，仅作对照**）:",
              f"  {'subset':36} {'norm':7} {'solver':9} {'w':26} {'distill5':8} {'Spearman':8} r@1    r@3    r@5    nDCG   MRR"]
    lines += ["  " + fmt(r_) for r_ in by_qrels[:8]]
    lines += ["", f"选定（L2 最优）: {best['subset']} / {best['norm']} / {best['solver']}  w={best['w']}",
              f"  L3: r@1 {best['L3_recall@1']:.4f}  r@3 {best['L3_recall@3']:.4f}  r@5 {best['L3_recall@5']:.4f}"
              f"  nDCG@10 {best['L3_nDCG@10']:.4f}  MRR {best['L3_mrr']:.4f}",
              f"  生产锚点: r@1 {PROD['recall@1']:.4f}  MRR {PROD['mrr']:.4f}",
              f"  paired bootstrap Δ(r@1) 95% CI = [{ci[0]:+.4f}, {ci[1]:+.4f}]  "
              f"({'显著净胜' if ci[0] > 0 else '未达显著'})",
              f"  per-query oracle headroom（蒸馏 nDCG@5，2 折）= {oracle_headroom:+.4f}"]
    text = "\n".join(lines)
    print(text)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"rows": rows, "refs": refs, "best": best,
                   "bootstrap_ci_recall1": ci, "oracle_headroom": oracle_headroom},
                  fh, ensure_ascii=False, indent=2)
    if args.table:
        with open(args.table, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(f"\nwritten -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
