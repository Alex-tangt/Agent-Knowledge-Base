"""清空集合的回归：local mode 下丢集合会被同名重建"复活"旧点。

实测（qdrant-client local mode）：`delete_collection` + `create_collection`
只摘掉元数据，磁盘上的点会回来（3 -> 0 -> 3）；`recreate_collection` 同样失效。
只有「空 filter 删光所有点」真正生效。legal_web 的 `DELETE /api/documents/clear`
与 `MemoryIndex.rebuild` 都依赖这条路径。
"""

from ragcore.services.vector_store_service import VectorStoreService  # noqa: E402
from ragcore.utils.model_status import EMBEDDING_DIMENSION  # noqa: E402


class StubEmbeddings:
    def embed_query(self, text):
        return [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]


def _store(tmp_path):
    return VectorStoreService(collection_name="t", db_path=str(tmp_path / "qdrant"),
                              embeddings=StubEmbeddings())


def test_clear_actually_removes_all_points(tmp_path):
    store = _store(tmp_path)
    store.add_documents(["a", "b", "c"], metadata_list=[{}, {}, {}])
    assert store.get_document_count() == 3

    store.clear_all_documents()

    assert store.get_document_count() == 0


def test_repeated_clear_and_add_does_not_accumulate(tmp_path):
    store = _store(tmp_path)
    for _ in range(3):
        store.add_documents(["a", "b"], metadata_list=[{}, {}])
        store.clear_all_documents()

    store.add_documents(["a", "b"], metadata_list=[{}, {}])

    assert store.get_document_count() == 2


def test_clear_rebuilds_a_usable_collection(tmp_path):
    store = _store(tmp_path)
    store.add_documents(["a"], metadata_list=[{}])
    store.clear_all_documents()

    store.add_documents(["b"], metadata_list=[{}])

    result = store.search_documents("b", k=1)
    assert len(result["documents"][0]) == 1
