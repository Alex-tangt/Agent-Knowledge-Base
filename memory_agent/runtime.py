"""memory_agent 运行时：先立 stdio 安全日志，再装配索引。

import 顺序有讲究：configure_stderr_logging() 必须在 import ragcore 之前执行
（ragcore 的 logger 会把 root handler 抢配到 stdout，腐蚀 stdio 协议通道）。
"""
from __future__ import annotations

from memory_agent import _bootstrap

_bootstrap.configure_stderr_logging()
_bootstrap.ensure_ragcore_on_path()

from memory_agent.memory.index import MemoryIndex  # noqa: E402
from memory_agent.memory.reindex import Reindexer  # noqa: E402
from memory_agent.memory.writer import MemoryWriter  # noqa: E402

_index: MemoryIndex | None = None
_writer: MemoryWriter | None = None


def build_index() -> dict:
    """从 Markdown 真相源全量重建（新代 + 原子切指针），返回统计。

    CLI 入口用；服务端分块重建走 `memory_reindex`。
    """
    return Reindexer().run_all()


def reindex(cursor: dict | None = None, batch: int = 16) -> dict:
    """分块全量重建一步；cursor=None 开始新一轮，返回 cursor 续调。"""
    return Reindexer().reindex(cursor=cursor, batch=batch)


def get_index() -> MemoryIndex:
    """取索引单例（跟随指针指向的当前代）；未构建时抛出可执行的提示。"""
    global _index
    if _index is None:
        index = MemoryIndex()
        if not index.is_built:
            raise RuntimeError(
                "记忆索引尚未构建：请先运行 "
                "venv\\Scripts\\python.exe memory_agent/build_index.py"
            )
        _index = index
    return _index


def reset_index() -> None:
    """测试用：丢弃单例。"""
    global _index
    _index = None


def get_writer() -> MemoryWriter:
    """取写入网关单例（复用索引做去重检索）。"""
    global _writer
    if _writer is None:
        _writer = MemoryWriter(get_index())
    return _writer


def reset_writer() -> None:
    """测试用：丢弃写入网关单例。"""
    global _writer
    _writer = None
