"""收录接口面（#36 / ADR-0025 D8）：include / exclude / list + 域所有权授权。

离线、确定性：只用 tmp 目录里的假 KB / 仓库 / overlay，不加载模型、不碰 Qdrant。
"""
import json
import os

import pytest

from memory_agent import settings
from memory_agent.corpus import loader
from memory_agent.gateway import make_identity
from memory_agent.memory.admission import AdmissionError, AdmissionManager


def _write(path, text):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return str(path)


def _setup(monkeypatch, tmp_path, repos=None):
    monkeypatch.setattr(settings, "KB_DIR", str(tmp_path / "kb"))
    monkeypatch.delenv("MEMORY_READONLY_ROOTS", raising=False)
    config = tmp_path / "readonly_repos.json"
    config.write_text(json.dumps(repos if repos is not None else []), encoding="utf-8")
    monkeypatch.setenv("MEMORY_READONLY_REPOS_CONFIG", str(config))
    overlay = tmp_path / "overlay.json"
    monkeypatch.setenv("MEMORY_OVERLAY_CONFIG", str(overlay))
    return overlay


def _writer(owners=None):
    return make_identity(principal="me", role="writer", owners=owners)


def _readonly_sources(selection):
    return {f.source for f in selection.files if not f.writable}


# ------------------------------------------------------------------- include

def test_include_preview_does_not_write(tmp_path, monkeypatch):
    overlay = _setup(monkeypatch, tmp_path)
    extra = _write(tmp_path / "notes" / "a.md", "# A\n")

    result = AdmissionManager().include(pattern=extra, identity=_writer())

    assert result["status"] == "confirmation_required"
    assert result["action"] == "include"
    assert result["written"] is False
    assert result["preview"]["matched"] == 1
    assert result["preview"]["label"] == "notes"
    assert not overlay.exists()


def test_include_confirm_writes_overlay_and_becomes_explicit(tmp_path, monkeypatch):
    overlay = _setup(monkeypatch, tmp_path)
    extra = _write(tmp_path / "notes" / "a.md", "# A\n")

    result = AdmissionManager().include(pattern=extra, confirm=True, identity=_writer())

    assert result["status"] == "written"
    data = json.loads(overlay.read_text(encoding="utf-8"))
    assert data["include"][0]["pattern"] == extra

    selection = loader.resolve_selection()
    file = next(f for f in selection.files if f.path == os.path.abspath(extra))
    assert file.explicit is True
    assert file.source == "notes/a.md"


def test_include_without_match_is_rejected(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path)
    with pytest.raises(AdmissionError):
        AdmissionManager().include(
            pattern=str(tmp_path / "nope" / "*.md"), confirm=True, identity=_writer()
        )


# -------------------------------------------------------------------- list

def test_list_distinguishes_default_and_explicit(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _write(repo / "docs" / "a.md", "# A\n")
    _setup(monkeypatch, tmp_path, repos=[{"label": "r1", "path": str(repo)}])
    extra = _write(tmp_path / "notes" / "b.md", "# B\n")
    AdmissionManager().include(pattern=extra, confirm=True, identity=_writer())

    listing = AdmissionManager().list()

    origins = {(f["source"], f["origin"]) for f in listing["resolved"]}
    assert ("r1/docs/a.md", "default") in origins
    assert ("notes/b.md", "explicit") in origins
    assert any(s["origin"] == "explicit" for s in listing["sources"])
    assert listing["counts"]["explicit"] == 1
    assert listing["complete"] is True


# ------------------------------------------------------------------ exclude

def test_exclude_preview_lists_leaving_files_without_writing(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _write(repo / "keep.md", "# Keep\n")
    skip = _write(repo / "skip.md", "# Skip\n")
    overlay = _setup(monkeypatch, tmp_path, repos=[{"label": "r1", "path": str(repo)}])

    result = AdmissionManager().exclude(pattern=skip, identity=_writer())

    assert result["status"] == "confirmation_required"
    assert result["written"] is False
    assert result["preview"]["leaving"] == 1
    assert not overlay.exists()


def test_exclude_confirm_shrinks_the_selection(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _write(repo / "keep.md", "# Keep\n")
    skip = _write(repo / "skip.md", "# Skip\n")
    _setup(monkeypatch, tmp_path, repos=[{"label": "r1", "path": str(repo)}])

    result = AdmissionManager().exclude(pattern=skip, confirm=True, identity=_writer())

    assert result["status"] == "written"
    assert _readonly_sources(loader.resolve_selection()) == {"r1/keep.md"}


def test_exclude_is_idempotent(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    skip = _write(repo / "skip.md", "# Skip\n")
    _setup(monkeypatch, tmp_path, repos=[{"label": "r1", "path": str(repo)}])

    AdmissionManager().exclude(pattern=skip, confirm=True, identity=_writer())
    again = AdmissionManager().exclude(pattern=skip, confirm=True, identity=_writer())

    assert again["status"] == "noop"
    assert again["written"] is False


# ------------------------------------------------------------------- authz

def test_include_refuses_foreign_domain(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    extra = _write(repo / "a.md", "# A\n")
    _setup(monkeypatch, tmp_path,
           repos=[{"label": "r1", "path": str(repo), "owner": "other"}])

    with pytest.raises(AdmissionError):
        AdmissionManager().include(pattern=extra, confirm=True, identity=_writer("me"))


def test_exclude_refuses_foreign_domain(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    skip = _write(repo / "skip.md", "# Skip\n")
    _setup(monkeypatch, tmp_path,
           repos=[{"label": "r1", "path": str(repo), "owner": "other"}])

    with pytest.raises(AdmissionError):
        AdmissionManager().exclude(pattern=skip, confirm=True, identity=_writer("me"))


def test_include_refuses_file_above_classification(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path)
    secret = _write(
        tmp_path / "notes" / "secret.md",
        '---\nclassification: public\n---\n\n# Secret\n',
    )
    identity = make_identity(principal="me", role="writer", classifications="private")

    with pytest.raises(AdmissionError):
        AdmissionManager().include(pattern=secret, confirm=True, identity=identity)


# -------------------------------------------------------------- 非法 overlay

def test_invalid_overlay_blocks_writes(tmp_path, monkeypatch):
    overlay = _setup(monkeypatch, tmp_path)
    overlay.write_text("{ broken", encoding="utf-8")
    extra = _write(tmp_path / "notes" / "a.md", "# A\n")

    with pytest.raises(AdmissionError):
        AdmissionManager().include(pattern=extra, confirm=True, identity=_writer())
