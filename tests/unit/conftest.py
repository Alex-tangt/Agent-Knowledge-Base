"""单元测试引导。

`ragcore` / `memory_agent` 现在是真包（ADR-0024），不再用 sys.path 垫片。
按仓库约定用 `venv\\Scripts\\python.exe -m pytest tests/unit -q` 从仓库根运行
（`python -m` 会把 CWD 加入 sys.path），或先 `pip install -e ragcore -e memory_agent`。
"""
import zipfile

import pytest


@pytest.fixture
def make_pdf():
    """合成一份可被 pypdf 抽出文本的单页 PDF（不提交二进制夹具）。"""

    def _make(path: str, lines: list[str]) -> str:
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
        })
        operators = ["BT", "/F1 12 Tf", "72 720 Td"]
        for index, line in enumerate(lines):
            if index:
                operators.append("0 -18 Td")
            escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            operators.append(f"({escaped}) Tj")
        operators.append("ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(operators).encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        with open(path, "wb") as handle:
            writer.write(handle)
        return path

    return _make


@pytest.fixture
def make_docx():
    """合成一份最小合法 DOCX（Heading1 + 正文 + Heading2 + 正文），供 Docling 真解析。"""

    def _make(path: str) -> str:
        content_types = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>"
        )
        rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>"
        )
        document = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>"
            '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
            "<w:r><w:t>Overview</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Body text for the document.</w:t></w:r></w:p>"
            '<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr>'
            "<w:r><w:t>Details</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>More detail here.</w:t></w:r></w:p>"
            "</w:body></w:document>"
        )
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", content_types)
            archive.writestr("_rels/.rels", rels)
            archive.writestr("word/document.xml", document)
        return path

    return _make
