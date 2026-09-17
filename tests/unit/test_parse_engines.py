"""解析引擎单测：pypdf 兜底 / Docling 注入 / 回落 / 无硬依赖 import。

Docling 是 optional extra（本环境未装），故用注入的假转换器验证"取 Markdown → 切节"
这段逻辑；真实 Docling 路径由 `@pytest.mark.skipif` 在装了 extra 的环境跑。
"""
import importlib
import sys

import pytest

from memory_agent.parse import engines
from memory_agent.parse.engines import (
    DoclingParser,
    PyPDFParser,
    available_parsers,
    get_parser,
    parse_document,
)
from memory_agent.parse.ports import ParseError, ParserUnavailable


class _FakeDocument:
    def __init__(self, markdown):
        self._markdown = markdown

    def export_to_markdown(self):
        return self._markdown


class _FakeResult:
    def __init__(self, markdown):
        self.document = _FakeDocument(markdown)


class _FakeConverter:
    def __init__(self, markdown):
        self.markdown = markdown
        self.calls = []

    def convert(self, source):
        self.calls.append(source)
        return _FakeResult(self.markdown)


def test_importing_parse_does_not_import_docling():
    assert "docling" not in sys.modules

    importlib.import_module("memory_agent.parse")

    assert "docling" not in sys.modules


def test_pypdf_engine_extracts_text(make_pdf, tmp_path):
    pdf = make_pdf(str(tmp_path / "sample.pdf"), ["Heading Text", "Body line one."])

    sections = PyPDFParser().parse(pdf)

    assert len(sections) == 1
    assert sections[0].title == "sample"
    assert "Heading Text" in sections[0].markdown


def test_pypdf_engine_rejects_non_pdf(tmp_path):
    path = tmp_path / "x.docx"
    path.write_bytes(b"not a pdf")

    with pytest.raises(ParseError):
        PyPDFParser().parse(str(path))


def test_get_parser_falls_back_to_pypdf_without_docling(monkeypatch):
    monkeypatch.setattr(engines, "_docling_available", lambda: False)

    assert isinstance(get_parser(), PyPDFParser)
    assert available_parsers() == ["pypdf"]


def test_get_parser_explicit_docling_and_unknown_name():
    assert isinstance(get_parser("docling"), DoclingParser)
    with pytest.raises(ValueError):
        get_parser("markitdown")


def test_docling_engine_splits_injected_markdown_by_heading():
    converter = _FakeConverter("# A\n\nalpha\n\n## B\n\nbeta\n")

    sections = DoclingParser(converter=converter).parse("whatever.docx")

    assert [s.title for s in sections] == ["A", "B"]
    assert converter.calls == ["whatever.docx"]


def test_docling_engine_missing_dependency_is_parser_unavailable():
    def factory():
        raise ParserUnavailable("Docling 未安装")

    with pytest.raises(ParserUnavailable):
        DoclingParser(converter_factory=factory).parse("a.pdf")


def test_parse_document_falls_back_to_pypdf_when_docling_fails(make_pdf, tmp_path, monkeypatch):
    pdf = make_pdf(str(tmp_path / "s.pdf"), ["hello from pdf"])

    class Boom(DoclingParser):
        def parse(self, path):
            raise ParseError("boom")

    monkeypatch.setattr(engines, "_docling_available", lambda: True)
    monkeypatch.setitem(engines.PARSERS, "docling", Boom)

    sections = parse_document(pdf)

    assert "hello from pdf" in sections[0].markdown


def test_parse_document_without_fallback_raises_first_error(make_pdf, tmp_path, monkeypatch):
    pdf = make_pdf(str(tmp_path / "s.pdf"), ["hello"])

    class Boom(DoclingParser):
        def parse(self, path):
            raise ParseError("boom")

    monkeypatch.setattr(engines, "_docling_available", lambda: True)
    monkeypatch.setitem(engines.PARSERS, "docling", Boom)

    with pytest.raises(ParseError, match="boom"):
        parse_document(pdf, allow_fallback=False)


def test_parse_document_explicit_parser_does_not_fall_back(make_pdf, tmp_path, monkeypatch):
    pdf = make_pdf(str(tmp_path / "s.pdf"), ["hello"])

    class Boom(DoclingParser):
        def parse(self, path):
            raise ParseError("boom")

    monkeypatch.setattr(engines, "_docling_available", lambda: True)
    monkeypatch.setitem(engines.PARSERS, "docling", Boom)

    with pytest.raises(ParseError, match="boom"):
        parse_document(pdf, parser="docling")


def test_parse_document_accepts_injected_parser_instance(make_pdf, tmp_path):
    pdf = make_pdf(str(tmp_path / "s.pdf"), ["injected"])

    sections = parse_document(pdf, parser=PyPDFParser())

    assert "injected" in sections[0].markdown


def test_docx_without_docling_reports_clean_error(tmp_path, monkeypatch):
    monkeypatch.setattr(engines, "_docling_available", lambda: False)
    path = tmp_path / "a.docx"
    path.write_bytes(b"PK\x03\x04not-really-a-docx")

    with pytest.raises(ParseError):
        parse_document(str(path))


@pytest.mark.skipif(not engines._docling_available(), reason="Docling 未安装（memory-agent[parse]）")
def test_real_docling_parses_docx(make_docx, tmp_path):
    path = make_docx(str(tmp_path / "real.docx"))

    sections = parse_document(path, parser="docling")

    assert "Overview" in [s.title for s in sections]
