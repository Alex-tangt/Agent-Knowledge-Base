"""运行时收录解析（#36 / ADR-0025 D8）：注册表默认 ∪ 显式 overlay + 廉价指纹。

不加载模型、不碰 Qdrant；只锁 loader 的选择语义与读时重读（改配置免重启）。
"""
import json
import os

from memory_agent import settings
from memory_agent.corpus import loader


def _write(path, text):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def _isolate(monkeypatch, tmp_path, repos=None, overlay=None):
    """隔离机器本地配置：KB / 注册表 / overlay 全部指向 tmp_path。"""
    monkeypatch.setattr(settings, "KB_DIR", str(tmp_path / "kb"))
    monkeypatch.delenv("MEMORY_READONLY_ROOTS", raising=False)
    config = tmp_path / "readonly_repos.json"
    config.write_text(json.dumps(repos if repos is not None else []), encoding="utf-8")
    monkeypatch.setenv("MEMORY_READONLY_REPOS_CONFIG", str(config))
    overlay_path = tmp_path / "overlay.json"
    if overlay is not None:
        overlay_path.write_text(json.dumps(overlay), encoding="utf-8")
    monkeypatch.setenv("MEMORY_OVERLAY_CONFIG", str(overlay_path))
    return config, overlay_path


def _readonly_sources(selection):
    return {f.source for f in selection.files if not f.writable}


# ------------------------------------------------------------- 注册表（默认）

def test_registry_is_reread_at_runtime(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _write(repo / "docs" / "a.md", "# A\n")
    _isolate(monkeypatch, tmp_path, repos=[{"label": "r1", "path": str(repo)}])

    first = loader.resolve_selection()
    assert _readonly_sources(first) == {"r1/docs/a.md"}

    # 外部新增文件后再解析，无需重启 / 改 env
    _write(repo / "docs" / "b.md", "# B\n")
    second = loader.resolve_selection()
    assert _readonly_sources(second) == {"r1/docs/a.md", "r1/docs/b.md"}


def test_registry_owner_inherited_by_files(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _write(repo / "a.md", "# A\n")
    _isolate(monkeypatch, tmp_path,
             repos=[{"label": "r1", "path": str(repo), "owner": "alice"}])

    selection = loader.resolve_selection()

    file = next(f for f in selection.files if f.source == "r1/a.md")
    assert file.owner == "alice"
    assert file.explicit is False


# ------------------------------------------------------------------- overlay

def test_overlay_include_exact_file_is_explicit(tmp_path, monkeypatch):
    extra = tmp_path / "notes" / "extra.md"
    _write(extra, "# Extra\n")
    _isolate(monkeypatch, tmp_path, repos=[], overlay={"include": [str(extra)]})

    selection = loader.resolve_selection()

    file = next(f for f in selection.files if f.path == str(extra))
    assert file.explicit is True
    assert file.source == "notes/extra.md"
    assert file.owner == "notes"  # owner 默认按来源标签
    assert next(s for s in selection.sources if s.explicit).label == "notes"


def test_overlay_glob_then_exclude(tmp_path, monkeypatch):
    specs = tmp_path / "specs"
    _write(specs / "public.md", "# Public\n")
    _write(specs / "private.md", "# Private\n")
    _isolate(monkeypatch, tmp_path, repos=[], overlay={
        "include": [{"glob": str(specs / "*.md"), "label": "specs", "owner": "me"}],
        "exclude": [str(specs / "private.md")],
    })

    selection = loader.resolve_selection()

    assert _readonly_sources(selection) == {"specs/public.md"}
    included = next(f for f in selection.files if f.source == "specs/public.md")
    assert included.owner == "me"


def test_overlay_exclude_applies_to_registry_files(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _write(repo / "keep.md", "# Keep\n")
    _write(repo / "skip.md", "# Skip\n")
    _isolate(monkeypatch, tmp_path, repos=[{"label": "r1", "path": str(repo)}],
             overlay={"exclude": [str(repo / "skip.md")]})

    selection = loader.resolve_selection()

    assert _readonly_sources(selection) == {"r1/keep.md"}


def test_explicit_include_overrides_registry_attribution(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _write(repo / "a.md", "# A\n")
    _isolate(monkeypatch, tmp_path,
             repos=[{"label": "r1", "path": str(repo), "owner": "alice"}],
             overlay={"include": [{"path": str(repo / "a.md"), "label": "mine",
                                   "owner": "me"}]})

    selection = loader.resolve_selection()

    file = next(f for f in selection.files if f.path == str(repo / "a.md"))
    assert file.explicit is True
    assert file.source == "mine/a.md"
    assert file.owner == "me"


# -------------------------------------------------------------- 完整性标志

def test_invalid_registry_marks_selection_incomplete(tmp_path, monkeypatch):
    config, _ = _isolate(monkeypatch, tmp_path, repos=[])
    config.write_text("{ not json", encoding="utf-8")

    selection = loader.resolve_selection()

    assert selection.complete is False


def test_invalid_overlay_marks_selection_incomplete(tmp_path, monkeypatch):
    _, overlay = _isolate(monkeypatch, tmp_path, repos=[])
    overlay.write_text("[not, an, object]", encoding="utf-8")

    selection = loader.resolve_selection()

    assert selection.complete is False


def test_missing_overlay_is_complete_and_empty(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path, repos=[])

    selection = loader.resolve_selection()

    assert selection.complete is True
    assert selection.include_specs == []
    assert selection.exclude_specs == []


# ---------------------------------------------------------------- 指纹（D9）

def test_scan_fingerprint_stats_selected_files(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _write(repo / "a.md", "# A\n")
    _isolate(monkeypatch, tmp_path, repos=[{"label": "r1", "path": str(repo)}])

    fingerprint, complete = loader.scan_fingerprint()

    assert complete is True
    assert set(fingerprint) == {os.path.normcase(str(repo / "a.md"))}
    assert all(isinstance(value, list) and len(value) == 2 for value in fingerprint.values())


def test_scan_fingerprint_changes_when_file_changes(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    path = _write(repo / "a.md", "# A\n")
    _isolate(monkeypatch, tmp_path, repos=[{"label": "r1", "path": str(repo)}])

    before, _ = loader.scan_fingerprint()
    _write(path, "# A\n\nmore content\n")
    after, _ = loader.scan_fingerprint()

    assert before != after
