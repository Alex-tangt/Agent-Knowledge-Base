"""Live source behavior and safety, using only disposable project fixtures."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from memory_base.paths import allowed, capture_path, effective_scopes, logical_path, validate_access
from memory_base.sources import SourceError, SourceRegistry, lexical_score, validate_registry


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "project"
    (root / "docs").mkdir(parents=True)
    registry = tmp_path / "sources.json"
    registry.write_text(json.dumps({"schema": 1, "projects": [{"name": "demo", "root": str(root)}]}))
    return root, registry, SourceRegistry(registry)


def test_live_edits_crlf_revision_delete_unavailable_and_recovery(source):
    root, registry, sources = source
    path = "projects/demo/docs/design.md"
    original = "# 原文\r\n\r\n  未提交修改\r\n".encode("utf-8")
    target = root / "docs" / "design.md"
    target.write_bytes(original)
    first = sources.read(path)
    assert first["content"].encode("utf-8") == original
    assert first["revision"] == hashlib.sha256(original).hexdigest()
    assert sources.scan(["projects/demo"])[0]["revision"] == first["revision"]
    target.write_text("new uncommitted text", encoding="utf-8")
    assert sources.read(path)["revision"] != first["revision"]
    with pytest.raises(SourceError) as mismatch:
        sources.read(path, first["revision"])
    assert mismatch.value.status == 409
    target.unlink()
    assert sources.scan(["projects/demo"]) == []
    with pytest.raises(SourceError) as deleted:
        sources.read(path)
    assert deleted.value.status == 404
    root.rename(root.with_name("offline"))
    with pytest.raises(SourceError) as offline:
        sources.scan(["projects/demo"])
    assert offline.value.status == 503
    root.with_name("offline").rename(root)
    target.write_bytes(original)
    assert sources.read(path)["revision"] == first["revision"]


def test_registry_rereads_and_allowlist_does_not_expose_sensitive_files(source):
    root, registry, sources = source
    (root / "README.md").write_text("readme")
    (root / "elsewhere.md").write_text("not included")
    (root / ".env.md").write_text("hidden secret")
    (root / "secrets.md").write_text("secret")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "secret.md").write_text("dependency")
    assert [e["path"] for e in sources.scan(["projects"])] == ["projects/demo/README.md"]
    data = json.loads(registry.read_text())
    data["projects"][0].update(include=["**/*.md"], exclude=[])
    registry.write_text(json.dumps(data))
    assert [e["path"] for e in sources.scan(["projects"])] == [
        "projects/demo/README.md", "projects/demo/elsewhere.md"]
    with pytest.raises(SourceError) as hidden:
        sources.read("projects/demo/.env.md")
    assert hidden.value.status == 403
    registry.write_text("corrupt")
    with pytest.raises(SourceError) as corrupt:
        sources.scan(["projects"])
    assert corrupt.value.status == 503


def test_only_authorized_subtrees_are_read(source):
    root, registry, sources = source
    (root / "docs" / "public.md").write_text("visible")
    (root / "docs" / "bad.md").write_bytes(b"\xff")
    assert sources.scan(["projects/demo/docs/public.md"])[0]["content"] == "visible"
    with pytest.raises(SourceError) as failure:
        sources.scan(["projects/demo/docs"])
    assert failure.value.code == "SOURCE_NOT_UTF8"


def test_source_symlink_cannot_escape_or_become_an_authority(source, tmp_path):
    root, registry, sources = source
    outside = tmp_path / "outside.md"
    outside.write_text("secret outside")
    link = root / "docs" / "escape.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation unavailable on this host")
    with pytest.raises(SourceError) as error:
        sources.read("projects/demo/docs/escape.md")
    assert error.value.status == 403
    with pytest.raises(SourceError):
        sources.scan(["projects/demo/docs"])


def test_source_directory_link_cannot_escape(source, tmp_path):
    root, _, sources = source
    outside = tmp_path / "external"
    outside.mkdir()
    (outside / "secret.md").write_text("outside secret")
    link = root / "docs" / "linked"
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                capture_output=True, check=False)
        if result.returncode:
            pytest.skip("Junction creation unavailable")
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(SourceError) as error:
            sources.read("projects/demo/docs/linked/secret.md")
        assert error.value.code == "SOURCE_LINK_FORBIDDEN"
        with pytest.raises(SourceError):
            sources.scan(["projects/demo/docs"])
    finally:
        if os.name == "nt":
            os.rmdir(link)  # Remove only the disposable junction, never its target.
        else:
            link.unlink()
    assert (outside / "secret.md").read_text() == "outside secret"


@pytest.mark.parametrize("path", [
    "projects/demo/../secret.md", "projects/demo/%2e%2e/secret.md",
    "projects/demo/docs\\secret.md", "projects/demo/docs//a.md", "/projects/demo/docs/a.md",
    "projects/demo/docs/a.md?secret", "projects/demo/docs/a.md:stream", "projects/demo/docs/a.md/",
])
def test_ambiguous_paths_are_rejected(path):
    with pytest.raises(ValueError):
        logical_path(path)


@pytest.mark.parametrize("data", [
    {"schema": 2, "projects": []}, {"schema": True, "projects": []},
    {"schema": 1, "projects": [], "extra": True},
    {"schema": 1, "projects": [{"name": "../demo", "root": "relative"}]},
    {"schema": 1, "projects": [{"name": "demo", "root": "relative"}]},
])
def test_registry_schema_fails_closed(data):
    with pytest.raises(ValueError):
        validate_registry(data)


def test_paths_unknown_date_and_scope_intersection():
    uri = "viking://resources/captures/" + "a" * 64 + "/content.md"
    assert capture_path(uri, {"kind": "note"}).startswith("captures/unknown/note/")
    assert effective_scopes("", ("projects/demo/docs", "captures/2026-10-11")) == [
        "captures/2026-10-11", "projects/demo/docs"]
    assert effective_scopes("projects/demo", ("projects/demo/docs",)) == ["projects/demo/docs"]
    assert effective_scopes("projects/other", ("projects/demo/docs",)) == []
    assert not allowed("projects/demolition/a.md", ("projects/demo",))


def test_default_include_prunes_unrelated_directories_and_files(source, monkeypatch):
    root, _, sources = source
    (root / "experiments").mkdir()
    (root / "experiments" / "unreadable.md").write_bytes(b"\xff")
    (root / "docs" / "ok.md").write_text("included")
    import memory_base.sources as module
    original = module.os.scandir
    visited = []

    def scan(path):
        visited.append(Path(path).name)
        assert Path(path).name != "experiments"
        return original(path)

    monkeypatch.setattr(module.os, "scandir", scan)
    assert len(sources.scan(["projects/demo"])) == 1
    assert visited == ["project", "docs"]


def test_source_size_cap_is_explicit_and_configurable(source):
    root, registry, sources = source
    (root / "README.md").write_bytes(b"x" * 21)
    capped = SourceRegistry(registry, max_bytes=20)
    with pytest.raises(SourceError) as exc:
        capped.read("projects/demo/README.md")
    assert exc.value.status == 413 and exc.value.code == "SOURCE_TOO_LARGE"
    assert SourceRegistry(registry, max_bytes=21).read("projects/demo/README.md")["bytes"] == 21


def test_chinese_bigrams_and_latin_terms_rank_actual_topic_above_shared_characters():
    query = "只读项目来源 revision"
    relevant = "只读项目来源保留原文件，通过 revision 校验哈希。"
    unrelated = "只言片语，阅读日记，一项工作，目光来源于绘画。"
    assert lexical_score(query, relevant) == 1.0
    assert lexical_score(query, unrelated) < .3
    assert lexical_score("OpenViking", "MemOS 框架") == 0
    assert lexical_score("恢复备份", "恢复备份保留ACL") > lexical_score("恢复备份", "从容恢复并准备其他资料")


def test_access_rules_normalize_root_preserve_deny_and_default_omission():
    assert validate_access({"schema": 1, "profiles": {
        "codex": {"read_scopes": ["root"], "write_scopes": []}, "reader": {},
    }}) == {"schema": 1, "profiles": {
        "codex": {"read_scopes": [""], "write_scopes": []}, "reader": {}}}
    for invalid in [{"schema": 1, "profiles": {"unknown": {}}},
                    {"schema": 1, "profiles": {"codex": {"secret": "no"}}},
                    {"schema": 1, "profiles": {"codex": {"read_scopes": None}}}]:
        with pytest.raises(ValueError):
            validate_access(invalid)
