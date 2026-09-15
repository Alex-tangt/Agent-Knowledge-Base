"""`/mcp` 边界 authn 中间件（#32 / ADR-0018 D2.1、D2.3、D2.4）。

`ServerMiddleware` 包住每个入站请求：
1. 从 HTTP 请求头解析 `Authorization: Bearer <token>`（stdio 模式无请求 = 进程配置身份）；
2. 解析 / 校验身份（无效或缺失 = 拒绝，绝不落到默认身份）；
3. 把身份绑定到请求期上下文，供工具层 authz 读取；
4. 对 `tools/call` 记一条审计（调用 + 身份）。

**proxy 不是信任源**：这里只认 daemon 进程配置或 daemon 侧校验过的 token，
`ctx.request.headers` 里的其它字段（如 `X-Tenant`）一律不参与身份判定。
"""
from __future__ import annotations

import logging

from mcp.server.context import HandlerResult, ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_REQUEST

from memory_agent.gateway.audit import AuditLog, check_quota
from memory_agent.gateway.context import bind_identity, reset_identity
from memory_agent.gateway.identity import (
    AuthConfig,
    AuthenticationError,
    load_auth_config,
    resolve_identity,
)

logger = logging.getLogger(__name__)


def _authorization_header(ctx: ServerRequestContext) -> str | None:
    request = getattr(ctx, "request", None)
    headers = getattr(request, "headers", None) if request is not None else None
    if not headers:
        return None
    try:
        return headers.get("authorization")
    except Exception:  # noqa: BLE001 - 取头失败按无凭证处理
        return None


def _tool_name(ctx: ServerRequestContext) -> str | None:
    params = ctx.params or {}
    name = params.get("name") if isinstance(params, dict) else None
    return str(name) if name else None


class GatewayAuthnMiddleware:
    """authn（边界）+ 绑定会话上下文 + 审计。authz 在工具层（见 `authz`）。"""

    def __init__(self, config: AuthConfig | None = None, *, audit: AuditLog | None = None):
        self.config = config if config is not None else load_auth_config()
        self.audit = audit

    def _audit(self, identity, ctx, *, outcome: str, detail: str | None = None) -> None:
        if self.audit is None:
            return
        self.audit.record(
            identity=identity,
            action=ctx.method,
            tool=_tool_name(ctx),
            outcome=outcome,
            detail=detail,
        )

    async def __call__(self, ctx: ServerRequestContext, call_next) -> HandlerResult:
        try:
            identity = resolve_identity(_authorization_header(ctx), self.config)
        except AuthenticationError as exc:
            self._audit(None, ctx, outcome="unauthenticated", detail=str(exc))
            raise MCPError(code=INVALID_REQUEST, message=str(exc)) from None

        token = bind_identity(identity)
        outcome = "ok"
        try:
            result = await call_next(ctx)
            if ctx.method == "tools/call":
                tool = _tool_name(ctx)
                if tool:
                    check_quota(identity, tool)
            return result
        except Exception:
            outcome = "error"
            raise
        finally:
            reset_identity(token)
            if ctx.method == "tools/call":
                self._audit(identity, ctx, outcome=outcome)


__all__ = ["GatewayAuthnMiddleware"]
