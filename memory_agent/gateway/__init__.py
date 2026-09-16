"""网关层（#32 / ADR-0018 D2）：authn 在 `/mcp` 边界，authz 在工具层。

- `identity`：身份形状 + `/mcp` 边界解析（单租户 = 进程配置；多租户 = Bearer token）。
- `authz`：白名单构造 effective filter，**只可收窄**；ABAC（classification/residency）+ tenant。
- `context`：请求期身份上下文（ContextVar），工具层据此注入过滤。
- `audit`：最小可用的调用审计（记录调用 + 身份；配额留钩子）。
- `middleware`：`ServerMiddleware`，在 daemon 每个入站请求上解析身份并绑定。

**proxy 只做传输，不是信任源**（ADR-0018 D2.4）：身份只来自 daemon 进程配置或
daemon 侧校验过的 Bearer token，绝不来自可伪造的 header 字段。
"""
from memory_agent.gateway.audit import AuditLog
from memory_agent.gateway.authz import (
    AuthorizationError,
    can_own,
    can_read,
    can_write,
    effective_filter,
)
from memory_agent.gateway.context import (
    bind_identity,
    current_identity,
    reset_identity,
    use_identity,
)
from memory_agent.gateway.identity import (
    AuthConfig,
    AuthenticationError,
    Identity,
    default_identity,
    load_auth_config,
    make_identity,
    resolve_identity,
)
from memory_agent.gateway.middleware import GatewayAuthnMiddleware

__all__ = [
    "AuditLog",
    "AuthConfig",
    "AuthenticationError",
    "AuthorizationError",
    "GatewayAuthnMiddleware",
    "Identity",
    "bind_identity",
    "can_own",
    "can_read",
    "can_write",
    "current_identity",
    "default_identity",
    "effective_filter",
    "load_auth_config",
    "make_identity",
    "reset_identity",
    "resolve_identity",
    "use_identity",
]
