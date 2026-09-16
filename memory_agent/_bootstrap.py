"""memory_agent 启动引导：stdio 安全日志 + HF 缓存感知离线。

**stdio MCP 的铁律**：stdout 是协议通道，任何日志写 stdout 都会腐蚀 JSON-RPC。
ragcore 的 logger 默认写 stdout，所以必须在 import ragcore 之前先把 root logger
配置到 stderr（logging.basicConfig 只在 root 无 handler 时生效，故先配者胜）。

**HF 离线（#46）**：`ensure_hf_offline()` 应在 import 任何 HF **之前**调一次，否则依赖链
（qdrant_client 等）会先把 `huggingface_hub` 拉进来，`HF_HUB_OFFLINE` 这个 import 期常量
就定死了。`_bootstrap` 是所有入口的最前处，故把这次调用收在这里。

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


def configure_hf_offline() -> bool:
    """在 import 任何 HF 之前切离线（缓存感知，issue #18 / #46）；返回是否离线。

    `ragcore` 的 import 放在函数内：先 `configure_stderr_logging()` 再调本函数，
    ragcore 的 logger 便不会把 handler 抢配到 stdout（保持 stdio 协议通道干净）。
    """
    from ragcore.config.hf import ensure_hf_offline

    return ensure_hf_offline()
