"""检索 agent 的**代码控循环运行时**（ADR-0030）。

- 只依赖端口（`LLMClient` / `ToolRegistry` / `SufficiencyChecker`），不依赖具体实现。
- 只**写** `Trace`（契约见 `memory_agent.trace`）；**不 import 评测**。

评测侧在 `memory_agent/eval/harness`（消费 trace），二者经 trace 契约解耦。
"""
from memory_agent.agent_loop.budget import Budget
from memory_agent.agent_loop.llm import (
    OpenAICompatClient,
    OpencodeServerClient,
    ProviderSpec,
    resolve_llm_client,
    resolve_provider,
)
from memory_agent.agent_loop.loop import AgentLoop, Decision, ModelDeclaredSufficiency, parse_decision
from memory_agent.agent_loop.ports import LLMClient, SufficiencyChecker, ToolRegistry
from memory_agent.agent_loop.tools import (
    NAV_TOOLS,
    MemoryNavToolRegistry,
    MemoryToolRegistry,
)

__all__ = [
    "AgentLoop",
    "Budget",
    "Decision",
    "LLMClient",
    "MemoryNavToolRegistry",
    "MemoryToolRegistry",
    "NAV_TOOLS",
    "ModelDeclaredSufficiency",
    "OpenAICompatClient",
    "OpencodeServerClient",
    "ProviderSpec",
    "SufficiencyChecker",
    "ToolRegistry",
    "parse_decision",
    "resolve_llm_client",
    "resolve_provider",
]
