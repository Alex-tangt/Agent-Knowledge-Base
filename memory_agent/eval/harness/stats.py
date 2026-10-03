"""harness：统计（bootstrap CI）。纯计算，不调模型（沿 ADR-0021 / phase_b 口径）。"""
from __future__ import annotations

import random
from typing import Any, Sequence


def paired_diffs(a: list[dict[str, Any]], b: list[dict[str, Any]],
                 key: str) -> list[float]:
    """按 `id` 对齐取 `a[key] - b[key]`；任一侧为 None 的题跳过。"""
    by_id = {row["id"]: row for row in b}
    diffs: list[float] = []
    for row in a:
        other = by_id.get(row["id"])
        if other is None:
            continue
        left, right = row.get(key), other.get(key)
        if left is None or right is None:
            continue
        diffs.append(float(left) - float(right))
    return diffs


def bootstrap_ci(values: Sequence[float], *, n_boot: int = 10000,
                 alpha: float = 0.05, seed: int = 0) -> dict[str, Any]:
    """百分位 bootstrap：返回 `{n, mean, lo, hi, significant}`。"""
    data = list(values)
    if not data:
        return {"n": 0, "mean": None, "lo": None, "hi": None, "significant": None}
    rng = random.Random(seed)
    n = len(data)
    means = []
    for _ in range(n_boot):
        means.append(sum(data[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo = means[int((alpha / 2) * n_boot)]
    hi = means[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return {
        "n": n,
        "mean": round(sum(data) / n, 6),
        "lo": round(lo, 6),
        "hi": round(hi, 6),
        "significant": (lo > 0) or (hi < 0),
    }
