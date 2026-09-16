"""#23 VectorStore 端口 + 条目 classification/residency + provenance。

锁外部可观察行为：端口形状（`isinstance` 协议核对）、tenant 收窄不可放宽、
payload 镜像字段默认值 / 解析 / 检索命中带 provenance。用 Stub 嵌入，不加载 BGE-M3。
"""
import os

from memory_agent.memory.entries import Entry, point_id_for  # noqa: E402
from memory_agent.memory.index import MemoryIndex  # noqa: E402
from memory_agent.memory.ports import (  # noqa: E402
    DEFAULT_CLASSIFICATION,
    DEFAULT_RESIDENCY,
    PLANE_LOCAL,
    VectorStore,
)
from memory_agent.memory.store import QdrantLocalStore, open_store  # noqa: E402
from ragcore.utils.model_status import EMBEDDING_DIMENSION  # noqa: E402


class StubEmbeddings:
    def embed_query(self, text):
        return [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]


def _entry(tmp_path, name, content, writable=True, entry_id=None):
    path = os.path.join(str(tmp_path), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return Entry.from_file(path, source=name, writable=writable, entry_id=entry_id)


# ------------------------------------------------------- 字段：默认 / 解析 / 镜像

def test_entry_defaults_classification_and_residency(tmp_path):
    entry = _entry(tmp_path, "kb/a.md", "# A\n\nbody\n", entry_id="a")
    assert entry.classification == "private"
    assert entry.residency == "local"
    assert entry.to_payload(6000)["classification"] == "private"
    assert entry.to_payload(6000)["residency"] == "local"
    assert entry.to_manifest()["classification"] == "private"


def test_entry_reads_optional_frontmatter_fields(tmp_path):
    entry = _entry(
        tmp_path, "kb/b.md",
        '---\nid: b\ntitle: "B"\nclassification: public\nresidency: cloud\n---\n\n# B\n',
        entry_id="b",
    )
    assert entry.classification == "public"
    assert entry.residency == "cloud"


def test_entry_invalid_choice_falls_back_to_default(tmp_path):
    entry = _entry(
        tmp_path, "kb/c.md",
        '---\nid: c\ntitle: "C"\nclassification: secret\nresidency: mars\n---\n\n# C\n',
        entry_id="c",
    )
    assert entry.classification == DEFAULT_CLASSIFICATION
    assert entry.residency == DEFAULT_RESIDENCY


# ------------------------------------------------------------- 端口：形状与租户

def test_local_adapter_satisfies_port_protocol(tmp_path):
    store = QdrantLocalStore(db_path=str(tmp_path / "q"), embeddings=StubEmbeddings())
    assert isinstance(store, VectorStore)
    assert store.plane == PLANE_LOCAL
    assert store.count() == 0
    assert open_store(str(tmp_path / "q2"), tenant="org-a").tenant == "org-a"


def test_search_filters_by_bound_tenant_and_cannot_be_widened(tmp_path):
    store = QdrantLocalStore(db_path=str(tmp_path / "q"), embeddings=StubEmbeddings(),
                             tenant="org-a")
    store.add(
        ["alpha doc", "beta doc"],
        metadata_list=[{"entry_id": "a", "tenant": "org-a"},
                       {"entry_id": "b", "tenant": "org-b"}],
        ids=[point_id_for("a"), point_id_for("b")],
    )

    hits = store.search("doc", k=5)
    assert [m["entry_id"] for m in hits["metadatas"][0]] == ["a"]

    # 调用方试图用 payload_filter 放宽租户 → 仍被绑定租户收窄（ADR-0018 D2）
    widened = store.search("doc", k=5, payload_filter={"tenant": "org-b"})
    assert [m["entry_id"] for m in widened["metadatas"][0]] == ["a"]


def test_search_tenant_parameter_used_when_unbound(tmp_path):
    store = QdrantLocalStore(db_path=str(tmp_path / "q"), embeddings=StubEmbeddings())
    store.add(
        ["alpha doc", "beta doc"],
        metadata_list=[{"entry_id": "a", "tenant": "org-a"},
                       {"entry_id": "b", "tenant": "org-b"}],
        ids=[point_id_for("a"), point_id_for("b")],
    )
    hits = store.search("doc", k=5, tenant="org-b")
    assert [m["entry_id"] for m in hits["metadatas"][0]] == ["b"]


# ------------------------------------------- MemoryIndex 只经工厂拿存储（不碰 Qdrant）

class _MiniStore:
    """最小 VectorStore 端口实现：证明 MemoryIndex 只依赖端口。"""

    plane = PLANE_LOCAL
    tenant = None

    def __init__(self):
        self.docs = {}

    def clear(self):
        self.docs.clear()

    def add(self, texts, metadata_list=None, ids=None):
        metas = list(metadata_list or [])
        ids = list(ids) if ids is not None else [str(i) for i in range(len(texts))]
        for point_id, text, meta in zip(ids, texts, metas):
            self.docs[point_id] = (text, meta)
        return ids

    def count(self):
        return len(self.docs)

    def delete(self, ids):
        for point_id in ids:
            self.docs.pop(point_id, None)
        return True

    def search(self, query, k=3, payload_filter=None, tenant=None):
        return {"documents": [[]], "metadatas": [[]], "distances": [[]]}

    search_documents = search

    def search_by_keywords(self, keywords, source_filter=None):
        return []

    def warmup(self):
        pass


def test_memory_index_builds_store_through_factory(tmp_path):
    built = []

    def factory(db_path):
        built.append(db_path)
        return _MiniStore()

    index = MemoryIndex(
        manifest_path=str(tmp_path / "manifest.json"), db_path="gen-1/qdrant",
        store_factory=factory,
    )
    index.rebuild([_entry(tmp_path, "kb/a.md", "# A\n", entry_id="a")])

    assert built == ["gen-1/qdrant"]
    assert index.status()["points"] == 1


# --------------------------------------------------- 检索命中：镜像字段 + provenance

def test_memory_search_hits_carry_classification_and_provenance(tmp_path):
    store = QdrantLocalStore(db_path=str(tmp_path / "q"), embeddings=StubEmbeddings())
    index = MemoryIndex(store=store, manifest_path=str(tmp_path / "manifest.json"))
    index.rebuild([
        _entry(tmp_path, "kb/a.md",
               '---\nid: a\ntitle: "A"\nclassification: internal\nresidency: cloud\n'
               '---\n\n# A\n\nbody alpha\n', entry_id="a"),
    ])

    hit = index.search("body", k=1)[0]

    assert hit["classification"] == "internal"
    assert hit["residency"] == "cloud"
    assert hit["provenance"] == {"plane": PLANE_LOCAL, "tenant": None}


def test_memory_search_hits_expose_owner(tmp_path):
    """#45 / ADR-0025 D19：读侧「域」可见——命中带 owner。"""
    entry = _entry(tmp_path, "kb/a.md", "# A\n\nbody alpha\n", entry_id="a")
    entry.owner = "team-x"
    store = QdrantLocalStore(db_path=str(tmp_path / "q"), embeddings=StubEmbeddings())
    index = MemoryIndex(store=store, manifest_path=str(tmp_path / "manifest.json"))
    index.rebuild([entry])

    hit = index.search("body", k=1)[0]

    assert hit["owner"] == "team-x"
