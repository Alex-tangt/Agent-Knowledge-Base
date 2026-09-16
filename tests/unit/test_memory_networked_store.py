"""#33 网络化 store 适配器（共享平面）：端口契约 + 原生 hybrid + 无双重融合 + DB 写入。

三种证据：
1. 不依赖外部服务的纯单测（稀疏编码、hybrid 机制走本地嵌入、检索层不叠加融合、
   共享写入只碰 store）。
2. 依赖一个**自建 Qdrant 服务**的端口契约测试——用 `MEMORY_STORE_TEST_URL`
   （默认 `http://127.0.0.1:6333`）探测；**不可达即显式 skip**（不是静默通过）。
3. `open_store` 的工厂切换（无需联网）。
"""
import os
import subprocess

import pytest

from memory_agent.memory.entries import point_id_for
from memory_agent.memory.ports import PLANE_LOCAL, PLANE_SHARED, VectorStore
from memory_agent.memory.retrieval import MemoryRetriever
from memory_agent.memory.shared_writer import SharedMemoryWriter, SharedWriteError
from memory_agent.memory.sparse import SPARSE_DIM, encode_sparse, tokenize
from memory_agent.memory.store import QdrantNetworkStore, open_store
from ragcore.services.vector_store_service import VectorStoreService
from ragcore.utils.model_status import EMBEDDING_DIMENSION

SERVER_URL = os.environ.get("MEMORY_STORE_TEST_URL", "http://127.0.0.1:6333")


class StubEmbeddings:
    def embed_query(self, text):
        return [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]


def _server_available() -> bool:
    try:
        from qdrant_client import QdrantClient
        QdrantClient(url=SERVER_URL, timeout=2).get_collections()
        return True
    except Exception:
        return False


requires_server = pytest.mark.skipif(
    not _server_available(),
    reason=f"需要自建 Qdrant 服务（{SERVER_URL}）；设 MEMORY_STORE_TEST_URL 指向可用服务",
)


# ------------------------------------------------------------------ 稀疏编码

def test_sparse_encoder_is_deterministic_and_nonempty():
    a = encode_sparse("BGE-M3 混合检索 hybrid retrieval")
    b = encode_sparse("BGE-M3 混合检索 hybrid retrieval")
    assert a == b
    assert len(a[0]) == len(a[1]) > 0
    assert all(0 <= idx < SPARSE_DIM for idx in a[0])


def test_sparse_encoder_empty_text_and_tf_weighting():
    assert encode_sparse("") == ([], [])
    # 重复词 → 单 index、值 1+ln(tf) > 1（次线性词频）
    indices, values = encode_sparse("retrieval retrieval retrieval")
    assert len(indices) == 1 and values[0] > 1.0


def test_tokenize_orders_and_stopwords():
    assert "如何" not in tokenize("如何做检索")  # 停用词被滤
    assert "检索" in tokenize("检索")            # 中文二元组


# --------------------------------------------- 原生 hybrid 机制（本地嵌入，无服务）

def test_vector_store_hybrid_local_mode_filters_at_prefetch(tmp_path):
    """hybrid 检索 + payload_filter 走本地嵌入模式：不依赖服务，锁过滤正确性。

    **关键回归**：Qdrant local mode 在有 prefetch 时**忽略顶层 `query_filter`**，
    过滤必须挂在每个 `Prefetch` 上——否则跨租户命中会泄漏（隔离绕过的前哨）。
    """
    svc = VectorStoreService(collection_name="mem", db_path=str(tmp_path / "q"),
                             embeddings=StubEmbeddings(), hybrid=True,
                             sparse_encoder=encode_sparse)
    svc.add_documents(
        ["alpha memory entry", "beta memory entry"],
        metadata_list=[{"tenant": "org-a"}, {"tenant": "org-b"}],
        ids=[point_id_for("a"), point_id_for("b")],
    )
    assert svc.get_document_count() == 2

    filtered = svc.search_hybrid_documents("entry", k=5, payload_filter={"tenant": "org-a"})
    assert [m["tenant"] for m in filtered["metadatas"][0]] == ["org-a"]

    # 多值 ABAC（MatchAny）
    multi = svc.search_hybrid_documents(
        "entry", k=5, payload_filter={"tenant": ["org-a", "org-b"]})
    assert len(multi["metadatas"][0]) == 2

    # dense 通道（余弦量纲，供按阈值去重）
    dense = svc.search_dense_documents("alpha", k=2)
    assert dense["distances"][0][0] == pytest.approx(1.0)


