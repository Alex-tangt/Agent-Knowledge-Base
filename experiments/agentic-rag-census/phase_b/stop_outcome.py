"""早停口径复核（离线，不调模型）：把「停了」与「答案对不对」对齐。

背景（ADR-0030 D7）：`report.md` 的**早停率**用 gold 覆盖定义（"停了但可达金标没搜齐"），
它默认**所有金标都必要**，因此只是**上界筛查量**。本脚本补一张 **outcome 交叉表**：
按 stop 分类看**答案正确率**——用来判断"早停"到底是不是危害。

只读已提交的 `artifacts/report.json`（含 stop 分类）+ `artifacts/per_query.json`（含 b3 正确率），
输出 `artifacts/stop_outcome.json` + 终端 markdown 表。

跑：`venv\\Scripts\\python.exe experiments/agentic-rag-census/phase_b/stop_outcome.py`
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ARTIFACTS = os.path.join(HERE, "artifacts")

CATEGORIES = ("complete", "premature", "stall", "budget")


def _find(node, key):
    """在嵌套 dict 里找第一个 key（report.json 结构可能调整）。"""
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for value in node.values():
            found = _find(value, key)
            if found is not None:
                return found
    return None


def _rate(flags: list[bool]) -> float | None:
    return round(sum(1 for f in flags if f) / len(flags), 6) if flags else None


def load():
    report = json.load(open(os.path.join(ARTIFACTS, "report.json"), encoding="utf-8"))
    per_query = json.load(open(os.path.join(ARTIFACTS, "per_query.json"), encoding="utf-8"))
    stop = _find(report, "per_query")
    if not stop:
        raise SystemExit("report.json 里找不到 stop per_query")
    b3 = {row["id"]: row for row in per_query["b3"]}
    return stop, b3


def build(stop, b3):
    by_category: dict[str, dict[str, list]] = defaultdict(lambda: {"correct": [], "unreached": []})
    by_type: dict[str, dict[str, dict[str, list]]] = defaultdict(
        lambda: defaultdict(lambda: {"correct": [], "unreached": []}))
    for row in stop:
        category = row.get("category")
        if category is None:
            continue
        answer = b3.get(row["id"], {})
        by_category[category]["correct"].append(bool(answer.get("correct")))
        by_category[category]["unreached"].append((row.get("reach_missing") or 0) > 0)
        question_type = answer.get("question_type", "?")
        by_type[question_type][category]["correct"].append(bool(answer.get("correct")))
        by_type[question_type][category]["unreached"].append((row.get("reach_missing") or 0) > 0)

    def summarize(groups):
        return {
            cat: {
                "n": len(groups[cat]["correct"]),
                "answer_correct_rate": _rate(groups[cat]["correct"]),
                "gold_unreached_rate": _rate(groups[cat]["unreached"]),
            }
            for cat in CATEGORIES if groups.get(cat, {}).get("correct")
        }

    return {
        "by_category": summarize(by_category),
        "by_question_type": {qtype: summarize(groups) for qtype, groups in sorted(by_type.items())},
    }


def markdown(result) -> str:
    lines = ["| stop 分类 | n | 答案正确率 | 有金标不可达 |", "|---|---|---|---|"]
    for cat, stats in result["by_category"].items():
        lines.append(f"| {cat} | {stats['n']} | {stats['answer_correct_rate']} "
                     f"| {stats['gold_unreached_rate']} |")
    lines.append("")
    lines.append("| 题型 | stop 分类 | n | 答案正确率 |")
    lines.append("|---|---|---|---|")
    for qtype, groups in result["by_question_type"].items():
        for cat, stats in groups.items():
            lines.append(f"| {qtype} | {cat} | {stats['n']} | {stats['answer_correct_rate']} |")
    return "\n".join(lines)


def main() -> None:
    stop, b3 = load()
    result = build(stop, b3)
    out = os.path.join(ARTIFACTS, "stop_outcome.json")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(markdown(result))
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
