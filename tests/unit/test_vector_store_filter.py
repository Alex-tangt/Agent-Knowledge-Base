"""VectorStoreService：payload_filter 需在 Qdrant 侧过滤（否则 top-k 之后筛会欠填）。

真实 Qdrant + 桩嵌入，不加载 BGE-M3。
"""

from ragcore.services.vector_store_service import VectorStoreService  # noqa: E402
from ragcore.utils.model_status import EMBEDDING_DIMENSION  # noqa: E402

_DIM = EMBEDDING_DIMENSION


class StubEmbeddings:
    """R* -> 与查询同向（最相似）；W* -> 正交（相似度 0）。"""

    def _vec(self, text):
        vec = [0.0] * _DIM
        vec[0 if str(text).startswith("R") else 1] = 1.0
        return vec

    def embed_query(self, text):
        return self._vec("R")

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]


def _seed(store):
    store.add_documents(["R1", "R2", "R3"],
                        metadata_list=[{"writable": False}] * 3)
    store.add_documents(["W1", "W2", "W3"],
                        metadata_list=[{"writable": True}] * 3)


def test_payload_filter_is_applied_on_the_server_side(tmp_path):
    store = VectorStoreService(collection_name="t_filter", db_path=str(tmp_path / "qdrant"),
                               embeddings=StubEmbeddings())
    _seed(store)

    unfiltered = store.search_documents("q", k=3)
    assert unfiltered["metadatas"][0] and all(
        m["writable"] is False for m in unfiltered["metadatas"][0]
    ), "控制组：不带过滤时 top-3 应被只读条目占满（向量更相似）"

    filtered = store.search_documents("q", k=3, payload_filter={"writable": True})

    assert len(filtered["metadatas"][0]) == 3, "过滤后仍应填满 k（欠填即回归）"
    assert all(m["writable"] is True for m in filtered["metadatas"][0])


def test_search_without_filter_keeps_previous_behavior(tmp_path):
    store = VectorStoreService(collection_name="t_nofilter", db_path=str(tmp_path / "qdrant"),
                               embeddings=StubEmbeddings())
    _seed(store)

    result = store.search_documents("q", k=2)

    assert len(result["metadatas"][0]) == 2
