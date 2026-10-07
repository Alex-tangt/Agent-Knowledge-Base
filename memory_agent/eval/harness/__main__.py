"""harness 的**命令入口**：一条命令跑完场景集 → 统计结论（#62 验收②）。

```
venv\\Scripts\\python.exe -m memory_agent.eval.harness <scenarios.json>
```

零网络、零模型权重：LLM 与工具都来自 `harness/stubs.py`（回放脚本）。产出**两层**报告：

1. **按 stop 分组的答案正确率**（`scorer.evaluate`，ADR-0030 D7：正确率优先、gold 只作筛查）；
2. **bootstrap CI**（`stats.bootstrap_ci`，纯计算）——对 `holdout` 的答案正确率、整体
   「不作答题不编造」率、gold 覆盖达标率，以及 dev↔holdout 的 paired 差值各给一个百分位 CI。

**确定性锚点**：报告是 asdict 后的 `json.dumps(..., sort_keys=True, ensure_ascii=False)`，
且一切都是回放（无采样、无时钟、无路径）。同一条命令跑两次 → **逐字节相同**。
`--out` 落盘与 stdout **同一份字节**。

## 复用（不重写）

- 指标聚合：`memory_agent/eval/harness/scorer.py`（其均值口径复用 `memory_agent/eval/metrics.py::_mean`）；
- CI：`memory_agent/eval/harness/stats.py::bootstrap_ci` / `paired_diffs`；
- `experiments/.../phase_b/analyze.py` **不在包内、不 import**（实验产物，非交付面）。

`bootstrap_ci` 之所以保留在 `harness/stats.py` 而没有被换成某个共享实现：`memory_agent/`
下**没有**可复用的 bootstrap 工具（`eval/metrics.py` 只有确定性检索指标，无重采样），
所以它继续由本 harness 负责，并在报告里显式说明。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from memory_agent._bootstrap import configure_utf8_stdio
from memory_agent.eval.harness import scenarios as scenario_set
from memory_agent.eval.harness.runner import Runner
from memory_agent.eval.harness.scorer import evaluate
from memory_agent.eval.harness.stats import bootstrap_ci, paired_diffs
from memory_agent.eval.harness.stubs import ScriptedLLM, StubTools, budget_for

SCHEMA_VERSION = 1
#: 场景集 `defaults` 缺省（预算）。
DEFAULT_K = 5
DEFAULT_MAX_HOPS = 3
DEFAULT_MAX_EVIDENCE = 20


@dataclass
class Analysis:
    """规范化的统计结论（dataclass → `asdict` → `json.dumps(sort_keys=True)`）。

    `kind="rate"` 的 `mean` 是比例；`kind="mean"` 的 `mean` 是连续量的均值。
    `significant` 沿用 `bootstrap_ci` 的语义（百分位区间是否不含 0）——它是**比例类**
    指标的描述，不要读成「显著优于某个基线」（本报告没有对照组）。
    `degenerate=True` 表示区间宽度为 0（例如全部命中 1.0）：此时 `significant` 没有
    统计含义，只是「比例大于 0」。
    """
    metric: str
    kind: str
    n: int
    mean: float | None
    lo: float | None
    hi: float | None
    significant: bool | None
    degenerate: bool
    note: str = ""

    @classmethod
    def of(cls, metric: str, values: list[float], *, note: str = "", kind: str = "rate",
           n_boot: int = 10000, alpha: float = 0.05, seed: int = 0) -> "Analysis":
        report = bootstrap_ci(values, n_boot=n_boot, alpha=alpha, seed=seed)
        lo, hi = report["lo"], report["hi"]
        return cls(metric=metric, kind=kind, n=report["n"], mean=report["mean"], lo=lo, hi=hi,
                   significant=report["significant"],
                   degenerate=lo is not None and lo == hi, note=note)


@dataclass
class HarnessReport:
    schema_version: int
    scenario_set: str
    bootstrap: dict[str, Any]
    scenario_set_sha256: str
    counts: dict[str, Any]
    splits: dict[str, Any]
    analyses: list[Analysis]
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, sort_keys=True)


def stable_seed(*parts: Any) -> int:
    """由内容派生 bootstrap 种子——固定值，不含随机 / 时间。"""
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _budget_for(scenario: dict[str, Any], defaults: dict[str, Any]):
    return budget_for(scenario, defaults=defaults,
                      default_hops=DEFAULT_MAX_HOPS,
                      default_evidence=DEFAULT_MAX_EVIDENCE)


def _traces_for(span: list[dict[str, Any]], defaults: dict[str, Any]) -> list[Any]:
    """每个场景现造预算 / LLM / 工具（场景之间零串状态）。"""
    traces = []
    for scenario in span:
        runner = Runner(
            ScriptedLLM(scenario.get("script") or []),
            lambda _scenario, evidence=scenario.get("stub_evidence"): StubTools(evidence or {}),
            budget=_budget_for(scenario, defaults),
            k=int(defaults.get("k", DEFAULT_K)),
        )
        traces.extend(runner.run([scenario]))
    return traces


def _report_for(span: list[dict[str, Any]], defaults: dict[str, Any]) -> dict[str, Any]:
    """按 stop 分组的答案正确率 + gold 覆盖筛查（`scorer.evaluate`）。"""
    traces = _traces_for(span, defaults)
    evalset = scenario_set.eval_set(span)
    evaluation = evaluate(traces, evalset)
    rows = evaluation["rows"]
    for row in rows:
        gold = evalset.get(row["id"], {})
        prediction = (next((t.final for t in traces if t.id == row["id"]), {}) or {}).get("answer")
        # 派生展示标志（不重复 scorer 的判定口径）：不作答题里"没有编造答案"。
        row["non_fabrication"] = (prediction is None) if gold.get("kind") == "no_answer" else None
    expectations = {scenario["id"]: (scenario.get("expect") or {}).get("stop") for scenario in span}
    judged = [row for row in rows if expectations.get(row["id"]) is not None]
    matched = sum(1 for row in judged if row["stop"] == expectations[row["id"]])
    return {
        "evaluate": evaluation,
        "traces": traces,
        "expectation_total": len(judged),
        "expectation_matched": matched,
        "expected_stop_match_rate": _ratio(
            [1.0 if row["stop"] == expectations[row["id"]] else 0.0 for row in judged]),
        "expectation_mismatch": [{"id": row["id"], "expected": expectations[row["id"]],
                                  "actual": row["stop"]} for row in judged
                                 if row["stop"] != expectations[row["id"]]],
    }


def _flags(rows: Iterable[dict[str, Any]], key: str) -> list[float]:
    return [1.0 if row[key] else 0.0 for row in rows if row.get(key) is not None]


def _ratio(flags: list[float]) -> float | None:
    """比例（无样本回 `None`）——`expected_stop_match_rate` 的口径。"""
    return round(sum(flags) / len(flags), 6) if flags else None


def build_report(path: str | Path | None = None) -> HarnessReport:
    """跑完整个场景集，返回确定性报告对象。"""
    payload = scenario_set.validate_file(path)
    defaults = dict(payload.get("defaults") or {})
    scenarios = list(payload["scenarios"])
    grouped = scenario_set.split_scenarios(scenarios)

    evaluations = {
        split: _report_for(span, defaults) for split, span in sorted(grouped.items())
    }

    holdout_key = "holdout"
    if holdout_key not in evaluations:
        raise scenario_set.ScenarioSetError("场景集没有 holdout 切分，无法给出对外报数")
    holdout = evaluations[holdout_key]
    holdout_rows = holdout["evaluate"]["rows"]
    seed = stable_seed("holdout", [row["id"] for row in holdout_rows],
                       path.name if path is not None else "scenarios.json")

    notes = [
        "dev 只用于调参 / 人眼观察；对外报数**只报 holdout**（见 scenarios.py 的留出规则）。",
        "bootstrap CI 来自 harness/stats.py::bootstrap_ci（纯计算，无模型）；"
        "memory_agent/ 下没有可复用的 bootstrap 工具，故保留在此，不另造第二份。",
        "`significant` 沿用 bootstrap_ci 的语义 = 百分位区间不含 0。本报告**没有对照组**，"
        "所以它不是「显著优于某基线」，只是「比例/均值 > 0」；`degenerate=true`（零宽区间，"
        "例如全部为 1.0）时该字段没有统计含义。",
        "指标聚合复用 eval/metrics.py::_mean（scorer 不再自写均值）。",
        "`experiments/` 下的 phase_b/analyze.py 属实验产物，本 harness **不 import**。",
        "确定性：stub LLM + stub 工具全程回放，无采样 / 无网络 / 无时钟；"
        "同命令两次运行报告逐字节相同。",
    ]

    report = HarnessReport(
        schema_version=SCHEMA_VERSION,
        scenario_set=str(path) if path is not None else "memory_agent/eval/harness/scenarios.json",
        bootstrap={"n_boot": 10000, "alpha": 0.05, "seed": seed,
                   "method": "percentile bootstrap（harness/stats.py）"},
        scenario_set_sha256=_sha256_file(
            Path(path) if path is not None else scenario_set.DEFAULT_SCENARIO_PATH),
        counts={
            "total": len(scenarios),
            "by_split": {split: len(span) for split, span in sorted(grouped.items())},
            "by_kind": _counts(scenarios, "kind"),
            "expected_stop": _counts([s for s in scenarios if s.get("expect")], "expect.stop"),
        },
        splits={
            split: {
                "ids": [scenario["id"] for scenario in grouped[split]],
                "n": len(grouped[split]),
                "expectation_total": evaluations[split]["expectation_total"],
                "expectation_matched": evaluations[split]["expectation_matched"],
                "expected_stop_match_rate": evaluations[split]["expected_stop_match_rate"],
                "expectation_mismatch": evaluations[split]["expectation_mismatch"],
                "overall": evaluations[split]["evaluate"]["overall"],
                "by_stop": evaluations[split]["evaluate"]["by_stop"],
            } for split in sorted(grouped)
        },
        analyses=_analyses(evaluations, holdout_rows, holdout_key),
        notes=notes,
    )
    return report


def _counts(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        value = item
        for part in key.split("."):
            value = (value or {}).get(part) if isinstance(value, dict) else None
        out[str(value)] = out.get(str(value), 0) + 1
    return dict(sorted(out.items()))


def _analyses(evaluations: dict[str, dict[str, Any]], holdout_rows: list[dict[str, Any]],
              holdout_key: str) -> list[Analysis]:
    seed = stable_seed("analyses", holdout_key, [row["id"] for row in holdout_rows])
    primary = Analysis.of(
        f"{holdout_key}.answer_correct_rate",
        _flags(holdout_rows, "answer_correct"),
        note=("主结论：留出集上的答案正确率（确定性匹配，不调 LLM 裁判）。"
              f"n = 留出集里可判分的题数 = {len(_flags(holdout_rows, 'answer_correct'))}。"),
        seed=seed,
    )
    non_fab = Analysis.of(
        f"{holdout_key}.non_fabrication_rate",
        _flags(holdout_rows, "non_fabrication"),
        note="不作答题（kind=no_answer）里没有编造答案的比例。",
        seed=seed,
    )
    covered = Analysis.of(
        f"{holdout_key}.gold_covered_rate",
        [1.0 if row["evidence_recall"] == 1.0 else 0.0
         for row in holdout_rows if row["evidence_recall"] is not None],
        note=("gold 覆盖达标率：展示给模型的证据**覆盖全部 gold** 的题占比（ADR-0030 D7："
              "gold 覆盖只作**筛查**，不作危害证据）。"),
        seed=seed,
    )
    paired = Analysis.of(
        f"{holdout_key}_minus_dev.answer_correct_rate",
        [float(value) for value in paired_diffs(
            [{"id": row["id"], "v": row["answer_correct"]} for row in holdout_rows],
            [{"id": row["id"], "v": row["answer_correct"]}
             for row in evaluations.get("dev", {}).get("evaluate", {}).get("rows", [])],
            "v",
        )],
        note=("dev 与 holdout 的正确率差（paired，按题 id 对齐）。**本场景集两侧题号无交集，"
              "故 n=0——这一项在结构上不构成结论**，保留它只为固定报告的字段形状。"
              "在 #65 的 in-domain 集上，同一字段即是「机制改动前后」的 paired 比较。"),
        seed=seed,
    )
    return [primary, non_fab, covered, paired]


def markdown(report: HarnessReport) -> str:
    """人读视图（同一份数据，不改口径）。"""
    lines = [
        "# 检索 agent harness 报告（确定性场景集）",
        "",
        f"- 场景集：`{report.scenario_set}`（sha256 `{report.scenario_set_sha256[:12]}`）",
        f"- 场景数：{report.counts['total']}（by split "
        f"{report.counts['by_split']} / by kind {report.counts['by_kind']}）",
        f"- bootstrap：{report.bootstrap['n_boot']} 次重采样，alpha={report.bootstrap['alpha']}，"
        f"seed={report.bootstrap['seed']}",
        "",
        "## 按 split × 停止触发词",
        "",
        "| split | n | 答案正确率 | mean recall | gold_unreached | 预期 stop 命中 |",
        "|---|---|---|---|---|---|",
    ]
    for split, block in report.splits.items():
        overall = block["overall"]
        rate = overall["answer_correct_rate"]
        lines.append(
            f"| {split} | {overall['n']} | "
            f"{'n/a' if rate is None else format(rate, '.4f')} | "
            f"{'n/a' if overall['mean_evidence_recall'] is None else format(overall['mean_evidence_recall'], '.4f')} | "
            f"{'n/a' if overall['gold_unreached_rate'] is None else format(overall['gold_unreached_rate'], '.4f')} | "
            f"{block['expectation_matched']}/{block['expectation_total']} |"
        )
    lines += ["", "### by_stop 明细", "",
              "| split | stop | n | 答案正确率 | mean recall | gold_unreached |",
              "|---|---|---|---|---|---|"]
    for split, block in report.splits.items():
        for stop, group in block["by_stop"].items():
            rate = group["answer_correct_rate"]
            lines.append(
                f"| {split} | {stop} | {group['n']} | "
                f"{'n/a' if rate is None else format(rate, '.4f')} | "
                f"{'n/a' if group['mean_evidence_recall'] is None else format(group['mean_evidence_recall'], '.4f')} | "
                f"{'n/a' if group['gold_unreached_rate'] is None else format(group['gold_unreached_rate'], '.4f')} |"
            )
    lines += ["", "## bootstrap CI（95%）", "",
              "| 指标 | 类别 | n | mean | lo | hi | 区间不含 0 | 零宽区间 |",
              "|---|---|---|---|---|---|---|---|"]
    for item in report.analyses:
        lines.append(
            f"| {item.metric} | {item.kind} | {item.n} | {item.mean} | {item.lo} | {item.hi} | "
            f"{item.significant} | {item.degenerate} |"
        )
    lines += ["", "## 口径备注", ""]
    lines += [f"- {note}" for note in report.notes]
    for item in report.analyses:
        if item.note:
            lines.append(f"- `{item.metric}`：{item.note}")
    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m memory_agent.eval.harness",
        description="跑确定性场景集 → 按 stop 的正确率 + bootstrap CI（零网络、零模型权重）",
    )
    parser.add_argument("scenarios", nargs="?", default=None,
                        help="场景集 JSON（默认包内 scenarios.json）")
    parser.add_argument("--out", default=None, help="把同一份报告字节写到该路径")
    parser.add_argument("--format", choices=("json", "md"), default="json")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    args = _build_parser().parse_args(argv)
    try:
        report = build_report(args.scenarios)
    except scenario_set.ScenarioSetError as exc:
        print(f"场景集不合规：{exc}", file=sys.stderr)
        return 2
    rendered = report.to_json() if args.format == "json" else markdown(report)
    stdout_text = rendered if rendered.endswith("\n") else rendered + "\n"
    if args.out:
        # 落盘与 stdout **逐字节相同**（含结尾换行）——确定性锚点要能对拍。
        Path(args.out).write_text(stdout_text, encoding="utf-8")
    sys.stdout.write(stdout_text)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI 冒烟
    raise SystemExit(main())
