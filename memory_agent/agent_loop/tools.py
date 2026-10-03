"""工具适配：把记忆检索暴露成 `ToolRegistry`。

**个人模式**直接走进程内 `MemoryIndex`（快、无网络）；工具只读、不改检索合成。
（MCP / 网关适配是后续形态，端口不变。）
"""
from __future__ import annotations

from typing import Any

SEARCH_TOOL = "memory_search"
GET_TOOL = "memory_get"


class MemoryToolRegistry:
    """`memory_search` / `memory_get` 的只读适配器。"""

    def __init__(self, index, *, exclude_retired: bool = False):
        self._index = index
        self._exclude_retired = exclude_retired

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": SEARCH_TOOL,
                "description": "语义检索记忆条目（可写 KB + 只读语料）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "k": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": GET_TOOL,
                "description": "按 id 读回条目真实 Markdown。",
                "parameters": {
                    "type": "object",
                    "properties": {"entry_id": {"type": "string"}},
                    "required": ["entry_id"],
                },
            },
        ]

    def call(self, name: str, args: dict[str, Any]) -> Any:
        if name == SEARCH_TOOL:
            return self._index.search(
                args["query"],
                k=int(args.get("k", 5)),
                exclude_retired=self._exclude_retired,
            )
        if name == GET_TOOL:
            return self._index.get(args["entry_id"])
        raise KeyError(f"未知工具：{name}")


def describe_tools(registry) -> str:
    """把注册表渲染成给模型看的工具目录（ACI：名称 + 参数 + 描述）。

    参数名后带 `*` = 必填。`AgentLoop` 每轮把它放进 prompt——注册的工具因此**可达**
    （此前 `list_tools` / `memory_get` 无人调用）。
    """
    lines: list[str] = []
    for tool in registry.list_tools():
        schema = tool.get("parameters") or {}
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        signature = ", ".join(
            f"{name}{'*' if name in required else ''}" for name in properties
        )
        lines.append(f"- {tool.get('name')}({signature})：{tool.get('description', '')}")
    return "\n".join(lines) or "- (无)"
