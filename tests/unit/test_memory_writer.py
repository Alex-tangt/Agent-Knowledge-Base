"""写入网关（#11）：去重只报告、frontmatter 校验、路径级单文件提交。

离线、确定性：不加载 BGE-M3，不碰 Qdrant（索引用 FakeIndex），KB 用临时 git 仓库。
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "ragcore"))

from memory_agent.memory.writer import MemoryWriteError, MemoryWriter  # noqa: E402


class FakeIndex:
    """只实现写入网关用到的那部分 MemoryIndex 契约。"""

    def __init__(self, hits=None):
        self.hits = hits or []
        self.last = None

    def search(self, query, k=5, writable_only=False):
        self.last = {"query": query, "k": k, "writable_only": writable_only}
        return self.hits[:k]


def _git(repo, *args):
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _repo(tmp_path):
    repo = tmp_path / "kb"
    for domain in ("topics", "decisions", os.path.join("projects", "demo")):
        (repo / domain).mkdir(parents=True)
    (repo / "tags.md").write_text("- demo\n- topic-x\n", encoding="utf-8")
    (repo / "INDEX.md").write_text("# index\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _add(writer, **overrides):
    params = dict(
        title="Demo topic", body="A durable fact.", domain="topics",
        type="topic", tags=["demo"], slug="demo-topic",
    )
    params.update(overrides)
    return writer.add(**params)


def test_add_writes_valid_entry_and_commits_only_that_file(tmp_path):
    repo = _repo(tmp_path)
    (repo / "other.md").write_text("another session's work\n", encoding="utf-8")
    (repo / "INDEX.md").write_text("# index\n\nedited elsewhere\n", encoding="utf-8")
    _git(repo, "add", "INDEX.md")  # 别人的改动此刻还是 staged 状态

    head_before = _git(repo, "rev-parse", "HEAD").strip()
    result = _add(MemoryWriter(FakeIndex(), kb_dir=str(repo)), today="2026-09-14")

    assert result["status"] == "written"
    assert result["id"] == "topics/demo-topic"
    text = (repo / "topics" / "demo-topic.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "id: topics/demo-topic" in text
    assert "type: topic" in text
    assert "updated: 2026-09-14" in text

    assert result["commit"] == _git(repo, "rev-parse", "HEAD").strip()
    assert result["commit"] != head_before
    assert _git(repo, "show", "--name-only", "--format=", "HEAD").split() == [
        "topics/demo-topic.md"
    ]

    status = _git(repo, "status", "--short")
    assert "?? other.md" in status
    assert "M  INDEX.md" in status


def test_add_refuses_existing_path_without_touching_it(tmp_path):
    repo = _repo(tmp_path)
    existing = repo / "topics" / "demo-topic.md"
    existing.write_text("hand-written original\n", encoding="utf-8")
    head = _git(repo, "rev-parse", "HEAD").strip()

    result = _add(MemoryWriter(FakeIndex(), kb_dir=str(repo)))

    assert result["status"] == "duplicate"
    assert result["reason"] == "exists"
    assert result["written"] is False
    assert existing.read_text(encoding="utf-8") == "hand-written original\n"
    assert _git(repo, "rev-parse", "HEAD").strip() == head


def test_add_reports_semantic_duplicate_without_writing(tmp_path):
    repo = _repo(tmp_path)
    hits = [{"id": "topics/existing", "title": "Existing", "source": "topics/existing.md",
             "status": "current", "score": 0.95}]
    index = FakeIndex(hits)
    head = _git(repo, "rev-parse", "HEAD").strip()

    result = _add(MemoryWriter(index, kb_dir=str(repo)))

    assert result["status"] == "duplicate"
    assert result["reason"] == "semantic"
    assert result["candidates"][0]["id"] == "topics/existing"
    assert not (repo / "topics" / "demo-topic.md").exists()
    assert _git(repo, "rev-parse", "HEAD").strip() == head
    assert index.last["writable_only"] is True


def test_allow_duplicate_true_bypasses_the_semantic_gate(tmp_path):
    repo = _repo(tmp_path)
    hits = [{"id": "topics/existing", "title": "Existing", "source": "x",
             "status": "current", "score": 0.99}]

    result = _add(MemoryWriter(FakeIndex(hits), kb_dir=str(repo)), allow_duplicate=True)

    assert result["status"] == "written"


def test_unknown_tag_is_a_warning_and_still_writes(tmp_path):
    repo = _repo(tmp_path)

    result = _add(MemoryWriter(FakeIndex(), kb_dir=str(repo)), tags=["not-in-vocab"])

    assert result["status"] == "written"
    assert any("not-in-vocab" in warning for warning in result["warnings"])


def test_type_must_match_domain(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(MemoryWriteError):
        _add(MemoryWriter(FakeIndex(), kb_dir=str(repo)), domain="projects/demo",
             type="topic")


def test_non_ascii_title_requires_an_explicit_slug(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(MemoryWriteError):
        _add(MemoryWriter(FakeIndex(), kb_dir=str(repo)), title="纯中文标题", slug=None)


def test_kb_check_failure_removes_file_and_skips_commit(tmp_path):
    repo = _repo(tmp_path)
    tools = repo / "tools"
    tools.mkdir()
    (tools / "kb.py").write_text(
        "import sys\n"
        "print('ERROR: topics/demo-topic.md: missing title')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    head = _git(repo, "rev-parse", "HEAD").strip()

    with pytest.raises(MemoryWriteError):
        _add(MemoryWriter(FakeIndex(), kb_dir=str(repo)))

    assert not (repo / "topics" / "demo-topic.md").exists()
    assert _git(repo, "rev-parse", "HEAD").strip() == head


def test_mcp_surface_exposes_only_scoped_tools():
    from memory_agent import mcp_server

    tools = {tool.name: tool for tool in mcp_server.mcp._tool_manager.list_tools()}
    assert set(tools) == {"memory_search", "memory_get", "memory_add"}
    props = set(tools["memory_add"].parameters["properties"])
    assert props == {"title", "body", "domain", "type", "tags", "slug", "sources",
                     "status", "allow_duplicate"}
