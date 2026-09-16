"""身份与 authn（#32 / ADR-0018 D2.1、D2.4）：`/mcp` 边界把凭证解析成 `Identity`。

- **单租户阶段：身份 = 进程配置**（`memory_agent/.env` / 进程环境），形状仍按多租户设计。
- **多租户形状：`MEMORY_AUTH_TOKENS`** 给出 `<token> -> <身份>` 映射；daemon 在 `/mcp`
  边界校验 `Authorization: Bearer <token>`。token 只做**等值查找**，绝不回显、绝不写日志。
- 缺 token / 无效 token = **拒绝**（require_token 模式）。
- **proxy 不是信任源**（D2.4）：身份绝不来自 `X-Tenant` 之类可伪造字段，只来自进程配置
  或 daemon 自己校验过的 Bearer token。

`Identity` 的 ABAC 维度（classification / residency）用**允许集**表达；tenant 是隔离边界
的**单值绑定**（None = 单租户未绑定）。
"""
from __future__ import annotations

import hmac
import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from memory_agent.memory.ports import (
    CLASSIFICATION_VALUES,
    DEFAULT_CLASSIFICATION,
    DEFAULT_RESIDENCY,
    RESIDENCY_VALUES,
)

VALID_ROLES = ("owner", "admin", "writer", "reader")


class AuthenticationError(RuntimeError):
    """身份解析失败。消息面向调用方，但**绝不**包含凭证本身。"""


@dataclass(frozen=True)
class Identity:
    """一次请求的有效身份（authn 的产物，authz 的输入）。"""

    principal: str
    tenant: str | None
    role: str
    allowed_classifications: frozenset[str]
    allowed_residencies: frozenset[str]
    # 可写域所有权（#36 / ADR-0025 D3/D15）：None = 不限制（单租户默认）。
    owned_domains: frozenset[str] | None = None

    def to_audit(self) -> dict[str, Any]:
        """审计用视图：只含身份，不含任何凭证。"""
        return {
            "principal": self.principal,
            "tenant": self.tenant,
            "role": self.role,
            "classifications": sorted(self.allowed_classifications),
            "residencies": sorted(self.allowed_residencies),
            "owners": sorted(self.owned_domains) if self.owned_domains is not None else None,
        }


@dataclass(frozen=True)
class AuthConfig:
    """authn 策略：默认身份 + 可选 token 映射（多租户形状）。"""

    default: Identity
    tokens: Mapping[str, Identity] = field(default_factory=dict)
    require_token: bool = False

    @property
    def multi_tenant(self) -> bool:
        return bool(self.tokens)


def _parse_allowed(raw: Any, universe: Sequence[str], field_name: str) -> frozenset[str]:
    """把逗号串 / 列表解析成允许集；缺省 = 全集（不限制）。"""
    if raw is None:
        return frozenset(universe)
    if isinstance(raw, str):
        values = [part.strip() for part in raw.split(",") if part.strip()]
    elif isinstance(raw, (list, tuple, set, frozenset)):
        values = [str(part).strip() for part in raw if str(part).strip()]
    else:
        raise AuthenticationError(f"authz 配置错误：{field_name} 类型不支持")
    if not values:
        return frozenset(universe)
    allowed = frozenset(values)
    unknown = allowed - set(universe)
    if unknown:
        raise AuthenticationError(
            f"authz 配置错误：{field_name} 含未知值 {sorted(unknown)}"
        )
    return allowed


def _parse_owners(raw: Any) -> frozenset[str] | None:
    """可写域所有权：逗号串 / 列表；缺省 / 空 = None（不限制）。"""
    if raw is None:
        return None
    if isinstance(raw, str):
        values = [part.strip() for part in raw.split(",") if part.strip()]
    elif isinstance(raw, (list, tuple, set, frozenset)):
        values = [str(part).strip() for part in raw if str(part).strip()]
    else:
        raise AuthenticationError("authz 配置错误：owners 类型不支持")
    return frozenset(values) if values else None


