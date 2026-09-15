"""MemoryIndex：全量重建 / 检索映射 / 读回真相源（用假 store，不碰模型与 Qdrant）。"""
import json
import os

import pytest

from memory_agent.memory.entries import Entry
from memory_agent.memory.errors import IndexConsistencyError
from memory_agent.memory.index import MemoryIndex


class FakeStore:
    """只实现 MemoryIndex 用到的那部分 `VectorStore` 端口契约。"""

    def __init__(self, hits=None, keyword_hits=None):
        self.cleared = 0
        self.texts = []
        self.metas = []
        self._ids = []
        self._hits = hits or []
        self._keyword_hits = keyword_hits or []
        self.last_filter = "unset"

    def clear(self):
        self.cleared += 1
        self.texts = []
        self.metas = []
        self._ids = []

    def add(self, documents, metadata_list=None, ids=None):
        docs = list(documents)
        metas = list(metadata_list or [])
        ids = list(ids) if ids is not None else [None] * len(docs)
        for doc, meta, point_id in zip(docs, metas, ids):
            if point_id is not None and point_id in self._ids:
                index = self._ids.index(point_id)
                self.texts[index] = doc
                self.metas[index] = meta
            else:
                self.texts.append(doc)
                self.metas.append(meta)
                self._ids.append(point_id)
        return ids

    def count(self):
        return len(self.metas)

    def delete(self, ids):
        wanted = set(str(i) for i in ids)
        kept_texts, kept_metas, kept_ids = [], [], []
        for doc, meta, point_id in zip(self.texts, self.metas, self._ids):
            if point_id in wanted:
                continue
            kept_texts.append(doc)
            kept_metas.append(meta)
            kept_ids.append(point_id)
        self.texts, self.metas, self._ids = kept_texts, kept_metas, kept_ids
        return True

    def search_by_keywords(self, keywords, source_filter=None):
        return self._keyword_hits

    def search_documents(self, query, k=3, payload_filter=None):
        self.last_filter = payload_filter
        hits = self._hits
        if payload_filter:
            hits = [h for h in hits
                    if all(h["meta"].get(key) == value for key, value in payload_filter.items())]
        hits = hits[:k]
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


def test_search_pushes_writable_filter_down_to_store(tmp_path):
    hits = [
        {"text": "a", "score": 0.9, "meta": {"entry_id": "a", "title": "A", "writable": False,
                                             "source": "r/a.md"}},
        {"text": "b", "score": 0.5, "meta": {"entry_id": "b", "title": "B", "writable": True,
                                             "source": "kb/b.md"}},
    ]
    store = FakeStore(hits)
    index = MemoryIndex(store=store, manifest_path=os.path.join(tmp_path, "m.json"))

    results = index.search("q", k=2, writable_only=True)

    assert store.last_filter == {"writable": True}
    assert [r["id"] for r in results] == ["b"]


def test_search_without_writable_only_passes_no_filter(tmp_path):
    store = FakeStore()
    index = MemoryIndex(store=store, manifest_path=os.path.join(tmp_path, "m.json"))

    index.search("q", k=2)

    assert store.last_filter is None


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


def test_refresh_embeds_only_changed_and_new(tmp_path):
    store = FakeStore()
    index = MemoryIndex(store=store, manifest_path=os.path.join(tmp_path, "m.json"))
    a = _entry(tmp_path, "kb/a.md", "# A\n\nbody-a\n", entry_id="a")
    b = _entry(tmp_path, "kb/b.md", "# B\n\nbody-b\n", entry_id="b")
    index.rebuild([a, b])

    a2 = _entry(tmp_path, "kb/a.md", "# A\n\nbody-a2\n", entry_id="a")
    c = _entry(tmp_path, "kb/c.md", "# C\n\nbody-c\n", entry_id="c")

    stats = index.refresh(entries=[a2, b, c])

    assert stats == {"entries": 3, "added": 1, "updated": 1, "skipped": 1,
                     "removed": 0, "embedded": 2}
    assert len(store.metas) == 3


def test_refresh_removes_orphans_without_residue(tmp_path):
    store = FakeStore()
    index = MemoryIndex(store=store, manifest_path=os.path.join(tmp_path, "m.json"))
    a = _entry(tmp_path, "kb/a.md", "# A\n", entry_id="a")
    b = _entry(tmp_path, "kb/b.md", "# B\n", entry_id="b")
    index.rebuild([a, b])

    stats = index.refresh(entries=[a])

    assert stats["removed"] == 1
    assert len(store.metas) == 1
    with pytest.raises(KeyError):
        index.get("b")


def test_refresh_on_empty_corpus_is_noop(tmp_path):
    index = MemoryIndex(store=FakeStore(), manifest_path=os.path.join(tmp_path, "m.json"))
    stats = index.refresh(entries=[])
    assert stats["entries"] == 0
    assert stats["embedded"] == 0


def test_search_routes_through_strategy_and_merges_keyword_hits(tmp_path):
    hits = [
        {"text": "vector doc", "score": 0.9,
         "meta": {"entry_id": "v", "title": "V", "writable": True, "source": "kb/v.md"}},
    ]
    keyword_hits = [
        {"document": "keyword doc", "matched": 1, "score": 3,
         "metadata": {"entry_id": "k", "title": "K", "writable": True, "source": "kb/k.md"}},
    ]
    index = MemoryIndex(store=FakeStore(hits, keyword_hits),
                        manifest_path=os.path.join(tmp_path, "m.json"))

    results = index.search("记忆检索", k=5)

    assert [r["id"] for r in results] == ["k", "v"]


def test_search_raises_when_manifest_and_points_disagree(tmp_path):
    store = FakeStore()
    index = MemoryIndex(store=store, manifest_path=os.path.join(tmp_path, "m.json"))
    index.rebuild([_entry(tmp_path, "kb/a.md", "# A\n", entry_id="a")])
    store.metas.clear()  # 模拟集合被清空、manifest 未更新的不自洽状态

    with pytest.raises(IndexConsistencyError):
        index.search("q")


def test_status_reports_consistency(tmp_path):
    store = FakeStore()
    index = MemoryIndex(store=store, manifest_path=os.path.join(tmp_path, "m.json"))
    index.rebuild([_entry(tmp_path, "kb/a.md", "# A\n", entry_id="a")])

    assert index.status()["consistent"] is True
    store.metas.clear()
    assert index.status()["consistent"] is False
