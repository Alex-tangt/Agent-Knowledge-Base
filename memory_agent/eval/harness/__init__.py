"""检索 agent 评测 harness（ADR-0030 D7）——只消费运行时留下的 `trace`。

| 模块 | 作用 |
|---|---|
| `runner` | in-process 驱动 `AgentLoop` 跑场景，产出/落盘 trace |
| `scorer` | trace × 评测集 → 按 stop 分类的正确率 + gold 覆盖筛查 |
| `stats`  | bootstrap CI（纯计算） |
| `replay` | 确定性 replay（回放录下的模型输出，不调模型） |

本包可 import 运行时；**运行时不得 import 本包**（守卫见
`tests/unit/test_agent_loop_isolation.py`）。
"""
from memory_agent.eval.harness.replay import ReplayLLM, budget_from_trace, replay, traces_equal
from memory_agent.eval.harness.runner import Runner
from memory_agent.eval.harness.scorer import answer_matches, evaluate, score_trace
from memory_agent.eval.harness.stats import bootstrap_ci, paired_diffs

__all__ = [
    "ReplayLLM",
    "Runner",
    "answer_matches",
    "bootstrap_ci",
    "budget_from_trace",
    "evaluate",
    "paired_diffs",
    "replay",
    "score_trace",
    "traces_equal",
]
