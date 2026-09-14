"""写入网关（#11）+ 生命周期工具（#12）：去重只报告、frontmatter 校验、
路径级单文件提交、supersede/archive 的确认闸门与不删文件。

离线、确定性：不加载 BGE-M3，不碰 Qdrant（索引用 FakeIndex），KB 用临时 git 仓库。
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "ragcore"))

from memory_agent.memory.writer import MemoryWriteError, MemoryWriter  # noqa: E402


class FakeIndex:
    """只实现写入网关用到的那部分 MemoryIndex 契约（search + get）。"""

    def __init__(self, hits=None, entries=None):
        self.hits = hits or []
        self.entries = entries or {}
        self.last = None

    def search(self, query, k=5, writable_only=False):
        self.last = {"query": query, "k": k, "writable_only": writable_only}
        return self.hits[:k]

    def get(self, entry_id):
        if entry_id not in self.entries:
            raise KeyError(entry_id)
        meta = self.entries[entry_id]
        with open(meta["path"], "r", encoding="utf-8") as handle:
            content = handle.read()
        return {"id": entry_id, "content": content, **meta}


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


def _seed(repo, entry_id="topics/existing", status="current", title="Existing topic",
          body="Original body.", writable=True):
    """在临时 KB 里放一条已提交的条目，并返回配套的 FakeIndex。"""
    rel = f"{entry_id}.md"
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        "---\n"
        f"id: {entry_id}\n"
        f"title: {json.dumps(title, ensure_ascii=False)}\n"
        "type: topic\n"
        "tags: [demo]\n"
        f"status: {status}\n"
        "updated: 2026-09-01\n"
        "---\n\n"
        f"# {title}\n\n{body}\n"
    )
    path.write_text(text, encoding="utf-8")
    _git(repo, "add", "--", rel)
    _git(repo, "commit", "-q", "-m", f"seed {entry_id}")
    index = FakeIndex(entries={
        entry_id: {"path": str(path), "writable": writable,
                   "title": title, "status": status},
    })
    return path, index


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


def _supersede(writer, old_id="topics/existing", **overrides):
    params = dict(
        old_id=old_id, title="Replacement topic", body="The corrected fact.",
        domain="topics", type="topic", tags=["demo"], slug="replacement-topic",
    )
    params.update(overrides)
    return writer.supersede(**params)


def test_supersede_requires_confirmation_and_writes_nothing(tmp_path):
    repo = _repo(tmp_path)
    old_path, index = _seed(repo)
    old_before = old_path.read_text(encoding="utf-8")
    head = _git(repo, "rev-parse", "HEAD").strip()

    result = _supersede(MemoryWriter(index, kb_dir=str(repo)), today="2026-09-14")

    assert result["status"] == "confirmation_required"
    assert result["written"] is False
    assert result["action"] == "supersede"
    assert result["preview"]["old"]["id"] == "topics/existing"
    assert result["preview"]["new"]["id"] == "topics/replacement-topic"
    assert result["preview"]["commit_scope"] == [
        "topics/replacement-topic.md", "topics/existing.md"
    ]
    assert not (repo / "topics" / "replacement-topic.md").exists()
    assert old_path.read_text(encoding="utf-8") == old_before
    assert _git(repo, "rev-parse", "HEAD").strip() == head


def test_supersede_confirm_true_annotates_both_and_commits_once(tmp_path):
    repo = _repo(tmp_path)
    old_path, index = _seed(repo)

    result = _supersede(
        MemoryWriter(index, kb_dir=str(repo)), confirm=True, today="2026-09-14"
    )

    assert result["status"] == "written"
    assert result["old_id"] == "topics/existing"
    assert result["new_id"] == "topics/replacement-topic"

    new_text = (repo / "topics" / "replacement-topic.md").read_text(encoding="utf-8")
    assert "supersedes: topics/existing" in new_text
    assert "status: current" in new_text

    old_text = old_path.read_text(encoding="utf-8")
    assert "status: superseded" in old_text
    assert "superseded_by: topics/replacement-topic" in old_text
    assert "updated: 2026-09-14" in old_text
    assert "Original body." in old_text  # 正文原样保留
    assert old_path.exists()  # 任何路径都不删文件

    assert result["commit"] == _git(repo, "rev-parse", "HEAD").strip()
    assert sorted(_git(repo, "show", "--name-only", "--format=", "HEAD").split()) == [
        "topics/existing.md", "topics/replacement-topic.md"
    ]


def test_supersede_refuses_when_target_path_exists(tmp_path):
    repo = _repo(tmp_path)
    _, index = _seed(repo)
    (repo / "topics" / "replacement-topic.md").write_text("taken\n", encoding="utf-8")
    head = _git(repo, "rev-parse", "HEAD").strip()

    with pytest.raises(MemoryWriteError):
        _supersede(MemoryWriter(index, kb_dir=str(repo)), confirm=True)

    assert _git(repo, "rev-parse", "HEAD").strip() == head


def test_supersede_refuses_unknown_id(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(MemoryWriteError):
        _supersede(MemoryWriter(FakeIndex(), kb_dir=str(repo)))


def test_supersede_refuses_non_current_old_entry(tmp_path):
    repo = _repo(tmp_path)
    _, index = _seed(repo, status="archived")

    with pytest.raises(MemoryWriteError):
        _supersede(MemoryWriter(index, kb_dir=str(repo)), confirm=True)


def test_supersede_refuses_readonly_corpus(tmp_path):
    repo = _repo(tmp_path)
    _, index = _seed(repo, writable=False)

    with pytest.raises(MemoryWriteError):
        _supersede(MemoryWriter(index, kb_dir=str(repo)), confirm=True)


def test_supersede_kb_check_failure_rolls_back_both_files(tmp_path):
    repo = _repo(tmp_path)
    old_path, index = _seed(repo)
    old_before = old_path.read_text(encoding="utf-8")
    tools = repo / "tools"
    tools.mkdir()
    (tools / "kb.py").write_text(
        "import sys\n"
        "print('ERROR: topics/replacement-topic.md: bad thing')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    head = _git(repo, "rev-parse", "HEAD").strip()

    with pytest.raises(MemoryWriteError):
        _supersede(MemoryWriter(index, kb_dir=str(repo)), confirm=True)

    assert not (repo / "topics" / "replacement-topic.md").exists()
    assert old_path.read_text(encoding="utf-8") == old_before
    assert _git(repo, "rev-parse", "HEAD").strip() == head


def test_archive_requires_confirmation_and_writes_nothing(tmp_path):
    repo = _repo(tmp_path)
    old_path, index = _seed(repo)
    old_before = old_path.read_text(encoding="utf-8")
    head = _git(repo, "rev-parse", "HEAD").strip()

    result = MemoryWriter(index, kb_dir=str(repo)).archive(
        entry_id="topics/existing", reason="被新事实取代", today="2026-09-14"
    )

    assert result["status"] == "confirmation_required"
    assert result["written"] is False
    assert result["action"] == "archive"
    assert result["preview"]["changes"]["archive_reason"] == "被新事实取代"
    assert old_path.read_text(encoding="utf-8") == old_before
    assert _git(repo, "rev-parse", "HEAD").strip() == head


def test_archive_confirm_true_marks_only_and_keeps_file(tmp_path):
    repo = _repo(tmp_path)
    old_path, index = _seed(repo)

    result = MemoryWriter(index, kb_dir=str(repo)).archive(
        entry_id="topics/existing", reason="被新事实取代",
        confirm=True, today="2026-09-14",
    )

    assert result["status"] == "written"
    assert result["action"] == "archive"
    text = old_path.read_text(encoding="utf-8")
    assert "status: archived" in text
    assert "archive_reason: 被新事实取代" in text
    assert "updated: 2026-09-14" in text
    assert "Original body." in text  # 正文原样保留
    assert old_path.exists()  # 不删文件
    assert _git(repo, "show", "--name-only", "--format=", "HEAD").split() == [
        "topics/existing.md"
    ]


def test_archive_refuses_empty_reason(tmp_path):
    repo = _repo(tmp_path)
    _, index = _seed(repo)

    with pytest.raises(MemoryWriteError):
        MemoryWriter(index, kb_dir=str(repo)).archive(
            entry_id="topics/existing", reason="  ", confirm=True
        )


def test_archive_refuses_already_archived_entry(tmp_path):
    repo = _repo(tmp_path)
    _, index = _seed(repo, status="archived")

    with pytest.raises(MemoryWriteError):
        MemoryWriter(index, kb_dir=str(repo)).archive(
            entry_id="topics/existing", reason="again", confirm=True
        )


def test_mcp_surface_exposes_only_scoped_tools():
    from memory_agent import mcp_server

    tools = {tool.name: tool for tool in mcp_server.mcp._tool_manager.list_tools()}
    assert set(tools) == {
        "memory_search", "memory_get", "memory_add", "memory_supersede", "memory_archive",
    }
    assert set(tools["memory_add"].parameters["properties"]) == {
        "title", "body", "domain", "type", "tags", "slug", "sources",
        "status", "allow_duplicate",
    }
    assert set(tools["memory_supersede"].parameters["properties"]) == {
        "old_id", "title", "body", "domain", "type", "tags", "slug", "sources", "confirm",
    }
    assert set(tools["memory_archive"].parameters["properties"]) == {
        "entry_id", "reason", "confirm",
    }
