"""工具层 authz（#32 / ADR-0018 D2.2）：白名单构造 effective filter，**只可收窄**。

调用方可以自带 `payload_filter`（例如进一步缩小 `writable` 范围），但**受管控维度**
（`tenant` / `classification` / `residency`）一律由网关按身份 entitlement 计算：

- 调用方请求的值必须落在 entitlement 内，否则**显式拒绝**（`AuthorizationError`），
  绝不静默放宽；
- 调用方省略时，网关**无条件注入** entitlement 的约束（多值 → 端口 `MatchAny`）；
- entitlement 为全集时该维度不产生子句（本地单租户默认不限制）。

tenant 是隔离边界：身份绑定租户后，调用方请求别的租户 = 越权 → 拒绝。
"""
from __future__ import annotations

from typing import Any, Mapping

from memory_agent.gateway.identity import Identity
from memory_agent.memory.ports import (
    CLASSIFICATION_VALUES,
    DEFAULT_CLASSIFICATION,
    DEFAULT_RESIDENCY,
    RESIDENCY_VALUES,
)

_MISSING = object()
_WRITE_ROLES = frozenset({"owner", "admin", "writer"})
_UNIVERSE = {
    "classification": frozenset(CLASSIFICATION_VALUES),
    "residency": frozenset(RESIDENCY_VALUES),
}

class AuthorizationError(RuntimeError):
    """调用方请求超出身份授权范围；消息面向调用方，含修正方向。"""


def can_write(identity: Identity) -> bool:
    """写 / 维护类工具的角色门槛。"""
    return identity.role in _WRITE_ROLES


def can_own(identity: Identity, owner: str | None) -> bool:
    """当前身份是否拥有某个域（#36 / ADR-0025 D3/D15）。

    `owned_domains is None` = 单租户未限制（本地默认）；否则 owner 必须落在授权集内。
    """
    if identity.owned_domains is None:
        return True
    return owner is not None and owner in identity.owned_domains


def _require_str(field: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AuthorizationError(f"{field} 过滤值必须是非空字符串")
    return value.strip()


def _gate(field: str, allowed: frozenset[str], requested: Any) -> str | list[str] | None:
    """单个受管控维度：返回过滤子句（str / 多值 list / None=不限制）。"""
    universe = _UNIVERSE[field]
    if requested is _MISSING:
        if allowed == universe:
            return None
        if len(allowed) == 1:
            return next(iter(allowed))
        return sorted(allowed)
    value = _require_str(field, requested)
    if value not in allowed:
        raise AuthorizationError(
            f"越权：请求 {field}={value!r} 超出授权范围 {sorted(allowed)}"
        )
    return value


def effective_filter(identity: Identity, requested: Mapping[str, Any] | None = None) -> dict | None:
    """把调用方请求与身份 entitlement 合并成最终过滤；返回 None 表示不过滤。"""
    requested = dict(requested or {})
    effective: dict[str, Any] = {}

    req_tenant = requested.pop("tenant", _MISSING)
    if identity.tenant is None:
        # 未绑定租户（单租户 / 本地）：调用方可以进一步收窄到某个 tenant。
        if req_tenant is not _MISSING:
            effective["tenant"] = _require_str("tenant", req_tenant)
    else:
        if req_tenant is not _MISSING and req_tenant != identity.tenant:
            raise AuthorizationError(
                f"越权：请求 tenant={req_tenant!r} 与绑定租户 {identity.tenant!r} 不符"
            )
        effective["tenant"] = identity.tenant

    for field, allowed in (
        ("classification", identity.allowed_classifications),
        ("residency", identity.allowed_residencies),
    ):
        clause = _gate(field, allowed, requested.pop(field, _MISSING))
        if clause is not None:
            effective[field] = clause

    # 其余字段不承载隔离/密级：调用方自选，仍然只用于收窄。
    for key, value in requested.items():
        effective[key] = value

    return effective or None


def can_read(identity: Identity, meta: Mapping[str, Any] | None) -> bool:
    """条目元数据是否落在该身份的读授权内（供 `memory_get` / 生命周期预检）。"""
    meta = meta or {}
    if identity.tenant is not None and meta.get("tenant") != identity.tenant:
        return False
    classification = meta.get("classification") or DEFAULT_CLASSIFICATION
    if classification not in identity.allowed_classifications:
        return False
    residency = meta.get("residency") or DEFAULT_RESIDENCY
    if residency not in identity.allowed_residencies:
        return False
    return True


__all__ = [
    "AuthorizationError",
    "can_own",
    "can_read",
    "can_write",
    "effective_filter",
]
