"""agent_loop 的端口（端口-适配器）：运行时只依赖这些协议，不依赖任何具体实现。

- `LLMClient`：纯文本生成（**不执行工具**）。
- `ToolRegistry`：列工具 + 调用。
- `SufficiencyChecker`：**充分性接缝**——把"够不够"从模型自评里抽出来，可替换。
  （ADR-0030：live 用流程约束；生产可换成独立校验器，而不改循环。）
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        """给消息列表（OpenAI 风格），回一段文本。"""
        ...


@runtime_checkable
class ToolRegistry(Protocol):
    def list_tools(self) -> list[dict[str, Any]]:
        ...

    def call(self, name: str, args: dict[str, Any]) -> Any:
        ...


@runtime_checkable
class SufficiencyChecker(Protocol):
    def sufficient(self, *, question: str, evidence: list[dict[str, Any]],
                   model_output: str) -> bool:
        """判断已有证据是否足以作答。"""
        ...
