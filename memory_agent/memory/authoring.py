"""条目写作与校验：渲染合规 frontmatter + 按全局 KB 的规则校验。

规则镜像自 `knowledge/tools/kb.py`（单一事实源在那里）：本模块只做写入前的本地
校验与渲染，不实现索引/搜索。`:memory:` 写入网关（writer.py）用它保证「过 check」。
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import date

VALID_TYPES = frozenset({"project-knowledge", "topic", "decision", "research"})
VALID_STATUS = frozenset({"current", "draft", "superseded", "archived"})
REQUIRED_KEYS = ("id", "title", "type", "tags", "status", "updated")
MAX_ENTRY_LINES = 150

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DOMAIN_RE = re.compile(r"^(topics|decisions|projects/[a-z0-9][a-z0-9-]*)$")
_FM_BLOCK_RE = re.compile(r"\A(---[ \t]*\r?\n)(.*?)(\r?\n---[ \t]*(?:\r?\n|$))", re.DOTALL)
_TOP_KEY_RE = re.compile(r"^([A-Za-z0-9_]+):")

# 领域 <-> type 的对应关系由 KB 目录约定决定（kb.py 不检查，但写网关要守住）。
TYPE_FOR_DOMAIN = {
    "topics": frozenset({"topic"}),
    "decisions": frozenset({"decision", "research"}),
}


def slugify(title: str) -> str:
    """英文标题 -> slug。无法产生 ASCII slug（如纯中文标题）时返回空串。"""
    ascii_text = (
        unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    )
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def normalize_domain(domain: str) -> str:
    """校验并规范化领域路径：topics / decisions / projects/<slug>。"""
    normalized = (domain or "").strip().strip("/").replace("\\", "/")
    if not _DOMAIN_RE.match(normalized):
        raise ValueError(
            f"domain {domain!r} 不合法：应为 topics / decisions / projects/<slug>"
        )
    return normalized


def allowed_types(domain: str) -> frozenset[str]:
    """该领域允许的 type 集合。"""
    if domain.startswith("projects/"):
        return frozenset({"project-knowledge"})
    return TYPE_FOR_DOMAIN[domain]


def load_tag_vocab(kb_dir: str) -> set[str] | None:
    """读取 tags.md 的受控标签表；没有 tags.md 时返回 None（不校验标签）。"""
    path = os.path.join(kb_dir, "tags.md")
    if not os.path.isfile(path):
        return None
    vocab: set[str] = set()
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            match = re.match(r"^\s*-\s+`?([a-z0-9][a-z0-9-]*)`?\s*$", line)
            if match:
                vocab.add(match.group(1))
    return vocab or None


def _yaml_scalar(value: str) -> str:
    """能裸写就裸写，否则 JSON 引号包起来（YAML 双引号块认得）。"""
    needs_quoting = (
        value == ""
        or "\n" in value
        or ": " in value
        or " #" in value
        or value[0] in "-?:,[]{}#&*!|>'\"%@`"
    )
    return json.dumps(value, ensure_ascii=False) if needs_quoting else value


def render_entry(
    *,
    entry_id: str,
    title: str,
    type: str,
    tags: list[str],
    status: str,
    updated: str,
    sources: list[str] | None,
    body: str,
    extra: dict[str, str] | None = None,
) -> str:
    """渲染一条 KB 条目的完整 Markdown（frontmatter + 标题 + 正文）。

    extra 是 frontmatter 的附加标量字段（生命周期标注用，如 supersedes）。
    """
    lines = [
        "---",
        f"id: {entry_id}",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"type: {type}",
        f"tags: [{', '.join(tags)}]",
        f"status: {status}",
        f"updated: {updated}",
    ]
    if sources:
        lines.append("sources:")
        lines += [f"  - {_yaml_scalar(source)}" for source in sources]
    for key, value in (extra or {}).items():
        lines.append(f"{key}: {_yaml_scalar(str(value))}")
    lines += ["---", "", f"# {title}", "", body.strip(), ""]
    return "\n".join(lines)


def update_frontmatter_fields(text: str, updates: dict[str, str]) -> str:
    """就地改写 frontmatter 里的顶层标量字段：已有的换值，没有的插到块末。

    只动命名的那几行——正文、未知字段、字段顺序、行尾风格都原样保留；这正是
    生命周期工具「不原地编辑正文、不丢信息」的实现方式。无 frontmatter 时抛错。
    """
    match = _FM_BLOCK_RE.match(text)
    if not match:
        raise ValueError("条目缺少合法 frontmatter，无法做生命周期标注")
    head, inner, tail = match.group(1), match.group(2), match.group(3)
    newline = "\r\n" if "\r\n" in head else "\n"
    lines = inner.split(newline)

    for key, value in updates.items():
        rendered = f"{key}: {_yaml_scalar(str(value))}"
        for index, line in enumerate(lines):
            key_match = _TOP_KEY_RE.match(line)
            if key_match and key_match.group(1) == key:
                lines[index] = rendered
                break
        else:
            lines.append(rendered)

    return head + newline.join(lines) + tail + text[match.end():]


def validate_entry(
    text: str, rel_path: str, tag_vocab: set[str] | None = None
) -> tuple[list[str], list[str]]:
    """按 kb.py check 的规则校验条目，返回 (errors, warnings)。

    errors 非空即「过不了 check」；warnings 对应 kb.py 里只警告不报错的项
    （未知标签、超长条目）。
    """
    from memory_agent.memory.entries import parse_frontmatter

    errors: list[str] = []
    warnings: list[str] = []
    meta, _ = parse_frontmatter(text)
    if not meta:
        return [f"{rel_path}: 缺少 frontmatter 块"], []

    for key in REQUIRED_KEYS:
        if key not in meta or meta[key] in ("", []):
            errors.append(f"{rel_path}: missing {key}")
    if meta.get("type") not in VALID_TYPES:
        errors.append(f"{rel_path}: bad type {meta.get('type')!r}")
    if meta.get("status") not in VALID_STATUS:
        errors.append(f"{rel_path}: bad status {meta.get('status')!r}")
    if not _DATE_RE.match(str(meta.get("updated", ""))):
        errors.append(f"{rel_path}: updated not YYYY-MM-DD")

    expect = rel_path[:-3] if rel_path.endswith(".md") else rel_path
    if meta.get("id") != expect:
        errors.append(f"{rel_path}: id {meta.get('id')!r} != path {expect!r}")

    line_count = len(text.splitlines())
    if line_count > MAX_ENTRY_LINES:
        warnings.append(f"{rel_path}: {line_count} 行 (> {MAX_ENTRY_LINES}；请拆分)")

    for tag in meta.get("tags") or []:
        if not _TAG_RE.match(str(tag)):
            errors.append(f"{rel_path}: bad tag {tag!r}")
        elif tag_vocab is not None and tag not in tag_vocab:
            warnings.append(f"{rel_path}: tag {tag!r} 不在 tags.md")

    return errors, warnings


def today_str() -> str:
    return date.today().isoformat()
