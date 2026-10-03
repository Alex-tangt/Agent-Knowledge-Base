"""harness：**确定性场景评测集**的加载 / 校验 / 留出切分（#62 验收②）。

## 为什么是 JSON 而不是 `.py` 模块

数据面（10 条场景）与代码面（加载 / 校验）分开：JSON 是**纯数据**，无 import 副作用、
可被非 Python 消费者读、改一条场景不碰任何逻辑，评审时 diff 只显示数据变化。
代价 = 没有注释，因此约定用 `note` 字段 + 本 docstring 承载口径，并把**全部校验**放进
`validate_scenarios`（schema 错了就报错，不静默跑）。场景集只有一张表，不值得为它写 Python DSL。

## 口径（沿 ADR-0030 D7 / CONTEXT.md，不另造词）

- **迭代检索**：据上一跳结果判断够不够，不足则据已有信息**改写 query** 再检索，
  硬预算 / 无新 id / 充分性信号即停。**由模型执行**，本包只提供确定性信号。
- **多跳**：问题属性（gold 证据跨多篇），**不等于**需要迭代检索。
- **充分性**：`Trace.stop.trigger == "insufficient"` 即模型明说证据不足（必须**不**作答）。

场景字段：
`{id, query, split, kind, relevant, gold_answer?, stub_evidence, script, max_hops?,
  max_evidence?, expect?}`——
`stub_evidence` 让 `stubs.StubTools` 回放确定性命中，`script` 让 `stubs.ScriptedLLM`
回放模型输出；两者合起来使整条路径**零网络、零模型权重、逐位可复现**。
`expect.stop`（可选）声明该场景**该**落到的停止触发词（`memory_agent.trace.STOP_TRIGGERS`），
runner 报告里据此给出 `expectation_match_rate`。

## 留出（holdout）规则

`split` 是**显式字段**（不做派生，避免"切分逻辑改了、基线跟着漂"）。**规则**：

- `dev` = 可以随便调参 / 人眼观察 / 改场景的地方；
- `holdout` = **调参期间不许看细节**，对外报的数**只能**是 holdout 上的数；
- 任何会让 holdout 分数变好的改动（改 prompt / 预算 / 打分口径）都必须先在 `dev` 上定，
  然后**只跑一次** holdout 记录结论；之后若要再调，必须新造 holdout 场景或换 split。

`holdout_ids(scenarios)` 每次调用都从 `split` 现算，因此"报数用哪一批"不依赖目录顺序。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from memory_agent.trace import STOP_TRIGGERS

SCENARIO_SET_VERSION = 1
DEFAULT_SCENARIO_PATH = Path(__file__).with_name("scenarios.json")

#: 场景 kind：answer = 有 gold 答案（`gold_answer` 必填）；no_answer = 知识库里没有答案，
#: 正确行为是**不做答**（`final.answer is None`）。
KINDS = ("answer", "no_answer")

#: 场景该覆盖的 **停止 / 结局类别**（验收②点名的那几类）。
OUTCOME_CLASSES = (
    "answer_on_first_hop",
    "multihop",
    "no_new_ids",
    "budget",
    "fallback",
    "insufficient_no_answer",
    "gold_unreached_screening",
)

#: id 前缀 → 该场景声称覆盖的类别（机器可校验，注释不算）。
CLASS_PREFIXES = {
    "s01-": "answer_on_first_hop",
    "s02-": "multihop",
    "s03-": "multihop",
    "s04-": "budget",
    "s05-": "budget",
    "s06-": "no_new_ids",
    "s07-": "fallback",
    "s08-": "insufficient_no_answer",
    "s09-": "gold_unreached_screening",
    "s10-": "gold_unreached_screening",
}
for _prefix in CLASS_PREFIXES:
    # 前缀必须真的对上某个类别；类别名不许拼错。
    assert CLASS_PREFIXES[_prefix] in OUTCOME_CLASSES, f"未知类别：{CLASS_PREFIXES[_prefix]}"

_REQUIRED_FIELDS = ("id", "query", "split", "kind", "relevant", "stub_evidence", "script")
_OPTIONAL_FIELDS = ("gold_answer", "max_hops", "max_evidence", "expect", "note")


class ScenarioSetError(ValueError):
    """场景集不合规（缺字段 / 重复 id / 非法 split / 非法停止触发词 …）。"""


def load_scenarios(path: str | Path | None = None) -> dict[str, Any]:
    """读场景集 JSON；默认读包内 `scenarios.json`。"""
    target = Path(path) if path is not None else DEFAULT_SCENARIO_PATH
    if not target.is_file():
        raise ScenarioSetError(f"场景集不存在：{target}")
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:  # 明确报错，不静默跳过
        raise ScenarioSetError(f"场景集不是合法 JSON：{target}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ScenarioSetError(f"场景集根节点必须是对象：{target}")
    return payload


def _as_id_list(value: Any, where: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        errors.append(f"{where} 必须是字符串列表")
        return []
    if len(set(value)) != len(value):
        errors.append(f"{where} 有重复项")
    return list(value)


def validate_scenarios(payload: dict[str, Any]) -> list[str]:
    """校验并返回**全部**错误（空列表 = 合规）。不抛异常，便于测试逐条断言。"""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["场景集根节点必须是对象"]

    version = payload.get("schema_version")
    if version != SCENARIO_SET_VERSION:
        errors.append(f"schema_version 必须是 {SCENARIO_SET_VERSION}，实际 {version!r}")

    defaults = payload.get("defaults") or {}
    for key in ("k", "max_hops", "max_evidence"):
        if key in defaults and not (isinstance(defaults[key], int) and defaults[key] >= 1):
            errors.append(f"defaults.{key} 必须是 >=1 的整数")

    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        return errors + ["scenarios 必须是非空列表"]

    seen_ids: set[str] = set()
    seen_queries: dict[str, str] = {}
    seen_splits: set[str] = set()
    seen_classes: set[str] = set()
    for index, scenario in enumerate(scenarios):
        where = f"scenarios[{index}]"
        if not isinstance(scenario, dict):
            errors.append(f"{where} 必须是对象")
            continue
        identifier = scenario.get("id")
        where = f"scenarios[{index}]({identifier})"

        for field in _REQUIRED_FIELDS:
            if field not in scenario:
                errors.append(f"{where} 缺必填字段 {field}")
        for field in scenario:
            if field not in _REQUIRED_FIELDS and field not in _OPTIONAL_FIELDS:
                errors.append(f"{where} 有未知字段 {field}")

        if not isinstance(identifier, str) or not identifier:
            errors.append(f"scenarios[{index}] 的 id 必须是非空字符串")
        elif identifier in seen_ids:
            errors.append(f"{where} id 重复")
        else:
            seen_ids.add(identifier)

        query = scenario.get("query")
        if not isinstance(query, str) or not query.strip():
            errors.append(f"{where} query 必须是非空字符串")
        elif query in seen_queries:
            errors.append(f"{where} query 与 {seen_queries[query]} 重复（stub 按 query 索引，会串）")
        else:
            seen_queries[query] = identifier

        split = scenario.get("split")
        if not isinstance(split, str) or not split.strip():
            errors.append(f"{where} split 必须是非空字符串")
        else:
            seen_splits.add(split)

        kind = scenario.get("kind")
        if kind not in KINDS:
            errors.append(f"{where} kind 必须是 {KINDS} 之一，实际 {kind!r}")

        relevant = _as_id_list(scenario.get("relevant"), f"{where}.relevant", errors)
        if kind == "answer" and not relevant:
            errors.append(f"{where} kind=answer 必须有非空 relevant")
        if kind == "no_answer" and relevant:
            errors.append(f"{where} kind=no_answer 的 relevant 必须为空（gold 覆盖只对有答案题计）")

        gold = scenario.get("gold_answer")
        if kind == "answer":
            if not isinstance(gold, str) or not gold.strip():
                errors.append(f"{where} kind=answer 必须有非空 gold_answer")
        elif gold is not None:
            errors.append(f"{where} kind=no_answer 不该有 gold_answer")

        evidence = scenario.get("stub_evidence")
        if not isinstance(evidence, dict) or not evidence:
            errors.append(f"{where} stub_evidence 必须是非空对象")
        else:
            for key, ids in evidence.items():
                if not isinstance(key, str) or not key.strip():
                    errors.append(f"{where} stub_evidence 的 key 必须是非空字符串")
                _as_id_list(ids, f"{where}.stub_evidence[{key!r}]", errors)
            if isinstance(query, str) and query not in evidence:
                errors.append(f"{where} stub_evidence 缺首跳 query 的登记（首跳会召回空）")

        script = scenario.get("script")
        if not isinstance(script, list) or not script:
            errors.append(f"{where} script 必须是非空字符串列表")
        elif not all(isinstance(item, str) for item in script):
            errors.append(f"{where} script 每项必须是字符串")
        # 注意：script 比预算长是**允许**的——`ScriptedLLM` 用尽后重复最后一条，
        # 「用满预算」的场景正要靠它继续申请下一跳。

        for key in ("max_hops", "max_evidence"):
            if key in scenario and not (isinstance(scenario[key], int) and scenario[key] >= 1):
                errors.append(f"{where}.{key} 必须是 >=1 的整数")

        expect = scenario.get("expect")
        if expect is not None:
            if not isinstance(expect, dict) or set(expect) != {"stop"}:
                errors.append(f"{where}.expect 必须是 {{'stop': <触发词>}}")
            elif expect["stop"] not in STOP_TRIGGERS:
                errors.append(
                    f"{where}.expect.stop 不是合法停止触发词：{expect['stop']!r}；"
                    f"合法值 {STOP_TRIGGERS}"
                )

        if isinstance(identifier, str):
            covered = next((name for prefix, name in CLASS_PREFIXES.items()
                            if identifier.startswith(prefix)), None)
            if covered is None:
                errors.append(f"{where} id 前缀不覆盖任何验收②类别（见 CLASS_PREFIXES）")
            else:
                seen_classes.add(covered)

    missing = [name for name in OUTCOME_CLASSES if name not in seen_classes]
    if missing:
        errors.append(f"场景集未覆盖这些结局类别：{missing}")
    if len(seen_splits) < 2:
        errors.append(f"split 必须至少两个（dev + holdout），实际 {sorted(seen_splits)}")
    if "holdout" not in seen_splits:
        errors.append("split 里必须有 holdout（对外报数只能报它）")
    return errors


def split_scenarios(scenarios: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按 `split` 字段分组（顺序 = 文件顺序，保持确定）。"""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for scenario in scenarios:
        grouped.setdefault(str(scenario.get("split")), []).append(scenario)
    return grouped


