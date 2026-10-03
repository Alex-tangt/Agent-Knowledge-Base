"""检索 agent 评测 harness（ADR-0030 D7）——只消费运行时留下的 `trace`。

| 模块 | 作用 |
|---|---|
| `runner` | in-process 驱动 `AgentLoop` 跑场景，产出/落盘 trace |
| `scorer` | trace × 评测集 → 按 stop 分类的正确率 + gold 覆盖筛查 |
| `stats`  | bootstrap CI（纯计算） |
| `replay` | 确定性 replay（回放录下的模型输出，不调模型） |
| `stubs`  | 确定性 stub（脚本 LLM + 回放工具）——零网络、零模型权重 |
| `scenarios` | 场景集加载 / schema 校验 / 留出（dev·holdout）切分规则 |
| `__main__` | 命令入口：跑场景集 → 按 stop 的正确率 + bootstrap CI |

本包可 import 运行时；**运行时不得 import 本包**（守卫见
`tests/unit/test_agent_loop_isolation.py`）。
"""
from memory_agent.eval.harness.replay import ReplayLLM, budget_from_trace, replay, traces_equal
from memory_agent.eval.harness.runner import Runner
from memory_agent.eval.harness.scenarios import (
    OUTCOME_CLASSES,
    SCENARIO_SET_VERSION,
    ScenarioSetError,
    dev_ids,
    eval_set,
    holdout_ids,
    load_scenarios,
    split_scenarios,
    validate_file,
    validate_scenarios,
)
from memory_agent.eval.harness.scorer import answer_matches, evaluate, score_trace
from memory_agent.eval.harness.stats import bootstrap_ci, paired_diffs
from memory_agent.eval.harness.stubs import (
    ScriptedLLM,
    StubTools,
    budget_for,
    budget_keywords,
    hit,
    make_budget,
    stub_llm,
    stub_llm_factory,
    stub_tools,
    stub_tools_factory,
)

__all__ = [
    "OUTCOME_CLASSES",
    "ReplayLLM",
    "Runner",
    "SCENARIO_SET_VERSION",
    "ScenarioSetError",
    "ScriptedLLM",
    "StubTools",
    "answer_matches",
    "bootstrap_ci",
    "budget_for",
    "budget_from_trace",
    "budget_keywords",
    "dev_ids",
    "eval_set",
    "evaluate",
    "hit",
    "holdout_ids",
    "load_scenarios",
    "make_budget",
    "paired_diffs",
    "replay",
    "score_trace",
    "split_scenarios",
    "stub_llm",
    "stub_llm_factory",
    "stub_tools",
    "stub_tools_factory",
    "traces_equal",
    "validate_file",
    "validate_scenarios",
]
