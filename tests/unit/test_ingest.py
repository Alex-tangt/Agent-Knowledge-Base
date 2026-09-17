"""#51 上传接线单测：原件暂存 / 物化只读条目 / overlay 收录 / CLI / 错误路径。

走真实 pypdf 路径（主环境未装 Docling）与真子进程，不加载任何模型。
"""
import json
import os
import sys

import pytest

from memory_agent import ingest, settings
from memory_agent.corpus import loader
from memory_agent.memory.entries import Entry, parse_frontmatter


def _isolate(monkeypatch, tmp_path, overlay):
    """把只读来源 / overlay / KB 指到临时目录，避免碰真实机器配置。"""
    monkeypatch.setenv("MEMORY_READONLY_ROOTS", "")
    monkeypatch.setenv("MEMORY_OVERLAY_CONFIG", str(overlay))
    monkeypatch.delenv("MEMORY_READONLY_REPOS_CONFIG", raising=False)
    monkeypatch.setattr(settings, "KB_DIR", str(tmp_path / "kb"))
    os.makedirs(str(tmp_path / "kb"), exist_ok=True)


def test_stage_source_copies_original(tmp_path):
    source = tmp_path / "report.pdf"
    source.write_bytes(b"%PDF-1.4 fake")
    uploads = tmp_path / "uploads"

    staged = ingest.stage_source(str(source), uploads_dir=str(uploads), label="my-label")

    assert os.path.isfile(staged)
    assert os.path.normpath(staged).endswith(os.path.join("my-label", "report.pdf"))
    with open(staged, "rb") as handle:
        assert handle.read() == source.read_bytes()


def test_parse_sections_in_process_uses_pypdf(make_pdf, tmp_path):
    pdf = make_pdf(str(tmp_path / "sample.pdf"), ["HELLO"])
    sections = ingest.parse_sections(pdf)
    assert len(sections) == 1
    assert "HELLO" in sections[0].markdown


def test_parse_sections_via_subprocess(make_pdf, tmp_path):
    pdf = make_pdf(str(tmp_path / "s.pdf"), ["SUBPROCESS-TOKEN"])

    sections = ingest.parse_sections(pdf, parse_python=sys.executable)

    assert len(sections) == 1
    assert "SUBPROCESS-TOKEN" in sections[0].markdown


def test_ingest_document_materializes_readonly_and_registers(make_pdf, tmp_path, monkeypatch):
    pdf = make_pdf(str(tmp_path / "handbook.pdf"),
                   ["Handbook", "IMPORTALA marker in the body."])
    overlay = tmp_path / "overlay.json"
    import_dir = tmp_path / "imports"
    uploads_dir = tmp_path / "uploads"
    _isolate(monkeypatch, tmp_path, overlay)

    summary = ingest.ingest_document(
        pdf, label="handbook", import_dir=str(import_dir), uploads_dir=str(uploads_dir),
        overlay_path=str(overlay), tags=["imported"], today="2026-09-17",
    )

    assert summary["status"] == "ingested"
    assert summary["owner"] == "handbook"
    assert summary["entries"] == 1
    assert os.path.isfile(summary["staged_original"])

    written = summary["files"][0]
    assert os.path.normpath(written).startswith(os.path.normpath(str(import_dir)))

    entry = Entry.from_file(written, source="handbook/handbook.md", writable=False,
                            owner="handbook", root=summary["import_dir"])
    assert entry.writable is False
    assert entry.owner == "handbook"
    assert entry.type == "research"
    assert entry.status == "current"
    assert entry.tags == ["imported"]
    assert entry.id == "handbook/handbook"
    assert "IMPORTALA" in entry.body

    with open(overlay, "r", encoding="utf-8") as handle:
        saved = json.load(handle)
    assert saved["include"] and saved["include"][0]["label"] == "handbook"


def test_ingested_entries_resolve_as_explicit_readonly(make_pdf, tmp_path, monkeypatch):
    pdf = make_pdf(str(tmp_path / "guide.pdf"), ["Guide", "RESOLVEDALA text."])
    overlay = tmp_path / "overlay.json"
    import_dir = tmp_path / "imports"
    _isolate(monkeypatch, tmp_path, overlay)

    ingest.ingest_document(
        pdf, label="guide", import_dir=str(import_dir),
        uploads_dir=str(tmp_path / "uploads"), overlay_path=str(overlay),
    )

    selection = loader.resolve_selection()
    readonly = [f for f in selection.files if not f.writable]
    assert any(f.explicit and f.source == "guide/guide.md" for f in readonly)
    assert all(f.owner == "guide" for f in readonly)


def test_register_import_is_idempotent(make_pdf, tmp_path, monkeypatch):
    pdf = make_pdf(str(tmp_path / "a.pdf"), ["A", "IDEMPOTENT token."])
    overlay = tmp_path / "overlay.json"
    kwargs = dict(label="dup", import_dir=str(tmp_path / "imports"),
                  uploads_dir=str(tmp_path / "uploads"), overlay_path=str(overlay))
    _isolate(monkeypatch, tmp_path, overlay)

    first = ingest.ingest_document(pdf, **kwargs)
    second = ingest.ingest_document(pdf, **kwargs)

    assert first["registration"]["status"] == "written"
    assert second["registration"]["status"] == "noop"
    with open(overlay, "r", encoding="utf-8") as handle:
        includes = json.load(handle)["include"]
    assert len(includes) == 1


def test_ingest_rejects_unsupported_extension(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("nope", encoding="utf-8")

    with pytest.raises(ingest.IngestError):
        ingest.ingest_document(str(path))


def test_ingest_missing_file(tmp_path):
    with pytest.raises(ingest.IngestError):
        ingest.ingest_document(str(tmp_path / "nope.pdf"))


def test_docx_without_docling_surfaces_ingest_error(tmp_path, monkeypatch):
    from memory_agent.parse import engines

    monkeypatch.setattr(engines, "_docling_available", lambda: False)
    path = tmp_path / "x.docx"
    path.write_bytes(b"PK\x03\x04not-a-real-docx")

    with pytest.raises(ingest.IngestError):
        ingest.ingest_document(str(path), import_dir=str(tmp_path / "imports"),
                              uploads_dir=str(tmp_path / "uploads"))


def test_cli_main_outputs_json(make_pdf, tmp_path, monkeypatch, capsys):
    pdf = make_pdf(str(tmp_path / "cli.pdf"), ["CLI", "CLITOKEN body."])
    overlay = tmp_path / "overlay.json"
    _isolate(monkeypatch, tmp_path, overlay)

    code = ingest.main([
        pdf, "--label", "cli", "--import-dir", str(tmp_path / "imports"),
        "--uploads-dir", str(tmp_path / "uploads"), "--overlay", str(overlay),
    ])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ingested" and payload["entries"] == 1


def test_frontmatter_source_records_original_filename(make_pdf, tmp_path, monkeypatch):
    pdf = make_pdf(str(tmp_path / "orig.pdf"), ["X", "body"])
    overlay = tmp_path / "overlay.json"
    _isolate(monkeypatch, tmp_path, overlay)

    summary = ingest.ingest_document(
        pdf, label="orig", import_dir=str(tmp_path / "imports"),
        uploads_dir=str(tmp_path / "uploads"), overlay_path=str(overlay),
    )

    with open(summary["files"][0], "r", encoding="utf-8") as handle:
        meta, _ = parse_frontmatter(handle.read())
    assert meta["source"] == "orig.pdf"
    assert meta["id"] == "orig/orig"
