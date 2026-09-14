"""Qdrant local mode 锁语义回归：操作之间不得长期持有锁。

旧实现把 client 缓存在服务实例上，锁从构造一直持有到进程结束——这会让
opencode 常驻的 MCP 服务整天独占存储目录（重建索引 / CLI / 冒烟脚本全被挡）。
本测试在同一进程内构造第二个 client：只要服务不再缓存 client，第二个必须能拿到锁。
"""
import os
import sys

from qdrant_client import QdrantClient

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "ragcore"))

from services.vector_store_service import VectorStoreService  # noqa: E402
from utils.model_status import EMBEDDING_DIMENSION  # noqa: E402


class StubEmbeddings:
    def embed_query(self, text):
        return [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]


def test_lock_is_released_between_operations(tmp_path):
    path = str(tmp_path / "qdrant")
    store = VectorStoreService(collection_name="t", db_path=path, embeddings=StubEmbeddings())
    store.add_documents(["hello"], metadata_list=[{"writable": True}])
    store.search_documents("hello", k=1)

    probe = QdrantClient(path=path)
    try:
        info = probe.get_collection("t")
        assert info.points_count == 1
    finally:
        probe.close()
