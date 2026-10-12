"""Canonical logical paths shared by capture aliases, sources and authorization."""

from __future__ import annotations

import re
from datetime import date

CAPTURE_URI = re.compile(r"^viking://resources/captures/([0-9a-f]{64})/content\.md$")
PROJECT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
KINDS = {"note", "article_summary", "video_summary"}


def logical_path(value: str, *, root: bool = False) -> str:
    """Reject ambiguous paths rather than decoding or repairing caller input."""
    if not isinstance(value, str) or not value or len(value) > 4096:
        if root and value == "":
            return ""
        raise ValueError("invalid logical path")
    if root and value == "root":
        return ""
    if any(c in value for c in '\\:%?#\x00"<>|*') or any(ord(c) < 32 for c in value):
        raise ValueError("invalid logical path")
    parts = value.split("/")
    if any(not p or p in {".", ".."} or p != p.strip() or p.endswith(".") for p in parts):
        raise ValueError("invalid logical path")
    devices = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
    if any(part.split(".", 1)[0].lower() in devices for part in parts):
        raise ValueError("invalid logical path")
    if parts[0] not in {"captures", "projects"}:
        raise ValueError("path must start with captures or projects")
    if parts[0] == "projects" and len(parts) > 1 and not PROJECT_NAME.fullmatch(parts[1]):
        raise ValueError("invalid project name")
    if parts[0] == "captures":
        if len(parts) > 1 and parts[1] != "unknown":
            observed_date(parts[1])
        if len(parts) > 2 and parts[2] not in KINDS:
            raise ValueError("invalid capture kind")
        if len(parts) > 3 and not re.fullmatch(r"[0-9a-f]{64}", parts[3]):
            raise ValueError("invalid capture id")
        if len(parts) > 5 or (len(parts) == 5 and parts[4] != "content.md"):
            raise ValueError("invalid capture content path")
    return value


def observed_date(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("observed_on must be an ISO date")
    date.fromisoformat(value)
    return value


def capture_path(uri: str, metadata: dict) -> str:
    match = CAPTURE_URI.fullmatch(uri)
    kind = metadata.get("kind", "note")
    if not match or kind not in KINDS:
        raise ValueError("invalid capture metadata")
    day = observed_date(metadata.get("observed_on")) or "unknown"
    return f"captures/{day}/{kind}/{match[1]}/content.md"


def contains(scope: str, path: str) -> bool:
    return not scope or path == scope or path.startswith(scope + "/")


def overlaps(left: str, right: str) -> bool:
    return contains(left, right) or contains(right, left)


def allowed(path: str, scopes: tuple[str, ...] | None) -> bool:
    return scopes is None or any(contains(scope, path) for scope in scopes)


def effective_scopes(requested: str, grants: tuple[str, ...] | None) -> list[str]:
    """Intersect a requested subtree with configured grants before reading content."""
    if grants is None:
        return [requested]
    result = []
    for grant in grants:
        if contains(grant, requested):
            result.append(requested)
        elif contains(requested, grant):
            result.append(grant)
    return sorted(set(result))


def validate_access(data: object) -> dict:
    """Validate durable, non-secret host scope rules used when issuing tokens."""
    if (not isinstance(data, dict) or set(data) != {"schema", "profiles"}
            or type(data["schema"]) is not int or data["schema"] != 1
            or not isinstance(data["profiles"], dict)):
        raise ValueError("access must contain schema=1 and profiles")
    profiles = {}
    for profile, rules in data["profiles"].items():
        if (profile not in {"codex", "dsh", "opencode", "agent-bubble", "reader"}
                or not isinstance(rules, dict) or set(rules) - {"read_scopes", "write_scopes"}):
            raise ValueError("invalid access profile or fields")
        normalized = {}
        for key, value in rules.items():
            if not isinstance(value, list) or len(value) > 128 or not all(isinstance(scope, str) for scope in value):
                raise ValueError("access scopes must be path arrays")
            normalized[key] = sorted(set(logical_path(scope, root=True) for scope in value))
        profiles[profile] = normalized
    return {"schema": 1, "profiles": profiles}
