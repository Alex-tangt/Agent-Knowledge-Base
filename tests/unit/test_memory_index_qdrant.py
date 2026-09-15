"""MemoryIndex 走真实 Qdrant 的整链路：rebuild（含清空重建）→ search → 过滤 → get。

MCP 层已在 issue #10 用官方 client 实连验证；这里锁住引擎侧行为，不加载 BGE-M3。
"""
import os

from memory_agent.memory.entries import Entry  # noqa: E402
from memory_agent.memory.index import MemoryIndex  # noqa: E402
from memory_agent.memory.store import QdrantLocalStore  # noqa: E402
from ragcore.utils.model_status import EMBEDDING_DIMENSION  # noqa: E402


class StubEmbeddings:
    def embed_query(self, text):
        return [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]


def _entry(tmp_path, name, content, writable, entry_id):
    path = os.path.join(str(tmp_path), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return Entry.from_file(path, source=name, writable=writable, entry_id=entry_id)


def test_rebuild_search_filter_and_get_on_real_qdrant(tmp_path):
    store = QdrantLocalStore(db_path=str(tmp_path / "qdrant"), collection_name="mem",
                             embeddings=StubEmbeddings())
    index = MemoryIndex(store=store, manifest_path=str(tmp_path / "manifest.json"))
    entries = [
        _entry(tmp_path, "kb/a.md", '---\nid: a\ntitle: "A"\n---\n\n# A\n\nkb body\n',
               writable=True, entry_id="a"),
        _entry(tmp_path, "repo/b.md", "# B\n\nrepo body\n", writable=False, entry_id="repo:b"),
    ]

    stats = index.rebuild(entries)
    assert stats == {**stats, "entries": 2, "writable": 1, "readonly": 1}

    hits = index.search("body", k=2)
    assert {h["id"] for h in hits} == {"a", "repo:b"}

    writable_hits = index.search("body", k=2, writable_only=True)
    assert [h["id"] for h in writable_hits] == ["a"]

    assert index.get("a")["content"].startswith("---")

    # 再来一次：清空重建不得残留旧点
    index.rebuild(entries)
    assert len(index.search("body", k=5)) == 2


def test_incremental_refresh_skips_unchanged_and_drops_orphans(tmp_path):
    store = QdrantLocalStore(db_path=str(tmp_path / "qdrant"), collection_name="mem",
                             embeddings=StubEmbeddings())
    index = MemoryIndex(store=store, manifest_path=str(tmp_path / "manifest.json"))
    a = _entry(tmp_path, "kb/a.md", '---\nid: a\ntitle: "A"\n---\n\n# A\n\nbody a\n',
               writable=True, entry_id="a")
    b = _entry(tmp_path, "kb/b.md", '---\nid: b\ntitle: "B"\n---\n\n# B\n\nbody b\n',
               writable=True, entry_id="b")
    index.rebuild([a, b])

    c = _entry(tmp_path, "kb/c.md", '---\nid: c\ntitle: "C"\n---\n\n# C\n\nbody c\n',
               writable=True, entry_id="c")
    index._entry_loader = lambda: [a, c]  # b 消失（删/改名）、a 未变、c 新增

    stats = index.refresh()

    assert stats == {"entries": 2, "added": 1, "updated": 0, "skipped": 1,
                     "removed": 1, "embedded": 1}
    assert {h["id"] for h in index.search("body", k=5)} == {"a", "c"}
    assert index.status()["consistent"] is True