def holdout_ids(scenarios: list[dict[str, Any]]) -> list[str]:
    return [scenario["id"] for scenario in scenarios if scenario.get("split") == "holdout"]


def dev_ids(scenarios: list[dict[str, Any]]) -> list[str]:
    return [scenario["id"] for scenario in scenarios if scenario.get("split") == "dev"]


def eval_set(scenarios: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """`scorer.evaluate` 要的 `{id: gold}`；只保留打分用得到的字段（确定性）。"""
    out: dict[str, dict[str, Any]] = {}
    for scenario in scenarios:
        out[str(scenario["id"])] = {
            "kind": scenario.get("kind"),
            "relevant": list(scenario.get("relevant") or []),
            "gold_answer": scenario.get("gold_answer"),
        }
    return out


def validate_file(path: str | Path | None = None) -> dict[str, Any]:
    """读 + 校验，返回 payload；不合规抛 `ScenarioSetError`。"""
    payload = load_scenarios(path)
    errors = validate_scenarios(payload)
    if errors:
        raise ScenarioSetError("；".join(errors))
    return payload


__all__ = [
    "CLASS_PREFIXES",
    "DEFAULT_SCENARIO_PATH",
    "KINDS",
    "OUTCOME_CLASSES",
    "SCENARIO_SET_VERSION",
    "ScenarioSetError",
    "dev_ids",
    "eval_set",
    "holdout_ids",
    "load_scenarios",
    "split_scenarios",
    "validate_file",
    "validate_scenarios",
]
