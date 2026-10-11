"""Canonical logical paths shared by capture aliases, sources and authorization."""

from __future__ import annotations

import re
from datetime import date

CAPTURE_URI = re.compile(r"^viking://resources/captures/([0-9a-f]{64})/content\.md$")
PROJECT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
KINDS = {"note", "article_summary", "video_summary"}


def logical_path(value: str, *, root: bool = False) -> str:
    """Reject ambiguous paths rather than decoding or repairing caller input."""
    if root and value in {"", "root"}:
        return ""
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError("invalid logical path")
    if any(c in value for c in "\\:%?#\x00") or any(ord(c) < 32 for c in value):
        raise ValueError("invalid logical path")
    parts = value.split("/")
    if any(not p or p in {".", ".."} or p != p.strip() or p.endswith(".") for p in parts):
        raise ValueError("invalid logical path")
    if parts[0] not in {"captures", "projects"}:
        raise ValueError("path must start with captures or projects")
    if parts[0] == "projects" and len(parts) > 1 and not PROJECT_NAME.fullmatch(parts[1]):
        raise ValueError("invalid project name")
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
