"""memory_agent 启动引导：stdio 安全日志。

**stdio MCP 的铁律**：stdout 是协议通道，任何日志写 stdout 都会腐蚀 JSON-RPC。
ragcore 的 logger 默认写 stdout，所以必须在 import ragcore 之前先把 root logger
配置到 stderr（logging.basicConfig 只在 root 无 handler 时生效，故先配者胜）。

`ragcore` 现在是真包（ADR-0024），不再需要 sys.path 垫片；`memory_agent` 依赖
安装好的 `ragcore`（`pip install -e ragcore -e memory_agent`）。
"""
from __future__ import annotations

import logging
import sys

_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def configure_stderr_logging() -> None:
    """把 root logger 抢配到 stderr。必须在 import ragcore 之前调用。"""
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT,
                            handlers=[logging.StreamHandler(sys.stderr)])