def test_retriever_does_not_double_fuse_on_native_hybrid_store():
    """`native_hybrid` store：检索层直接取 store 结果，**不调**关键词通道（ADR-0019 D4）。"""
    calls = {"strategy": 0, "keywords": 0, "search": 0}

    class NativeStore:
        plane = PLANE_SHARED
        tenant = None
        native_hybrid = True

        def search_documents(self, query, k=3, payload_filter=None):
            calls["search"] += 1
            return {"documents": [["doc"]], "metadatas": [[{"entry_id": "x"}]],
                    "distances": [[0.5]]}

        def search_by_keywords(self, keywords, source_filter=None):
            calls["keywords"] += 1
            return []

    class BoomStrategy:
        def retrieve(self, *a, **kw):
            calls["strategy"] += 1
            raise AssertionError("native_hybrid store 不应走策略层融合")

    got = MemoryRetriever(NativeStore(), strategy=BoomStrategy()).retrieve("q", k=1)
    assert got == [(0.5, "doc", {"entry_id": "x"})]
    assert calls == {"strategy": 0, "keywords": 0, "search": 1}


# ------------------------------------------------------------- 共享域 DB 写入

class FakeSharedStore:
    """最小共享 store：只在内存里 upsert / fetch / dense 检索，记录调用。"""

    plane = PLANE_SHARED
    tenant = None
    native_hybrid = True

    def __init__(self):
        self.points = {}
        self.adds = 0

    def add(self, texts, metadata_list=None, ids=None):
        self.adds += 1
        for text, meta, point_id in zip(texts, metadata_list, ids):
            self.points[point_id] = dict(meta)
        return list(ids)

    def fetch(self, ids):
        return [self.points[i] for i in ids if i in self.points]

    def count(self):
        return len(self.points)

    def search_dense(self, query, k=3, payload_filter=None):
        return {"documents": [[]], "metadatas": [[]], "distances": [[]]}


def test_shared_writer_touches_only_the_store(tmp_path, monkeypatch):
    """共享域写入：只碰 store，**不调 git、不写代目录 / 指针**（ADR-0025 D16）。"""
    store = FakeSharedStore()
    writer = SharedMemoryWriter(store, owner="me", tenant="org-a")

    def _no_subprocess(*a, **kw):
        raise AssertionError("共享域写入不得调用 git / 子进程（本地专用机制）")

    monkeypatch.setattr(subprocess, "run", _no_subprocess)
    index_dir = tmp_path / "vector_db"
    monkeypatch.setenv("MEMORY_INDEX_DIR", str(index_dir))

    result = writer.add(title="Shared note", body="body text", domain="topics",
                        type="topic", tags=["shared"], slug="shared-note")
    assert result["status"] == "written" and result["index"]["mode"] == "db"
    assert store.count() == 1
    assert not index_dir.exists()  # 无代目录 / 无 CURRENT 指针

    written = store.fetch([point_id_for("topics/shared-note")])[0]
    assert written["tenant"] == "org-a" and written["writable"] is True
    assert written["residency"] == "cloud"

    # 同 id 再来 → 报告重复，不写
    again = writer.add(title="Shared note", body="body text", domain="topics",
                       type="topic", tags=["shared"], slug="shared-note")
    assert again["status"] == "duplicate" and store.adds == 1


