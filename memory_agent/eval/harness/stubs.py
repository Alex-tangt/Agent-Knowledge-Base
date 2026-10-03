"""harness：**确定性 stub**（脚本 LLM + 回放工具）——场景路径零网络、零模型权重。

为什么放在包里而不是 `tests/unit/`：包内评测要能在**没有测试目录**的情况下自跑
（`python -m memory_agent.eval.harness`），而包**不得**反向 import `tests/`
（`tests/unit/test_agent_loop_harness.py` 直接从本模块取用，不重复实现假件）。

确定性来自两处：
- `ScriptedLLM` 只回放 `scenario["script"]` 里写死的字符串，**不采样**；
- `StubTools` 只把 `scenario["stub_evidence"]` 的 id 列表映射成固定
  `{"id", "title", "snippet"}` 命中，**不打分、不排序、不随机**。
"""
from __future__ import annotations

from dataclasses import fields
from typing import Any, Iterable

from memory_agent.agent_loop.budget import Budget
from memory_agent.agent_loop.tools import SEARCH_TOOL

#: 轮数预算的**两种键名**：`max_hops`（旧）→ `max_rounds`（现契约，ADR-0030 D2 改名）。
#: harness 在改名前后都必须能跑，所以按 dataclass 实际字段名选，不写死。
_ROUNDS_KEYS = ("max_rounds", "max_hops")


def budget_keywords(*, max_rounds: int, max_evidence: int) -> dict[str, int]:
    """构造 `Budget` 关键字：按 `Budget` 的**实际字段名**挑「总轮数」的名字。

    这样并行会话把 `max_hops` 改名成 `max_rounds`（或反过来）时，harness 不需要跟着改。
    """
    names = {field_.name for field_ in fields(Budget)}
    for key in _ROUNDS_KEYS:
        if key in names:
            return {key: int(max_rounds), "max_evidence": int(max_evidence)}
    raise RuntimeError(f"Budget 既没有 {_ROUNDS_KEYS[0]} 也没有 {_ROUNDS_KEYS[1]}：{sorted(names)}")


def make_budget(*, max_rounds: int = 3, max_evidence: int = 20) -> Budget:
    return Budget(**budget_keywords(max_rounds=max_rounds, max_evidence=max_evidence))


class ScriptedLLM:
    """按脚本逐次返回；用尽后**重复最后一条**。

    「重复最后一条」与运行时单测里的假 LLM 同义：用满预算的场景要靠它继续申请下一跳，
    否则预算分支永远走不到。脚本非空由 `scenarios.validate_scenarios` 保证。
    """

    def __init__(self, outputs: Iterable[str]):
        self.responses = [str(item) for item in outputs]
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages, **kwargs) -> str:
        self.calls.append(messages)
        if not self.responses:
            raise RuntimeError("ScriptedLLM 脚本为空：场景缺少 script")
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


def hit(entry_id: str) -> dict[str, str]:
    """固定命中形状（与 `MemoryToolRegistry` / `memory_search` 的字段一致）。"""
    return {"id": entry_id, "title": entry_id, "snippet": f"snip {entry_id}"}


class StubTools:
    """按 query 回放固定命中；未登记的 query 返回空列表（= 召回不到）。

    与 `MemoryToolRegistry` 同端口（`list_tools` / `call`），因此 `Runner` / `AgentLoop`
    不需要知道它是假的。
    """

    def __init__(self, by_query: dict[str, list[str]] | None = None):
        self.by_query = {str(key): list(value or []) for key, value in (by_query or {}).items()}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_tools(self) -> list[dict[str, Any]]:
        return [{"name": SEARCH_TOOL, "description": "stub：按 query 回放固定命中"}]

    def call(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, dict(args)))
        if name != SEARCH_TOOL:
            raise KeyError(f"未知工具：{name}")
        return [hit(entry_id) for entry_id in self.by_query.get(str(args.get("query")), [])]


def stub_llm(scenario: dict[str, Any]) -> ScriptedLLM:
    """场景 → 脚本 LLM（回放 `scenario["script"]`）。"""
    return ScriptedLLM(scenario.get("script") or [])


def stub_tools(scenario: dict[str, Any]) -> StubTools:
    """场景 → 回放工具（映射来自 `scenario["stub_evidence"]`，不是直接收 `{query: ids}`）。"""
    return StubTools(scenario.get("stub_evidence") or {})

def stub_tools_factory(scenario: dict[str, Any]) -> StubTools:
    """`Runner(tools_factory=...)` 用的工厂（每个场景现造，互不串状态）。"""
    return stub_tools(scenario)


def stub_llm_factory(scenario: dict[str, Any]) -> ScriptedLLM:
    return stub_llm(scenario)


def budget_for(scenario: dict[str, Any], *, defaults: dict[str, Any] | None = None,
               default_hops: int = 3, default_evidence: int = 20) -> Budget:
    """场景级预算覆盖：`max_hops` / `max_evidence`，缺省回落到场景集 `defaults`。

    场景 JSON 里的键名保持 `max_hops`（场景集是**数据**，不随内部字段改名而改），
    这里翻译成 `Budget` 当前实际使用的字段名。
    """
    fallback = dict(defaults or {})
    return make_budget(
        max_rounds=int(scenario.get("max_hops",
                                    fallback.get("max_hops", default_hops))),
        max_evidence=int(scenario.get("max_evidence",
                                      fallback.get("max_evidence", default_evidence))),
    )


__all__ = [
    "ScriptedLLM",
    "StubTools",
    "budget_for",
    "budget_keywords",
    "hit",
    "make_budget",
    "stub_llm",
    "stub_llm_factory",
    "stub_tools",
    "stub_tools_factory",
]
