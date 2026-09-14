"""#19：MCP 服务默认不预热嵌入模型。

BGE-M3 常驻约 3.9GB 私有内存，而每个 opencode 会话都会拉起一份 MCP——无条件预热
等于每会话白付 3.9GB（曾把系统 commit 打满，见 issue #19）。本测试锁住"默认惰性"。
"""
import time

from memory_agent import mcp_server
from memory_agent.settings import warmup_on_start


def test_warmup_defaults_to_off(monkeypatch):
    monkeypatch.delenv("MEMORY_WARMUP", raising=False)
    assert warmup_on_start() is False


def test_warmup_on_for_truthy_values(monkeypatch):
    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("MEMORY_WARMUP", value)
        assert warmup_on_start() is True


def test_warmup_off_for_falsy_values(monkeypatch):
    for value in ("0", "false", "no", ""):
        monkeypatch.setenv("MEMORY_WARMUP", value)
        assert warmup_on_start() is False


def test_disabled_start_does_not_call_warmup(monkeypatch):
    called = []
    monkeypatch.setattr(mcp_server, "_warmup", lambda: called.append(1))

    assert mcp_server._start_warmup(enabled=False) is False
    assert called == []


def test_enabled_start_runs_warmup_in_background(monkeypatch):
    called = []
    monkeypatch.setattr(mcp_server, "_warmup", lambda: called.append(1))

    assert mcp_server._start_warmup(enabled=True) is True
    for _ in range(100):
        if called:
            break
        time.sleep(0.01)
    assert called == [1]
