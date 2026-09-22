"""harness：trace × 评测集 → 指标。

口径（ADR-0030 D7）：**按 stop 分类的答案正确率优先**；gold 覆盖只作**筛查**
（`gold_unreached`），不当危害证据。答案判定用**确定性匹配**（不调 LLM 裁判）。
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from memory_agent.trace import Trace

_WS = re.compile(r"\s+")


def normalize(text: Any) -> str:
    return _WS.sub(" ", str(text or "").strip().lower())


def answer_matches(prediction: Any, gold: Any) -> bool:
    """确定性匹配：归一化后相等 / 包含 / 词元子集。"""
    pred, expected = normalize(prediction), normalize(gold)
    if not pred or not expected:
        return False
    if pred == expected or expected in pred or pred in expected:
        return True
    pred_tokens, gold_tokens = set(pred.split()), set(expected.split())
    return bool(gold_tokens) and gold_tokens <= pred_tokens


def score_trace(trace: Trace, gold: dict[str, Any]) -> dict[str, Any]:
    relevant = set(gold.get("relevant") or [])
    evidence = set(trace.evidence_ids())
    recall = len(relevant & evidence) / len(relevant) if relevant else None
    prediction = (trace.final or {}).get("answer")
    correct: bool | None
    if gold.get("kind") == "no_answer":
        correct = prediction is None
    elif gold.get("gold_answer") is not None:
        correct = bool(prediction) and answer_matches(prediction, gold["gold_answer"])
    else:
        correct = None
    return {
        "id": trace.id,
        "stop": trace.stop.trigger if trace.stop else None,
        "evidence_recall": recall,
        "answer_correct": correct,
        "gold_unreached": recall is not None and recall < 1.0,
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _rate(flags: list[bool]) -> float | None:
    return round(sum(1 for f in flags if f) / len(flags), 6) if flags else None


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    recalls = [r["evidence_recall"] for r in rows if r["evidence_recall"] is not None]
    correct = [r["answer_correct"] for r in rows if r["answer_correct"] is not None]
    unreached = [r["gold_unreached"] for r in rows if r["evidence_recall"] is not None]
    return {
        "n": len(rows),
        "mean_evidence_recall": _mean(recalls),
        "answer_correct_rate": _rate(correct),
        "gold_unreached_rate": _rate(unreached),
    }


def evaluate(traces: list[Trace], eval_set: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """`eval_set`：`{id: {query?, kind?, relevant?, gold_answer?}}`。"""
    rows = [score_trace(trace, eval_set.get(trace.id, {})) for trace in traces]
    by_stop: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_stop[row["stop"] or "none"].append(row)
    return {
        "overall": _aggregate(rows),
        "by_stop": {stop: _aggregate(group) for stop, group in sorted(by_stop.items())},
        "rows": rows,
    }
