"""请求期身份上下文（#32 / ADR-0018 D2.1）：authn 产物 -> 工具层可读。

`GatewayAuthnMiddleware` 在 `/mcp` 边界解析出 `Identity` 后 `bind_identity()`；
工具层用 `current_identity()` 取用。用 `ContextVar`（而非全局变量）保证并发请求
之间互不串身份；工具函数的同步执行经 `anyio.to_thread` 复制上下文，仍能读到。

请求之外（直接单测 / stdio 引导）回落到进程配置身份 `default_identity()`。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator

from memory_agent.gateway.identity import Identity, default_identity

_CURRENT: ContextVar[Identity | None] = ContextVar("memory_gateway_identity", default=None)


def bind_identity(identity: Identity) -> Token:
    return _CURRENT.set(identity)


def reset_identity(token: Token) -> None:
    _CURRENT.reset(token)


def current_identity() -> Identity:
    """当前请求的身份；请求外回落到进程配置身份。"""
    identity = _CURRENT.get()
    return identity if identity is not None else default_identity()


@contextmanager
def use_identity(identity: Identity | None) -> Iterator[Identity]:
    """显式绑定身份的作用域（测试 / 脚本用；None = 进程默认）。"""
    resolved = identity if identity is not None else default_identity()
    token = bind_identity(resolved)
    try:
        yield resolved
    finally:
        reset_identity(token)


__all__ = [
    "bind_identity",
    "current_identity",
    "reset_identity",
    "use_identity",
]
