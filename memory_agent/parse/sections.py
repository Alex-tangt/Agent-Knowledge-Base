"""按标题层级切节 + 把每节压进嵌入窗口（ADR-0027 D2 / D4）。

切节是"条目切分即节切分"的形状层：Docling / pypdf 都先产出 Markdown（或纯文本），
本模块把它切成 `Section`，再把超过窗口的节**按段落**续切——直接消掉 #47 观察到
的长文档 6000 字截断损失。
"""
from __future__ import annotations

import re
from dataclasses import replace

from memory_agent.parse.ports import Section

# ATX 标题：`## 标题`（允许尾部闭合 `##`）。Setext 标题 Docling 不产出，不做。
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
# 分块续切时给标题后缀预留的余量（` (part 9999)`），保证渲染后仍 ≤ 窗口。
_PART_RESERVE = len(" (part 9999)")


def _normalize(markdown: str) -> str:
    return markdown.replace("\r\n", "\n").replace("\r", "\n")


def _split_heading(text: str) -> tuple[tuple[int, str] | None, str]:
    """拆首行标题：`((level, 标题), 余下正文)`；首行不是标题则 `(None, 原文)`。"""
    first, _, rest = text.partition("\n")
    match = _HEADING_RE.match(first)
    if not match:
        return None, text
    return (len(match.group(1)), match.group(2).strip()), rest


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:120]
    return ""


def split_markdown_sections(markdown: str, *, fallback_title: str = "") -> list[Section]:
    """把一份 Markdown 按 ATX 标题切成节。

    - 首个标题之前的前言自成一节（标题回落 `fallback_title` / 首行文本）；
    - 围栏代码块（``` / ~~~）里的 `#` 不算标题；
    - 只有标题、没有正文的空节被丢弃（Docling 常见"父标题紧跟子标题"）。
    """
    text = _normalize(markdown)
    sections: list[Section] = []
    buffer: list[str] = []
    title: str | None = None
    level = 1
    fence: str | None = None

    def flush() -> None:
        nonlocal buffer, title, level
        raw = "\n".join(buffer).strip()
        buffer = []
        if not raw:
            return
        heading, body = _split_heading(raw)
        if heading is not None and not body.strip():
            return  # 标题后没有正文：空节，丢弃
        resolved = (
            title
            or (heading[1] if heading else "")
            or fallback_title
            or _first_line(body)
            or "Untitled"
        )
        # 保证每节 Markdown 自包含（以 `# 标题` 开头）：嵌入窗口只量这一个字段，
        # 且 `Entry.embedding_text` 不会再把标题重复拼一次。
        markdown = raw if heading is not None else f"# {resolved}\n\n{raw}"
        sections.append(Section(
            title=resolved,
            level=heading[0] if heading else 1,
            markdown=markdown,
            order=len(sections),
        ))
        title, level = None, 1

    for line in text.split("\n"):
        stripped = line.strip()
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            buffer.append(line)
            continue
        if stripped[:3] in ("```", "~~~"):
            fence = stripped[:3]
            buffer.append(line)
            continue
        match = _HEADING_RE.match(line)
        if match:
            flush()
            title = match.group(2).strip() or fallback_title
            level = len(match.group(1))
        buffer.append(line)
    flush()

    if not sections and text.strip():
        body = text.strip()
        heading, _rest = _split_heading(body)
        resolved = (heading[1] if heading else "") or fallback_title or _first_line(body) or "Untitled"
        sections.append(Section(
            title=resolved,
            level=heading[0] if heading else 1,
            markdown=body if heading else f"# {resolved}\n\n{body}",
            order=0,
        ))
    return sections


def _split_paragraphs(text: str, budget: int) -> list[str]:
    """按空行分段贪心装箱；单段超预算则按字符硬切（保证不漏出窗口）。"""
    text = text.strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n[ \t]*\n", text)]
    chunks: list[str] = []
    current = ""
    for paragraph in (p for p in paragraphs if p):
        if len(paragraph) > budget:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(
                paragraph[i:i + budget] for i in range(0, len(paragraph), budget)
            )
            continue
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= budget:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = paragraph
    if current:
        chunks.append(current)
    return chunks


def _chunk_section(section: Section, max_chars: int) -> list[Section]:
    """把一个超窗口的节按段落续切成多节；每节 Markdown 都 ≤ max_chars。"""
    markdown = section.markdown.strip()
    if len(markdown) <= max_chars:
        return [section]

    heading, body = _split_heading(markdown)
    if heading is None:
        level, heading_text, body = 1, section.title, markdown
    else:
        level, heading_text = heading[0], heading[1] or section.title

    base_heading = f"{'#' * level} {heading_text}"
    budget = max(1, max_chars - len(base_heading) - _PART_RESERVE - 2)
    chunks = _split_paragraphs(body, budget) or [""]

    chunked: list[Section] = []
    for index, chunk in enumerate(chunks):
        suffix = "" if index == 0 else f" (part {index + 1})"
        piece = f"{base_heading}{suffix}\n\n{chunk}".strip()
        chunked.append(Section(
            title=f"{heading_text}{suffix}", level=level, markdown=piece,
            order=section.order,
        ))
    return chunked


def enforce_window(sections: list[Section], max_chars: int) -> list[Section]:
    """把每节压进 `max_chars`；返回**顺序重排**后的节列表。"""
    if max_chars is None or max_chars <= 0:
        return list(sections)
    out: list[Section] = []
    for section in sections:
        out.extend(_chunk_section(section, max_chars))
    return [replace(section, order=index) for index, section in enumerate(out)]
