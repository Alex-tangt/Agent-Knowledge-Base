"""证据归并 / 上下文格式化（issue #48）。

检索是**条目级**（一条 = 一整篇文章的送嵌正文，≤6000 字），比参考实现的 256-token 块长
一个量级。为保证 LLM 上下文可控，`format_context` 按证据顺序做**总字符预算**截断
（`CONTEXT_MAX_CHARS`），并把结果记入日志（`context_chars` / `truncated`），供报告披露。
"""
from __future__ import annotations

from typing import Any


def make_doc(hit: dict, round_index: int, score: float | None = None) -> dict:
    """把一次命中规范成证据项。"""
    return {
        "entry_id": hit.get("entry_id") or hit.get("id"),
        "title": hit.get("title") or "",
        "source": hit.get("source") or "",
        "text": hit.get("text") or hit.get("snippet") or "",
        "round": round_index,
        "score": float(score if score is not None else hit.get("score", 0.0)),
    }


def merge_evidence(existing: list[dict], incoming: list[dict],
                   cap: int) -> tuple[list[dict], list[str]]:
    """按 `entry_id` 去重合并（保序），返回 `(累计, 本轮新增 id)`。"""
    seen = {doc["entry_id"] for doc in existing}
    added: list[str] = []
    merged = list(existing)
    for doc in incoming:
        eid = doc["entry_id"]
        if eid in seen:
            continue
        seen.add(eid)
        added.append(eid)
        if len(merged) < cap:
            merged.append(doc)
    return merged, added


def format_context(docs: list[dict], max_chars: int) -> tuple[str, dict]:
    """把证据列表渲染成带引用 id 的上下文；按顺序贪心截断到总预算。"""
    parts: list[str] = []
    used = 0
    included = 0
    truncated = False
    for index, doc in enumerate(docs, start=1):
        header = (f"[E{index}] Title: {doc.get('title') or 'Untitled'}\n"
                  f"Source: {doc.get('source') or 'Unknown source'}\n")
        remaining = max_chars - used
        if remaining <= len(header):
            truncated = True
            break
        body = doc.get("text") or ""
        room = remaining - len(header)
        if len(body) > room:
            body = body[:room]
            truncated = True
        parts.append(header + body)
        used += len(header) + len(body)
        included += 1
        if truncated:
            break
    return "\n\n--------------\n\n".join(parts), {
        "docs_total": len(docs),
        "docs_included": included,
        "context_chars": used,
        "truncated": truncated,
    }
