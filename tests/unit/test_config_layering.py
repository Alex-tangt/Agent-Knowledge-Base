"""config 分层（core/llm）+ memory_agent 独立 .env 加载器（#22 / ADR-0016）。

core 层不得含密钥、import 不得校验；llm 层惰性加载 `legal_web/.env` 并在缺凭证时
显式抛错；memory_agent 用 `MEMORY_*` 命名空间 + 自己的 `.env`，不读 `legal_web/.env`。
"""
import importlib
import os

import pytest

from config import config, llm
from memory_agent import settings


# --------------------------------------------------------------- core 层

def test_core_config_has_no_llm_secrets():
    for name in ("API_KEY", "BASE_URL", "MODEL", "LANGSMITH_API_KEY"):
        assert not hasattr(config, name), f"core 层不应导出 {name}"


def test_core_config_imports_without_llm_env(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("BASE_URL", raising=False)
    monkeypatch.delenv("Model", raising=False)
    module = importlib.reload(config)
    assert os.path.basename(module.LEGAL_WEB_DIR) == "legal_web"
    assert module.ARTICLE_MAX_CHARS == 800


# ----------------------------------------------------------------- llm 层

def _no_env_file(monkeypatch):
    """让 require_llm 只看进程环境（不读真实 legal_web/.env）。"""
    monkeypatch.setattr(llm, "_env_loaded", True)


def test_require_llm_raises_when_missing(monkeypatch):
    _no_env_file(monkeypatch)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("BASE_URL", raising=False)
    monkeypatch.delenv("Model", raising=False)
    with pytest.raises(ValueError):
        llm.require_llm()


def test_require_llm_returns_config(monkeypatch):
    _no_env_file(monkeypatch)
    monkeypatch.setenv("API_KEY", "k")
    monkeypatch.setenv("BASE_URL", "http://localhost")
    monkeypatch.setenv("Model", "m")
    cfg = llm.require_llm()
    assert (cfg.api_key, cfg.base_url, cfg.model) == ("k", "http://localhost", "m")


def test_require_llm_error_does_not_leak_value(monkeypatch):
    _no_env_file(monkeypatch)
    monkeypatch.setenv("API_KEY", "super-secret")
    monkeypatch.delenv("BASE_URL", raising=False)
    monkeypatch.delenv("Model", raising=False)
    with pytest.raises(ValueError) as excinfo:
        llm.require_llm()
    assert "super-secret" not in str(excinfo.value)


def test_langsmith_settings_defaults(monkeypatch):
    for name in ("LANGSMITH_API_KEY", "LANGSMITH_PROJECT",
                 "LANGSMITH_ENDPOINT", "LANGSMITH_TRACING"):
        monkeypatch.delenv(name, raising=False)
    settings_dict = llm.langsmith_settings()
    assert settings_dict["api_key"] is None
    assert settings_dict["project"] == "rag-knowledge-base"
    assert settings_dict["tracing"] is True


def test_load_llm_env_does_not_override_process_env(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("MEMORY_TEST_LLM_KEEP=from_file\n", encoding="utf-8")
    monkeypatch.setattr(llm, "_env_loaded", False)
    monkeypatch.setattr(llm, "ENV_FILE", str(env))
    monkeypatch.setenv("MEMORY_TEST_LLM_KEEP", "from_process")
    assert llm.load_llm_env() is True
    assert os.environ["MEMORY_TEST_LLM_KEEP"] == "from_process"


# --------------------------------------------------- memory_agent 独立 .env

def test_memory_env_file_is_not_legal_web():
    expected = os.environ.get("MEMORY_ENV_FILE") or os.path.join(
        settings.MEMORY_AGENT_DIR, ".env"
    )
    assert settings.ENV_FILE == expected
    assert "legal_web" not in settings.ENV_FILE


def test_load_env_file_reads_values(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("MEMORY_TEST_FROM_FILE=loaded\n", encoding="utf-8")
    monkeypatch.delenv("MEMORY_TEST_FROM_FILE", raising=False)
    assert settings.load_env_file(str(env)) is True
    assert os.environ["MEMORY_TEST_FROM_FILE"] == "loaded"


def test_load_env_file_process_env_wins(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("MEMORY_TEST_KEEP=from_file\n", encoding="utf-8")
    monkeypatch.setenv("MEMORY_TEST_KEEP", "from_process")
    settings.load_env_file(str(env))
    assert os.environ["MEMORY_TEST_KEEP"] == "from_process"


def test_load_env_file_missing_is_noop(tmp_path):
    assert settings.load_env_file(str(tmp_path / "does-not-exist.env")) is False


def test_settings_loads_env_at_import(monkeypatch, tmp_path):
    """import memory_agent.settings 时就应加载 .env（MEMORY_ENV_FILE 指向的）。"""
    env = tmp_path / ".env"
    env.write_text("MEMORY_TEST_IMPORT=yes\n", encoding="utf-8")
    monkeypatch.setenv("MEMORY_ENV_FILE", str(env))
    monkeypatch.delenv("MEMORY_TEST_IMPORT", raising=False)
    try:
        importlib.reload(settings)
        assert os.environ["MEMORY_TEST_IMPORT"] == "yes"
    finally:
        monkeypatch.delenv("MEMORY_ENV_FILE", raising=False)
        importlib.reload(settings)
