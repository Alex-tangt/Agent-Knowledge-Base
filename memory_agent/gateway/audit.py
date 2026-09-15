"""调用审计（#32 / ADR-0018 D2.3）：记录「谁调了什么」，最小可用。

- 进程内保留最近事件（测试 / 观测），可选追加到 JSONL 文件（daemon 用）。
- 事件只含**身份**（principal / tenant / role / ABAC 范围），**绝不**含凭证。
- 配额只留钩子：`set_quota_hook()` 注册后每次调用前检查；未注册 = 不限制。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from memory_agent.gateway.identity import Identity

QuotaHook = Callable[[Identity, str], None]
_QUOTA_HOOK: QuotaHook | None = None


def set_quota_hook(hook: QuotaHook | None) -> None:
    """注册配额检查钩子（收到 (identity, tool)）；None 清除。"""
    global _QUOTA_HOOK
    _QUOTA_HOOK = hook


def check_quota(identity: Identity, tool: str) -> None:
    if _QUOTA_HOOK is not None:
        _QUOTA_HOOK(identity, tool)


class AuditLog:
    """最小审计：内存事件 + 可选 JSONL 文件 sink。线程安全。"""

    def __init__(self, path: str | None = None, *, max_events: int = 1000):
        self.path = path
        self._max_events = max_events
        self._lock = threading.Lock()
        self._entries: list[dict] = []

    @property
    def entries(self) -> list[dict]:
        with self._lock:
            return list(self._entries)

    def record(
        self,
        *,
        identity: Identity | None,
        action: str,
        tool: str | None = None,
        outcome: str = "ok",
        detail: str | None = None,
    ) -> dict:
        event: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "outcome": outcome,
            "identity": identity.to_audit() if identity is not None else None,
        }
        if tool:
            event["tool"] = tool
        if detail:
            event["detail"] = detail
        with self._lock:
            self._entries.append(event)
            if len(self._entries) > self._max_events:
                del self._entries[: len(self._entries) - self._max_events]
        self._append_file(event)
        return event

    def _append_file(self, event: dict) -> None:
        if not self.path:
            return
        try:
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            # 审计落盘失败不该阻断服务；事件仍在内存里。
            pass


__all__ = ["AuditLog", "check_quota", "set_quota_hook"]
