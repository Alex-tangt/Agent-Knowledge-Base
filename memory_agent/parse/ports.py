"""文档解析端口：`DocumentParser.parse(path) -> [Section]`。

范式同 `VectorStore` 端口（ADR-0019 / ADR-0027 D3）：v1 固定单引擎 + 扩展点，
**不做** glob → 引擎路由。`Section` 是「文档 → 结构化 Markdown 条目」管线的中间形状：
解析引擎（Docling / pypdf）只负责产出节，条目化（`materialize`）再把节落成带
frontmatter 的 Markdown —— 这样"真相源 = 文件 + git"（ADR-0025 D1）继续成立。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class ParseError(RuntimeError):
    """解析失败：文件读不出 / 格式不支持 / 引擎内部报错。"""


class ParserUnavailable(ParseError):
    """引擎的可选依赖未安装（如未装 `memory-agent[parse]` 的 Docling）。"""


@dataclass(frozen=True)
class Section:
    """文档里的一个节：标题 + 层级 + 该节的 Markdown 正文（含标题行）。

    `markdown` 是自包含的（以 `# 标题` 开头），这样条目化后 `Entry.embedding_text`
    不会再把标题重复拼一次，窗口核算也只需量这一个字段。
    """

    title: str
    level: int
    markdown: str
    order: int = 0

    def __post_init__(self) -> None:
        if not self.title:
            raise ValueError("Section.title 不能为空")
        if self.level < 1:
            raise ValueError("Section.level 必须 >= 1（1 = 顶层标题）")


@runtime_checkable
class DocumentParser(Protocol):
    """解析引擎端口。扩展点 = 实现 `name` + `parse` 后注册进 `engines.PARSERS`。"""

    name: str

    def parse(self, path: str) -> list[Section]:
        ...
