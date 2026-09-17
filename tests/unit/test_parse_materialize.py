"""条目化单测：frontmatter 兼容 / id 派生 / 窗口 / 落盘 / docx→条目。"""
import os

from memory_agent import settings
from memory_agent.memory.entries import Entry, parse_frontmatter
from memory_agent.parse import (
    DoclingParser,
    EntryDraft,
    Section,
    build_entries,
    parse_document,
    write_entries,
)


class _FakeConverter:
    def __init__(self, markdown):
        self.markdown = markdown

    def convert(self, source):
        document = type("Doc", (), {"export_to_markdown": lambda self: self._md})()
        document._md = self.markdown
        return type("Result", (), {"document": document})()


def test_render_frontmatter_is_entry_compatible():
    draft = EntryDraft(
        id="topics/x",
        title="Title: with colon",
        source="a/b.pdf",
        type="research",
        tags=("pdf", "docx"),
        status="draft",
        updated="2026-09-17",
        markdown="# Title\n\nbody",
    )

    meta, body = parse_frontmatter(draft.render())

    assert meta["id"] == "topics/x"
    assert meta["title"] == "Title: with colon"
    assert meta["type"] == "research"
    assert meta["tags"] == ["pdf", "docx"]
    assert meta["status"] == "draft"
    # 日期裸写（同 `render_entry` 约定）：YAML 会解析成 date，取回时字符串化一致。
    assert str(meta["updated"]) == "2026-09-17"
    assert meta["source"] == "a/b.pdf"
    assert body.strip() == "# Title\n\nbody"


def test_parse_pdf_to_entries_roundtrips_through_entry(make_pdf, tmp_path):
    pdf = make_pdf(str(tmp_path / "guide.pdf"), ["Introduction", "Some body text."])

    drafts = build_entries(
        parse_document(pdf),
        doc_path=pdf,
        id_base="topics/guide",
        source="guide.pdf",
        type="research",
        tags=["pdf"],
        status="draft",
        updated="2026-09-17",
    )

    assert len(drafts) == 1
    assert drafts[0].id == "topics/guide"
    written = write_entries(drafts, str(tmp_path / "kb"))
    entry = Entry.from_file(written[0], source="topics/guide.md", writable=True)

    assert entry.id == "topics/guide"
    assert entry.title == drafts[0].title
    assert entry.type == "research"
    assert entry.tags == ["pdf"]
    assert entry.status == "draft"


def test_build_entries_multiple_sections_get_unique_ids_and_stay_in_window():
    sections = [
        Section(title=f"S{i}", level=1, markdown=f"# S{i}\n\n" + "x" * 3000)
        for i in range(3)
    ]

    drafts = build_entries(
        sections, doc_path="d.pdf", id_base="projects/d",
        max_chars=6000, updated="2026-09-17",
    )

    ids = [d.id for d in drafts]
    assert ids == ["projects/d-01", "projects/d-02", "projects/d-03"]
    assert len(ids) == len(set(ids))
    assert all(len(d.markdown) <= 6000 for d in drafts)


def test_build_entries_default_window_reads_settings(monkeypatch):
    monkeypatch.setattr(settings, "MAX_ENTRY_CHARS", 500)
    sections = [Section(title="A", level=1, markdown="# A\n\n" + "y" * 2000)]

    drafts = build_entries(sections, doc_path="a.pdf", id_base="topics/a")

    assert len(drafts) > 1
    assert all(len(d.markdown) <= 500 for d in drafts)


def test_write_entries_creates_nested_paths(tmp_path):
    drafts = [
        EntryDraft(id="topics/foo-01", title="One", source="f.pdf", type="research",
                   tags=("pdf",), status="draft", updated="2026-09-17", markdown="# One\n\nx"),
        EntryDraft(id="topics/foo-02", title="Two", source="f.pdf", type="research",
                   tags=("pdf",), status="draft", updated="2026-09-17", markdown="# Two\n\ny"),
    ]

    written = write_entries(drafts, str(tmp_path))

    assert all(os.path.isfile(path) for path in written)
    assert written[0].endswith(os.path.join("topics", "foo-01.md"))
    assert written[1].endswith(os.path.join("topics", "foo-02.md"))


def test_docx_sections_materialize_via_injected_docling(tmp_path):
    path = tmp_path / "spec.docx"
    path.write_bytes(b"placeholder")
    converter = _FakeConverter("# Overview\n\nalpha\n\n## Details\n\nbeta\n")

    sections = DoclingParser(converter=converter).parse(str(path))
    drafts = build_entries(
        sections, doc_path=str(path), id_base="projects/spec",
        tags=["docx"], updated="2026-09-17",
    )

    assert [d.title for d in drafts] == ["Overview", "Details"]
    assert [d.id for d in drafts] == ["projects/spec-01", "projects/spec-02"]
    assert all(d.source == str(path) for d in drafts)
