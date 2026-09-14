"""MemoryIndex 走真实 Qdrant 的整链路：rebuild（含清空重建）→ search → 过滤 → get。

MCP 层已在 issue #10 用官方 client 实连验证；这里锁住引擎侧行为，不加载 BGE-M3。
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "ragcore"))

from memory_agent.memory.entries import Entry  # noqa: E402
from memory_agent.memory.index import MemoryIndex  # noqa: E402
from services.vector_store_service import VectorStoreService  # noqa: E402
from utils.model_status import EMBEDDING_DIMENSION  # noqa: E402


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
    store = VectorStoreService(collection_name="mem", db_path=str(tmp_path / "qdrant"),
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
