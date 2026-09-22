"""harness：确定性 replay——用 trace 里录下的 `model_output` 重跑，**不调模型**。

用途：验证"给定同一份模型输出 + 同一份工具，轨迹逐位复现"（确定性锚点）。
**反事实续跳**（在停点强制再跑一跳以度量 regret）是后续扩展：它需要真实/钉定模型，
属**离线、抽样、"停了∧错"子集**（ADR-0030 D7），本模块先不实现。
"""
from __future__ import annotations

from typing import Any

from memory_agent.agent_loop import AgentLoop, Budget
from memory_agent.trace import Trace


class ReplayLLM:
    """按序回放已录的 `model_output`；用尽即报错（说明 trace 与循环不一致）。"""

    def __init__(self, outputs: list[str]):
        self._outputs = list(outputs)

    def complete(self, messages, **kwargs) -> str:
        if not self._outputs:
            raise RuntimeError("replay 输出已用尽：trace 与循环参数不一致？")
        return self._outputs.pop(0)


def budget_from_trace(trace: Trace) -> Budget:
    meta = trace.meta or {}
    return Budget(max_hops=int(meta.get("max_hops", 3)),
                  max_evidence=int(meta.get("max_evidence", 20)))


def replay(trace: Trace, tools: Any) -> Trace:
    """用录下的输出 + 给定工具，确定性重跑同一条查询。"""
    outputs = [round_.model_output for round_ in trace.rounds]
    loop = AgentLoop(
        ReplayLLM(outputs), tools,
        budget=budget_from_trace(trace), k=int((trace.meta or {}).get("k", 5)),
    )
    return loop.run(trace.query, trace_id=trace.id)


def traces_equal(a: Trace, b: Trace) -> bool:
    """逐位比较（忽略 `meta`，因预算可能由不同默认构造）。"""
    return a.query == b.query and a.rounds == b.rounds and a.stop == b.stop and a.final == b.final
