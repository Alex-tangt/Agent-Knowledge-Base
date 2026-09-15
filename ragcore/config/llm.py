"""LLM 层配置：API 凭证 + 模型名 + 可选 LangSmith 追踪。

与 core 层（`config/config.py`）分开的理由（issue #22 / ADR-0016）：core 路径
（`memory_agent` 的构建 / 检索 / 写入）不需要 LLM，若 import 即校验凭证，就会把
「没有 `legal_web/.env`」的进程整条挡在门外——那正是独立发布（目的地 a）的前置。

契约：
- 本层**惰性**：只有真正要构造 LLM client 时才 `load_llm_env()` + `require_llm()`。
- 加载的是**适配层自己的** `legal_web/.env`（显式锚定，不依赖 CWD），
  `override=False` → 已存在的进程环境变量优先。
- 只由适配层 `legal_web` 触发；`memory_agent` 走 core 层，**不 import 本模块**、
  **不读 `legal_web/.env`**（见 `memory_agent/settings.py` 的独立 `MEMORY_*` 加载器）。
- 凭证值绝不写进日志 / 异常：校验失败只报变量名，不报值。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from ragcore.config.config import LEGAL_WEB_DIR

ENV_FILE = os.path.join(LEGAL_WEB_DIR, ".env")

_env_loaded = False


def load_llm_env() -> bool:
    """加载 `legal_web/.env`（幂等、进程环境优先）。返回是否真的读了文件。"""
    global _env_loaded
    if _env_loaded:
        return False
    _env_loaded = True
    if not os.path.isfile(ENV_FILE):
        return False
    return load_dotenv(ENV_FILE, override=False)


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str
    model: str


def require_llm() -> LLMConfig:
    """加载 .env 并校验 LLM 三项；缺任一抛 `ValueError`（显式失败，不静默后移）。"""
    load_llm_env()
    api_key = os.getenv("API_KEY")
    if not api_key:
        raise ValueError("API_KEY环境变量未设置！请检查.env文件配置")
    base_url = os.getenv("BASE_URL")
    if not base_url:
        raise ValueError("BASE_URL环境变量未设置！请检查.env文件配置")
    model = os.getenv("Model")
    if not model:
        raise ValueError("MODEL环境变量未设置！请检查.env文件配置")
    return LLMConfig(api_key=api_key, base_url=base_url, model=model)


def langsmith_settings() -> dict:
    """可选的 LangSmith 配置；只读进程环境（调用方负责先 `load_llm_env()`）。"""
    return {
        "api_key": os.getenv("LANGSMITH_API_KEY"),
        "project": os.getenv("LANGSMITH_PROJECT", "rag-knowledge-base"),
        "endpoint": os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"),
        "tracing": os.getenv("LANGSMITH_TRACING", "true").lower() == "true",
    }
