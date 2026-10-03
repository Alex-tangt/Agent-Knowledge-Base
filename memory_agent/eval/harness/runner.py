"""harness：in-process 驱动 `AgentLoop` 跑场景集，产出 trace（ADR-0030 D7）。

评测侧只**消费**运行时；运行时不知道评测存在。`trace_path` 落盘后即可被 `scorer` /
`replay` 离线消费。
"""
from __future__ import annotations

from typing import Any, Callable

from memory_agent.agent_loop import AgentLoop, Budget
from memory_agent.trace import Trace, append_trace


class Runner:
    """场景 → trace。`tools_factory(scenario)` 每个场景现造工具（隔离用）。"""

    def __init__(self, llm, tools_factory: Callable[[dict], Any], *,
                 budget: Budget | None = None, sufficiency=None, k: int = 5):
        self.llm = llm
        self.tools_factory = tools_factory
        self.budget = budget
        self.sufficiency = sufficiency
        self.k = k

    def run(self, scenarios: list[dict], *, trace_path: str | None = None) -> list[Trace]:
        traces: list[Trace] = []
        for scenario in scenarios:
            loop = AgentLoop(
                self.llm, self.tools_factory(scenario),
                budget=self.budget, sufficiency=self.sufficiency, k=self.k,
            )
            trace = loop.run(scenario["query"], trace_id=scenario.get("id"))
            traces.append(trace)
            if trace_path:
                append_trace(trace_path, trace)
        return traces
