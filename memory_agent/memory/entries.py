"""条目解析：Markdown + frontmatter -> Entry。

真相源是 Markdown 文件本身；Entry 只是解析视图，可随时从文件重建。
"""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass, field

import yaml

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.DOTALL)
_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def point_id_for(entry_id: str) -> str:
    """条目 id -> 稳定的 Qdrant 点 id（uuid5）。

    增量重建会按条目 upsert；点 id 必须由 entry_id 决定（而非随机），否则同一
    条目会被写成多个点、数量核对与孤儿清理都会失真。
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"memory_agent:{entry_id}"))


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """返回 (frontmatter dict, body)。无合法 frontmatter 时返回 ({}, 原文)。"""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    return meta, text[match.end():]


def _as_str(value) -> str | None:
    if value is None:
        return None
    return str(value)


def _as_tags(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    if isinstance(value, (list, tuple)):
        return [str(t) for t in value]
    return [str(value)]


@dataclass
class Entry:
    id: str
    path: str
    source: str
    writable: bool
    title: str
    content: str
    type: str | None = None
    tags: list[str] = field(default_factory=list)
    status: str | None = None
    updated: str | None = None

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    @property
    def body(self) -> str:
        return parse_frontmatter(self.content)[1]

    def embedding_text(self, max_chars: int) -> str:
        text = self.body.strip()
        if self.title and not text.startswith("# "):
            text = f"{self.title}\n\n{text}"
        return text[:max_chars]

    def snippet(self, limit: int = 240) -> str:
        text = " ".join(self.body.split())
        return text[:limit]

    def to_manifest(self) -> dict:
        return {
            "path": self.path,
            "source": self.source,
            "writable": self.writable,
            "title": self.title,
            "type": self.type,
            "tags": self.tags,
            "status": self.status,
            "updated": self.updated,
            "hash": self.content_hash,
        }

    def to_payload(self, max_chars: int) -> dict:
        payload = dict(self.to_manifest())
        payload["text"] = self.embedding_text(max_chars)
        payload["entry_id"] = self.id
        return payload

    @classmethod
    def from_file(
        cls,
        path: str,
        *,
        source: str,
        writable: bool,
        entry_id: str | None = None,
    ) -> "Entry":
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()
        meta, body = parse_frontmatter(content)

        meta_id = _as_str(meta.get("id"))
        resolved_id = entry_id or meta_id or f"repo:{source}"

        title = _as_str(meta.get("title"))
        if not title:
            heading = _HEADING_RE.search(body)
            title = heading.group(1) if heading else os.path.splitext(os.path.basename(path))[0]

        return cls(
            id=resolved_id,
            path=os.path.abspath(path),
            source=source,
            writable=writable,
            title=title,
            content=content,
            type=_as_str(meta.get("type")),
            tags=_as_tags(meta.get("tags")),
            status=_as_str(meta.get("status")),
            updated=_as_str(meta.get("updated")),
        )
