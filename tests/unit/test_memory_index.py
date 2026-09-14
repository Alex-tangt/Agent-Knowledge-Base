"""MemoryIndex：全量重建 / 检索映射 / 读回真相源（用假 store，不碰模型与 Qdrant）。"""
import json
import os

import pytest

from memory_agent.memory.entries import Entry
from memory_agent.memory.index import MemoryIndex


class FakeStore:
    """只实现 MemoryIndex 用到的那部分 VectorStoreService 契约。"""

    def __init__(self, hits=None):
        self.cleared = 0
        self.texts = []
        self.metas = []
        self._hits = hits or []

    def clear_all_documents(self):
        self.cleared += 1
        self.texts = []
        self.metas = []

    def add_documents(self, documents, metadata_list=None):
        self.texts.extend(list(documents))
        self.metas.extend(list(metadata_list or []))
        return []

    def search_documents(self, query, k=3):
        hits = self._hits[:k]
        if hits:
            return {
                "documents": [[h["text"] for h in hits]],
                "metadatas": [[h["meta"] for h in hits]],
                "distances": [[h["score"] for h in hits]],
            }
        return {
            "documents": [self.texts[:k]],
            "metadatas": [self.metas[:k]],
            "distances": [[1.0 - i * 0.1 for i in range(len(self.texts[:k]))]],
        }


def _entry(tmp_path, name, content, writable=True, entry_id=None):
    path = os.path.join(tmp_path, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return Entry.from_file(path, source=name, writable=writable, entry_id=entry_id)


def test_rebuild_clears_writes_and_flags(tmp_path):
    store = FakeStore()
    index = MemoryIndex(store=store, manifest_path=os.path.join(tmp_path, "manifest.json"))
    entries = [
        _entry(tmp_path, "kb/a.md", "# A\n", writable=True, entry_id="a"),
        _entry(tmp_path, "repo/b.md", "# B\n", writable=False, entry_id="repo:b"),
    ]

    stats = index.rebuild(entries)

    assert store.cleared == 1
    assert len(store.texts) == 2
    assert {m["entry_id"] for m in store.metas} == {"a", "repo:b"}
    assert stats["entries"] == 2
    assert stats["writable"] == 1
    assert stats["readonly"] == 1
    assert index.is_built


def test_manifest_round_trip(tmp_path):
    manifest = os.path.join(tmp_path, "manifest.json")
    store = FakeStore()
    entries = [_entry(tmp_path, "kb/a.md", "# A\n", entry_id="a")]
    MemoryIndex(store=store, manifest_path=manifest).rebuild(entries)

    with open(manifest, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    assert data["entries"]["a"]["writable"] is True

    reloaded = MemoryIndex(store=FakeStore(), manifest_path=manifest)
    assert reloaded.is_built
    assert reloaded.stats()["entries"] == 1
    assert reloaded.get("a")["content"].startswith("# A")


def test_empty_corpus_writes_manifest_without_adding(tmp_path):
    store = FakeStore()
    index = MemoryIndex(store=store, manifest_path=os.path.join(tmp_path, "manifest.json"))

    stats = index.rebuild([])

    assert store.texts == []
    assert stats["entries"] == 0
    assert not index.is_built


def test_search_maps_payloads_and_sorts_by_score(tmp_path):
    hits = [
        {"text": "low", "score": 0.2, "meta": {"entry_id": "low", "title": "Low",
                                              "writable": False, "tags": [], "type": None,
                                              "status": None, "source": "r/low.md"}},
        {"text": "high", "score": 0.9, "meta": {"entry_id": "high", "title": "High",
                                               "writable": True, "tags": ["x"], "type": "topic",
                                               "status": "current", "source": "kb/high.md"}},
    ]
    index = MemoryIndex(store=FakeStore(hits), manifest_path=os.path.join(tmp_path, "m.json"))

    results = index.search("q", k=2)

    assert [r["id"] for r in results] == ["high", "low"]
    assert results[0]["writable"] is True
    assert results[0]["tags"] == ["x"]
    assert results[0]["snippet"] == "high"


def test_search_writable_only_filters_readonly(tmp_path):
    hits = [
        {"text": "a", "score": 0.9, "meta": {"entry_id": "a", "title": "A", "writable": False,
                                             "source": "r/a.md"}},
        {"text": "b", "score": 0.5, "meta": {"entry_id": "b", "title": "B", "writable": True,
                                             "source": "kb/b.md"}},
    ]
    index = MemoryIndex(store=FakeStore(hits), manifest_path=os.path.join(tmp_path, "m.json"))

    results = index.search("q", k=2, writable_only=True)

    assert [r["id"] for r in results] == ["b"]


def test_search_rejects_empty_query(tmp_path):
    index = MemoryIndex(store=FakeStore(), manifest_path=os.path.join(tmp_path, "m.json"))
    with pytest.raises(ValueError):
        index.search("   ")


def test_get_returns_real_markdown_and_rejects_unknown(tmp_path):
    store = FakeStore()
    index = MemoryIndex(store=store, manifest_path=os.path.join(tmp_path, "m.json"))
    entry = _entry(tmp_path, "kb/a.md", '---\nid: a\ntitle: "A"\n---\n\n# A\n\nbody\n')
    index.rebuild([entry])

    got = index.get("a")
    assert got["id"] == "a"
    assert got["title"] == "A"
    assert got["content"].startswith("---")
    assert "body" in got["content"]

    with pytest.raises(KeyError):
        index.get("missing")


def test_get_raises_when_source_file_is_gone(tmp_path):
    store = FakeStore()
    index = MemoryIndex(store=store, manifest_path=os.path.join(tmp_path, "m.json"))
    entry = _entry(tmp_path, "kb/gone.md", "# Gone\n", entry_id="gone")
    index.rebuild([entry])
    os.remove(entry.path)

    with pytest.raises(FileNotFoundError):
        index.get("gone")
