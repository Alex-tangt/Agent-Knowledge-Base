"""corpus 装载与条目解析（不依赖模型 / 向量库）。"""
import os

from memory_agent.corpus.loader import (
    load_corpus,
    load_kb_entries,
    load_readonly_entries,
)
from memory_agent.memory.entries import Entry, parse_frontmatter


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


# ---- frontmatter 解析 ----

def test_parse_frontmatter_with_valid_block():
    meta, body = parse_frontmatter('---\nid: a/b\ntags: [x, y]\n---\n\n# T\n')
    assert meta["id"] == "a/b"
    assert meta["tags"] == ["x", "y"]
    assert body.strip() == "# T"


def test_parse_frontmatter_without_block_returns_text():
    meta, body = parse_frontmatter("# T\n\nbody\n")
    assert meta == {}
    assert body == "# T\n\nbody\n"


def test_parse_frontmatter_with_invalid_yaml_returns_text():
    text = "---\nid: [unclosed\n---\nbody\n"
    meta, body = parse_frontmatter(text)
    assert meta == {}
    assert body == text


# ---- Entry ----

def test_entry_uses_frontmatter_id_and_fields(tmp_path):
    path = _write(os.path.join(tmp_path, "e.md"),
                  '---\nid: topics/x\ntitle: "X"\ntype: topic\ntags: a, b\n'
                  'status: current\nupdated: 2026-09-14\n---\n\n# X\n\nbody\n')
    entry = Entry.from_file(path, source="topics/x.md", writable=True)
    assert entry.id == "topics/x"
    assert entry.title == "X"
    assert entry.type == "topic"
    assert entry.tags == ["a", "b"]
    assert entry.status == "current"
    assert entry.writable is True
    assert entry.body.strip() == "# X\n\nbody"


def test_entry_without_frontmatter_gets_repo_id_and_heading_title(tmp_path):
    path = _write(os.path.join(tmp_path, "plain.md"), "# Heading\n\ntext\n")
    entry = Entry.from_file(path, source="docs/plain.md", writable=False)
    assert entry.id == "repo:docs/plain.md"
    assert entry.title == "Heading"
    assert entry.writable is False


def test_entry_hash_is_stable_and_content_sensitive(tmp_path):
    path = _write(os.path.join(tmp_path, "h.md"), "# H\n\nA\n")
    first = Entry.from_file(path, source="h.md", writable=False)
    same = Entry.from_file(path, source="h.md", writable=False)
    _write(path, "# H\n\nB\n")
    second = Entry.from_file(path, source="h.md", writable=False)
    assert first.content_hash == same.content_hash
    assert first.content_hash != second.content_hash


# ---- KB 装载 ----

def test_load_kb_entries_keeps_only_real_entries(tmp_path):
    _write(os.path.join(tmp_path, "topics", "keep.md"),
           '---\nid: topics/keep\n---\n\n# Keep\n')
    _write(os.path.join(tmp_path, "topics", "_index.md"), "# generated\n")
    _write(os.path.join(tmp_path, "INDEX.md"), "# meta\n")
    _write(os.path.join(tmp_path, "AGENTS.md"), "# conventions\n")
    _write(os.path.join(tmp_path, "topics", "no-id.md"), "# no frontmatter id\n")

    entries = load_kb_entries(str(tmp_path))

    assert [e.id for e in entries] == ["topics/keep"]
    assert entries[0].writable is True


# ---- 只读装载与排除 ----

def test_load_readonly_entries_excludes_noise_dirs_and_raw_data(tmp_path):
    _write(os.path.join(tmp_path, "docs", "a.md"), "# A\n")
    _write(os.path.join(tmp_path, "legal_web", "data", "raw", "law.md"), "# Law\n")
    _write(os.path.join(tmp_path, "venv", "lib", "b.md"), "# B\n")
    _write(os.path.join(tmp_path, ".git", "c.md"), "# C\n")
    _write(os.path.join(tmp_path, "node_modules", "d.md"), "# D\n")
    _write(os.path.join(tmp_path, "notes.txt"), "not markdown\n")

    entries = load_readonly_entries([str(tmp_path)])

    assert [e.source for e in entries] == ["docs/a.md"]
    assert entries[0].writable is False


def test_load_readonly_entries_skips_oversized_files(tmp_path, monkeypatch):
    with open(os.path.join(tmp_path, "big.md"), "wb") as handle:
        handle.write(b"x" * 100)
    with open(os.path.join(tmp_path, "small.md"), "wb") as handle:
        handle.write(b"# S\n")
    monkeypatch.setattr("memory_agent.corpus.loader.MAX_CORPUS_FILE_BYTES", 10)

    entries = load_readonly_entries([str(tmp_path)])

    assert [e.source for e in entries] == ["small.md"]


# ---- 合并 ----

def test_load_corpus_dedupes_and_flags_writable(tmp_path):
    kb = os.path.join(tmp_path, "kb")
    repo = os.path.join(tmp_path, "repo")
    _write(os.path.join(kb, "topics", "x.md"), '---\nid: topics/x\n---\n\n# KB X\n')
    _write(os.path.join(repo, "docs", "y.md"), "# Repo Y\n")

    entries = load_corpus(kb_dir=kb, readonly_roots=[repo])

    by_id = {e.id: e for e in entries}
    assert set(by_id) == {"topics/x", "repo:docs/y.md"}
    assert by_id["topics/x"].writable is True
    assert by_id["repo:docs/y.md"].writable is False
    assert entries[0].writable is True  # 可写条目排在前
