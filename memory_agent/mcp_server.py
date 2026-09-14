"""stdio MCP server：读路径最小闭环（issue #10）。

暴露 memory_search / memory_get。真相源是 Markdown；索引是派生物。
opencode 接入（opencode.json）：
    "memory-agent": {
      "type": "local",
      "command": ["<repo>/venv/Scripts/python.exe", "<repo>/memory_agent/mcp_server.py"],
      "enabled": true
    }
首次使用前先建索引：`venv\\Scripts\\python.exe memory_agent/build_index.py`。
"""
from __future__ import annotations

import os
import sys
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _HERE]
sys.path.insert(0, _ROOT)

from memory_agent.runtime import get_index  # noqa: E402  (导入即配 stderr 日志 + ragcore 路径)

from mcp.server import MCPServer  # noqa: E402
from utils.logger import logger  # noqa: E402

mcp = MCPServer(
    "memory-agent",
    version="0.1.0",
    instructions=(
        "Agent 长期记忆读路径：memory_search 语义检索条目，memory_get 读回真实 Markdown。"
        "只读语料（writable=false）不可写入。"
    ),
)


@mcp.tool()
def memory_search(query: str, k: int = 5, writable_only: bool = False) -> list[dict]:
    """语义检索长期记忆条目（可写 KB 记忆 + 只读项目语料）。

    返回条目级命中：id / title / source / writable / type / tags / status / score / snippet。
    score 是余弦相似度（越大越相关）。用 memory_get(id) 读回完整 Markdown。
    writable=false 的是只读参考语料，不可写入。
    """
    return get_index().search(query, k=k, writable_only=writable_only)


@mcp.tool()
def memory_get(entry_id: str) -> dict:
    """按 memory_search 返回的 id，读回该条目的真实 Markdown 内容与元数据。"""
    try:
        return get_index().get(entry_id)
    except KeyError:
        raise ValueError(f"未知条目 id：{entry_id}（先用 memory_search 取 id）")
    except FileNotFoundError as exc:
        raise ValueError(f"条目文件已不存在（索引孤儿，需重建）：{exc}")


def _warmup() -> None:
    try:
        index = get_index()
        index.store.warmup()
        logger.info("memory index warmup done")
    except Exception as exc:  # noqa: BLE001 - 预热失败不阻断服务
        logger.warning(f"memory index warmup failed: {exc}")


def main() -> None:
    threading.Thread(target=_warmup, name="memory-warmup", daemon=True).start()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
