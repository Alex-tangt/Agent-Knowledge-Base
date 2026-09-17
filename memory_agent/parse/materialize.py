"""条目化：把 `Section` 落成带 frontmatter 的 Markdown 条目（ADR-0027 D2 / D4）。

产出与现有 `.md` 条目约定兼容（`memory_agent/memory/entries.py` 能直接解析）：
frontmatter 至少含 `id / title / type / tags / status / updated`，另加 `source` 记来源文档。
每节先经 `enforce_window` 压进嵌入窗口，再渲染——每份 Markdown 即一个索引点。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date

from memory_agent import settings
from memory_agent.parse.ports import Section
from memory_agent.parse.sections import enforce_window


@dataclass(frozen=True)
class EntryDraft:
    """一条待落盘的记忆条目：元数据 + 自包含 Markdown（含标题行）。"""

    id: str
    title: str
    source: str
    type: str
    tags: tuple[str, ...] = field(default_factory=tuple)
    status: str = "draft"
    updated: str = ""
    order: int = 0
    markdown: str = ""

    def render(self) -> str:
        """渲染完整 Markdown：frontmatter + 标题 + 正文（形状对齐 `render_entry`）。"""
        lines = [
            "---",
            f"id: {self.id}",
            f"title: {json.dumps(self.title, ensure_ascii=False)}",
            f"type: {self.type}",
            f"tags: [{', '.join(self.tags)}]",
            f"status: {self.status}",
            f"updated: {self.updated}",
            f"source: {json.dumps(self.source, ensure_ascii=False)}",
            "---",
            "",
            self.markdown.strip(),
            "",
        ]
        return "\n".join(lines)


def build_entries(
    sections: list[Section],
    *,
    doc_path: str,
    id_base: str,
    source: str | None = None,
    type: str = "research",
    tags: tuple[str, ...] | list[str] = (),
    status: str = "draft",
    updated: str | None = None,
    max_chars: int | None = None,
) -> list[EntryDraft]:
    """节列表 → 条目草稿：压窗口、派生唯一 id、补 frontmatter。

    - `id_base`：条目 id 基名（形如 `topics/my-doc`）；单节时即它本身，多节时追加
      `-01/-02…`，与"id == 相对路径去 `.md`"的 `kb.py check` 约定一致。
    - 类型 / 标签 / 状态由调用方给（解析层不替 KB 猜受控标签）。
    """
    limit = max_chars if max_chars is not None else settings.MAX_ENTRY_CHARS
    windowed = enforce_window(list(sections), limit)
    resolved_source = source or doc_path
    resolved_updated = updated or date.today().isoformat()
    total = len(windowed)

    return [
        EntryDraft(
            id=id_base if total == 1 else f"{id_base}-{index + 1:02d}",
            title=section.title,
            source=resolved_source,
            type=type,
            tags=tuple(tags),
            status=status,
            updated=resolved_updated,
            order=section.order,
            markdown=section.markdown,
        )
        for index, section in enumerate(windowed)
    ]


def write_entries(entries: list[EntryDraft], out_dir: str) -> list[str]:
    """把条目草稿写成 `<out_dir>/<id>.md`；返回落盘路径（id 里的 `/` 建子目录）。"""
    written: list[str] = []
    for entry in entries:
        relative = entry.id + ".md"
        target = os.path.join(out_dir, *relative.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(entry.render())
        written.append(target)
    return written
