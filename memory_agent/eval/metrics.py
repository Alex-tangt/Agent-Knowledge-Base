"""确定性检索指标（issue #24）：recall@k / nDCG@k / MRR，条目级二值相关。

纯函数、无模型、无 I/O——评测 harness 与单测共用。
"""
from __future__ import annotations

import math


def recall_at_k(ranked: list[str], relevant, k: int) -> float:
    """|top-k ∩ relevant| / |relevant|。"""
    relevant = set(relevant)
    if not relevant:
        return 0.0
    hits = sum(1 for doc in ranked[:k] if doc in relevant)
    return hits / len(relevant)


def ndcg_at_k(ranked: list[str], relevant, k: int) -> float:
    """二值相关 nDCG@k（折损 log2(rank+1)，IDCG 按理想命中数）。"""
    relevant = set(relevant)
    if not relevant:
        return 0.0
    dcg = 0.0
    for i, doc in enumerate(ranked[:k]):
        if doc in relevant:
            dcg += 1.0 / math.log2(i + 2)
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg else 0.0


def reciprocal_rank(ranked: list[str], relevant) -> float:
    """第一个相关命中的倒数排名；无命中为 0。"""
    relevant = set(relevant)
    for i, doc in enumerate(ranked):
        if doc in relevant:
            return 1.0 / (i + 1)
    return 0.0


def first_hit_rank(ranked: list[str], relevant) -> int | None:
    """第一个相关命中的排名（1 起）；无命中为 None。"""
    relevant = set(relevant)
    for i, doc in enumerate(ranked):
        if doc in relevant:
            return i + 1
    return None


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def evaluate(records, *, ks=(1, 3, 5, 10), ndcg_k: int = 10) -> dict:
    """对一批 `{id, query, relevant, ranked, ranked_scores?}` 求指标。

    - 有 `relevant` 的 query 计入 recall / nDCG / MRR（条目级二值）。
    - 无答案 query（`relevant == []`）不计入检索指标，单独报告 top-1 分数分布，
      供后续相关性阈值/拒答研究使用。
    """
    ks = tuple(sorted(set(ks)))
    per_query = []
    answerable = []
    no_answer = []

    for r in records:
        relevant = list(r.get("relevant") or [])
        if relevant:
            answerable.append(r)
        else:
            no_answer.append(r)

    for r in answerable:
        ranked = list(r.get("ranked") or [])
        relevant = list(r["relevant"])
        per_query.append({
            "id": r["id"],
            "query": r["query"],
            "relevant": relevant,
            "ranked": ranked,
            "recall": {str(k): round(recall_at_k(ranked, relevant, k), 6) for k in ks},
            f"ndcg@{ndcg_k}": round(ndcg_at_k(ranked, relevant, ndcg_k), 6),
            "mrr": round(reciprocal_rank(ranked, relevant), 6),
            "first_hit_rank": first_hit_rank(ranked, relevant),
        })

    aggregate = {
        "queries_total": len(records),
        "queries_answerable": len(answerable),
        "queries_no_answer": len(no_answer),
        "recall": {str(k): round(_mean(p["recall"][str(k)] for p in per_query), 6)
                   for k in ks},
        f"ndcg@{ndcg_k}": round(_mean(p[f"ndcg@{ndcg_k}"] for p in per_query), 6),
        "mrr": round(_mean(p["mrr"] for p in per_query), 6),
        "misses": [p["id"] for p in per_query if p["first_hit_rank"] is None],
    }

    no_answer_detail = [
        {
            "id": r["id"],
            "query": r["query"],
            "ranked": list(r.get("ranked") or []),
            "ranked_scores": list(r.get("ranked_scores") or []),
        }
        for r in no_answer
    ]

    if no_answer:
        top1 = [(r.get("ranked_scores") or [None])[0] for r in no_answer]
        numeric = [s for s in top1 if isinstance(s, (int, float))]
        aggregate["no_answer_top1_score"] = {
            "count": len(no_answer),
            "mean": round(_mean(numeric), 6) if numeric else None,
            "max": round(max(numeric), 6) if numeric else None,
        }

    return {"aggregate": aggregate, "per_query": per_query, "no_answer": no_answer_detail}
