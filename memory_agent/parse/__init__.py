"""文档解析与收录管线（ADR-0027 / #50）。

「文档 → 结构化 Markdown 条目」= 可插拔 `DocumentParser` 端口（Docling 首装 +
pypdf 兜底）→ 按标题切节 → 压进嵌入窗口 → 物化带 frontmatter 的 `.md` 条目。

本包**只做解析 / 切分 / 条目化纯逻辑**：不接 MCP、不改索引 / 检索、不写全局 KB
（接线是 #51）。可选依赖走 `memory-agent[parse]`，import 本包不触发重依赖加载。
"""
from memory_agent.parse.engines import (
    DoclingParser,
    PyPDFParser,
    available_parsers,
    get_parser,
    parse_document,
)
from memory_agent.parse.materialize import (
    EntryDraft,
    build_entries,
    write_entries,
)
from memory_agent.parse.ports import (
    DocumentParser,
    ParseError,
    ParserUnavailable,
    Section,
)
from memory_agent.parse.sections import enforce_window, split_markdown_sections

__all__ = [
    "DocumentParser",
    "DoclingParser",
    "EntryDraft",
    "ParseError",
    "ParserUnavailable",
    "PyPDFParser",
    "Section",
    "available_parsers",
    "build_entries",
    "enforce_window",
    "get_parser",
    "parse_document",
    "split_markdown_sections",
    "write_entries",
]
