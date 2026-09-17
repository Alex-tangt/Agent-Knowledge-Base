"""解析引擎：Docling 首装 + pypdf 兜底（ADR-0027 D3）。

**可选依赖不硬绑**：Docling 只走 `memory-agent[parse]`，import 本模块**不**触发
`import docling`；pypdf 也在真正解析时才 import。Docling 的模型加载 / 下载发生在
`parse()` 里（显式、可预期），不在 import 期隐式触发，避免污染 daemon / 检索链路的
离线语义（#18 / #46）。

v1 固定"单引擎 + 扩展点"：`PARSERS` 是可加的注册表；不做 glob → 引擎路由。
"""
from __future__ import annotations

import importlib.util
import os

from memory_agent.parse.ports import (
    DocumentParser,
    ParseError,
    ParserUnavailable,
    Section,
)
from memory_agent.parse.sections import split_markdown_sections


def _stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0] or "document"


def _docling_available() -> bool:
    """Docling 是否可 import（只查 spec，不 import、不加载模型）。"""
    try:
        return importlib.util.find_spec("docling") is not None
    except (ImportError, ValueError):  # pragma: no cover - 环境异常兜底
        return False


def _pypdf_available() -> bool:
    try:
        return importlib.util.find_spec("pypdf") is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


# --------------------------------------------------------------------- pypdf 兜底

class PyPDFParser:
    """纯文本兜底：`pypdf` 逐页抽文本，再按 Markdown / 段落简单切分。

    无版式 / 表格能力（ADR-0027 调研页），但零新增依赖、失败时给出明确错误而不崩。
    """

    name = "pypdf"

    def parse(self, path: str) -> list[Section]:
        extension = os.path.splitext(path)[1].lower()
        if extension and extension != ".pdf":
            raise ParseError(f"pypdf 兜底只支持 PDF，收到 {extension}：{path}")
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - 已装时才走真实路径
            raise ParserUnavailable(
                "pypdf 未安装：pip install pypdf"
            ) from exc

        try:
            reader = PdfReader(path)
            pages = [(page.extract_text() or "").strip() for page in reader.pages]
        except Exception as exc:  # pypdf 抛出的异常类型较杂，统一转 ParseError
            raise ParseError(f"pypdf 解析失败：{path}: {exc}") from exc

        text = "\n\n".join(page for page in pages if page)
        if not text.strip():
            raise ParseError(f"pypdf 未从 PDF 抽出任何文本：{path}")
        return split_markdown_sections(text, fallback_title=_stem(path))


# --------------------------------------------------------------------- Docling

def _default_docling_converter():
    """构造 `DocumentConverter`（首次会加载 / 可能下载 Docling 模型）。"""
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:
        raise ParserUnavailable(
            "Docling 未安装：pip install 'memory-agent[parse]'"
        ) from exc
    return DocumentConverter()


class DoclingParser:
    """高保真首装引擎：Docling `convert()` → `export_to_markdown()` → 按标题切节。

    `converter` / `converter_factory` 可注入——单测据此在未安装 Docling 时也能验证
    "取 Markdown → 切节"这段逻辑；生产走默认工厂（lazy import + lazy 构造）。
    """

    name = "docling"

    def __init__(self, converter=None, converter_factory=None):
        self._converter = converter
        self._converter_factory = converter_factory or _default_docling_converter

    def _get_converter(self):
        if self._converter is None:
            self._converter = self._converter_factory()
        return self._converter

    def parse(self, path: str) -> list[Section]:
        converter = self._get_converter()
        try:
            result = converter.convert(str(path))
        except ParseError:
            raise
        except Exception as exc:
            raise ParseError(f"Docling 解析失败：{path}: {exc}") from exc

        document = getattr(result, "document", result)
        try:
            markdown = document.export_to_markdown()
        except AttributeError as exc:
            raise ParseError(f"Docling 结果无 export_to_markdown()：{path}") from exc
        if not str(markdown).strip():
            raise ParseError(f"Docling 未从文档抽出任何内容：{path}")
        return split_markdown_sections(markdown, fallback_title=_stem(path))


# --------------------------------------------------------------------- 选择 / 兜底

# 扩展点：引擎名 -> 构造器。新增引擎只需注册，无需碰调用方。
PARSERS: dict[str, type] = {
    "docling": DoclingParser,
    "pypdf": PyPDFParser,
}


def available_parsers() -> list[str]:
    """当前环境可用的引擎名（按优先级：Docling > pypdf）。"""
    names: list[str] = []
    if _docling_available():
        names.append("docling")
    if _pypdf_available():
        names.append("pypdf")
    return names


def get_parser(name: str | None = None) -> DocumentParser:
    """取引擎实例：显式 `name` 优先，否则按可用性回落（Docling → pypdf）。"""
    if name:
        factory = PARSERS.get(name)
        if factory is None:
            raise ValueError(f"未知解析引擎 {name!r}；可选：{sorted(PARSERS)}")
        return factory()
    if _docling_available():
        return DoclingParser()
    if _pypdf_available():
        return PyPDFParser()
    raise ParserUnavailable("没有可用的解析引擎（既无 Docling 也无 pypdf）")


def _auto_order() -> list[str]:
    """自动模式下按优先级尝试的引擎名。"""
    order = []
    if _docling_available():
        order.append("docling")
    if _pypdf_available():
        order.append("pypdf")
    return order


def parse_document(
    path: str,
    *,
    parser: str | DocumentParser | None = None,
    allow_fallback: bool = True,
) -> list[Section]:
    """解析文档为节列表。

    - `parser` 给实例 / 名字 = 用户显式指定，**不静默换引擎**（失败即抛）；
    - `parser=None` = 自动：Docling 不可用或解析失败时回落 pypdf（`allow_fallback=False`
      可关掉回落，让首个错误直接冒泡）。
    """
    if parser is not None and not isinstance(parser, str):
        return parser.parse(path)

    if parser is not None:
        return get_parser(parser).parse(path)

    errors: list[str] = []
    candidates = _auto_order()
    if not candidates:
        raise ParserUnavailable("没有可用的解析引擎（既无 Docling 也无 pypdf）")
    for name in candidates:
        try:
            return PARSERS[name]().parse(path)
        except ParserUnavailable as exc:
            errors.append(f"{name}: {exc}")
            continue
        except ParseError as exc:
            errors.append(f"{name}: {exc}")
            if not allow_fallback:
                raise
            continue
    raise ParseError("所有解析引擎都失败：\n- " + "\n- ".join(errors))