def make_identity(
    *,
    principal: str,
    tenant: str | None = None,
    role: str = "reader",
    classifications: Any = None,
    residencies: Any = None,
    owners: Any = None,
) -> Identity:
    """构造并校验一个 `Identity`（供配置解析与测试使用）。"""
    principal = (principal or "").strip()
    if not principal:
        raise AuthenticationError("authz 配置错误：principal 不能为空")
    role = (role or "").strip() or "reader"
    if role not in VALID_ROLES:
        raise AuthenticationError(
            f"authz 配置错误：role {role!r} 不合法，应为 {list(VALID_ROLES)}"
        )
    if tenant is not None:
        tenant = str(tenant).strip() or None
    return Identity(
        principal=principal,
        tenant=tenant,
        role=role,
        allowed_classifications=_parse_allowed(
            classifications, CLASSIFICATION_VALUES, "classification"
        ),
        allowed_residencies=_parse_allowed(residencies, RESIDENCY_VALUES, "residency"),
        owned_domains=_parse_owners(owners),
    )


def default_identity() -> Identity:
    """进程配置身份（单租户阶段 = 身份）。每次读取 settings，便于测试改写。"""
    from memory_agent import settings

    return make_identity(
        principal=getattr(settings, "AUTH_PRINCIPAL", "local"),
        tenant=getattr(settings, "AUTH_TENANT", None),
        role=getattr(settings, "AUTH_ROLE", "owner"),
        classifications=getattr(settings, "AUTH_CLASSIFICATIONS", None),
        residencies=getattr(settings, "AUTH_RESIDENCIES", None),
        owners=getattr(settings, "AUTH_OWNERS", None),
    )


def _parse_tokens(raw: str) -> dict[str, Identity]:
    """解析 `MEMORY_AUTH_TOKENS`（JSON）：`{token: {tenant, role, principal, ...}}`。

    配置错误 → 抛 `AuthenticationError`（daemon 启动即失败，绝不静默放行）。
    """
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuthenticationError("authz 配置错误：MEMORY_AUTH_TOKENS 不是合法 JSON") from exc
    if not isinstance(data, dict):
        raise AuthenticationError("authz 配置错误：MEMORY_AUTH_TOKENS 应为对象")
    tokens: dict[str, Identity] = {}
    for token, spec in data.items():
        if not isinstance(spec, dict):
            raise AuthenticationError("authz 配置错误：token 映射值应为对象")
        tokens[str(token)] = make_identity(
            principal=spec.get("principal") or f"token:{str(token)[:4]}",
            tenant=spec.get("tenant"),
            role=spec.get("role") or "reader",
            classifications=spec.get("classifications"),
            residencies=spec.get("residencies"),
            owners=spec.get("owners"),
        )
    return tokens


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_auth_config() -> AuthConfig:
    """从 settings（进程环境 / `.env`）装载 authn 策略。"""
    from memory_agent import settings

    tokens = _parse_tokens(getattr(settings, "AUTH_TOKENS", ""))
    require = getattr(settings, "AUTH_REQUIRE_TOKEN", None)
    # 配了 token 映射默认进入「必须带 token」模式（多租户）；显式 0 可关。
    require_token = _truthy(require) if require not in (None, "") else bool(tokens)
    return AuthConfig(default=default_identity(), tokens=tokens, require_token=require_token)


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def _lookup_token(tokens: Mapping[str, Identity], token: str) -> Identity | None:
    for candidate, identity in tokens.items():
        if hmac.compare_digest(candidate, token):
            return identity
    return None


def resolve_identity(authorization: str | None, config: AuthConfig) -> Identity:
    """`Authorization` 头 -> `Identity`。无效 / 缺失（require_token）→ `AuthenticationError`。"""
    token = _bearer_token(authorization)
    if token is not None:
        identity = _lookup_token(config.tokens, token)
        if identity is None:
            raise AuthenticationError("凭证无效或已失效")
        return identity
    if config.require_token:
        raise AuthenticationError("缺少 Bearer 凭证")
    return config.default