def test_shared_writer_archive_and_supersede_are_preview_then_confirm():
    store = FakeSharedStore()
    writer = SharedMemoryWriter(store, owner="me")
    writer.add(title="Old note", body="old body", domain="decisions",
               type="decision", tags=["shared"], slug="old-note")

    preview = writer.archive(entry_id="decisions/old-note", reason="outdated")
    assert preview["status"] == "confirmation_required"
    assert store.fetch([point_id_for("decisions/old-note")])[0]["status"] == "current"

    done = writer.archive(entry_id="decisions/old-note", reason="outdated", confirm=True)
    assert done["status"] == "written"
    assert store.fetch([point_id_for("decisions/old-note")])[0]["status"] == "archived"

    with pytest.raises(SharedWriteError):
        writer.archive(entry_id="decisions/old-note", reason="again", confirm=True)

    writer.add(title="Live note", body="live body", domain="decisions",
               type="decision", tags=["shared"], slug="live-note")
    sup = writer.supersede(old_id="decisions/live-note", title="New note", body="new body",
                           domain="decisions", type="decision", tags=["shared"],
                           slug="new-note")
    assert sup["status"] == "confirmation_required"
    writer.supersede(old_id="decisions/live-note", title="New note", body="new body",
                     domain="decisions", type="decision", tags=["shared"],
                     slug="new-note", confirm=True)
    assert store.fetch([point_id_for("decisions/live-note")])[0]["status"] == "superseded"
    assert store.fetch([point_id_for("decisions/new-note")])[0]["supersedes"] == \
        "decisions/live-note"


# ------------------------------------------------------------ 工厂切换（无网络）

def test_open_store_switches_to_network_when_url_given():
    store = open_store(url="http://127.0.0.1:9", tenant="org-a")
    assert isinstance(store, QdrantNetworkStore)
    assert store.plane == PLANE_SHARED and store.tenant == "org-a"
    assert store.native_hybrid is True

    local = open_store(db_path="unused")
    assert local.plane == PLANE_LOCAL and local.native_hybrid is False


# --------------------------------------- 端口契约（共享适配器，需自建 Qdrant 服务）

@requires_server
def test_network_store_satisfies_port_contract():
    collection = f"pytest_33_{os.getpid()}"
    store = QdrantNetworkStore(url=SERVER_URL, collection_name=collection,
                               embeddings=StubEmbeddings(), tenant="org-a")
    try:
        assert isinstance(store, VectorStore)
        assert store.plane == PLANE_SHARED
        store.clear()
        assert store.count() == 0
        store.add(["alpha doc", "beta doc"],
                  metadata_list=[{"entry_id": "a", "tenant": "org-a"},
                                 {"entry_id": "b", "tenant": "org-b"}],
                  ids=[point_id_for("a"), point_id_for("b")])
        assert store.count() == 2

        hits = store.search("doc", k=5)
        assert [m["entry_id"] for m in hits["metadatas"][0]] == ["a"]  # 绑定租户收窄
        widened = store.search("doc", k=5, payload_filter={"tenant": "org-b"})
        assert [m["entry_id"] for m in widened["metadatas"][0]] == ["a"]

        store.delete([point_id_for("a")])
        assert store.count() == 1
    finally:
        store.clear()


@requires_server
def test_network_store_native_hybrid_and_dense_channel():
    collection = f"pytest_33_hybrid_{os.getpid()}"
    store = QdrantNetworkStore(url=SERVER_URL, collection_name=collection,
                               embeddings=StubEmbeddings())
    try:
        store.clear()
        store.add(["alpha memory entry", "beta memory entry"],
                  ids=[point_id_for("a"), point_id_for("b")],
                  metadata_list=[{"entry_id": "a"}, {"entry_id": "b"}])
        hybrid = store.search_documents("alpha", k=2)
        assert len(hybrid["documents"][0]) == 2
        dense = store.search_dense("alpha", k=1)
        assert dense["distances"][0][0] == pytest.approx(1.0)
    finally:
        store.clear()
