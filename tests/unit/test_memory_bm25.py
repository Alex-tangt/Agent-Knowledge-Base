"""BM25 稀疏编码接缝（#40）：不加载真实 fastembed 模型——用 fake。"""
import types

import pytest

import memory_agent.memory.bm25 as bm25_mod
from memory_agent.memory.sparse import encode_sparse
from memory_agent.memory.store import _resolve_sparse_encoders, open_store
from ragcore.services.vector_store_service import VectorStoreService


class FakeSparseEmbedding:
    def __init__(self, model_name, cache_dir=None):
        self.model_name = model_name

    def embed(self, documents):
        for _ in documents:
            yield types.SimpleNamespace(indices=[1, 2], values=[0.5, 0.7])

    def query_embed(self, queries):
        for _ in queries:
            yield types.SimpleNamespace(indices=[1], values=[1.0])


@pytest.fixture
def fake_fastembed(monkeypatch):
    import fastembed.sparse as fastembed_sparse

    monkeypatch.setattr(fastembed_sparse, "SparseTextEmbedding", FakeSparseEmbedding)


def test_bm25_encoder_doc_vs_query_differ(fake_fastembed):
    encoder = bm25_mod.Bm25Encoder("Qdrant/bm25")
    assert encoder.encode_document("x") == ([1, 2], [0.5, 0.7])
    assert encoder.encode_query("x") == ([1], [1.0])


def test_resolve_defaults_to_tfidf():
    doc, query = _resolve_sparse_encoders("tfidf", "Qdrant/bm25", None)
    assert doc is encode_sparse and query is encode_sparse


def test_resolve_bm25_returns_asymmetric_pair(fake_fastembed):
    doc, query = _resolve_sparse_encoders("bm25", "Qdrant/bm25", None)
    assert doc("x") == ([1, 2], [0.5, 0.7])
    assert query("x") == ([1], [1.0])


def test_explicit_encoder_wins_over_backend(fake_fastembed):
    explicit = lambda text: ([9], [9.0])  # noqa: E731

    doc, query = _resolve_sparse_encoders("bm25", "Qdrant/bm25", explicit)
    assert doc is explicit and query is explicit


def test_vector_store_encodes_query_with_query_encoder():
    doc = lambda text: ([1], [1.0])  # noqa: E731
    query = lambda text: ([2], [2.0])  # noqa: E731
    service = VectorStoreService(
        collection_name="c", db_path="unused", hybrid=True,
        sparse_encoder=doc, sparse_query_encoder=query)
    assert service._sparse_vector("t").indices == [1]
    assert service._sparse_vector("t", query=True).indices == [2]


def test_vector_store_query_encoder_falls_back_to_doc():
    doc = lambda text: ([1], [1.0])  # noqa: E731
    service = VectorStoreService(
        collection_name="c", db_path="unused", hybrid=True, sparse_encoder=doc)
    assert service._sparse_vector("t", query=True).indices == [1]


def test_open_store_wires_bm25_encoders(fake_fastembed):
    store = open_store(db_path="unused", sparse_backend="bm25")
    assert store._service._sparse_encoder("x") == ([1, 2], [0.5, 0.7])
    assert store._service._sparse_query_encoder("x") == ([1], [1.0])


def test_open_store_defaults_to_bm25(monkeypatch):
    """2026-09-16 owner 决定：本地默认词法路 = BM25（ADR-0019 D14 修订）。"""
    monkeypatch.delenv("MEMORY_SPARSE_BACKEND", raising=False)
    store = open_store(db_path="unused")
    assert callable(store._service._sparse_encoder)
    assert store._service._sparse_encoder is not encode_sparse


def test_open_store_can_fall_back_to_tfidf_and_dense_local(monkeypatch):
    """可回退：tfidf 词法路 + 非 hybrid 本地平面 = 改前的旧行为。

    settings 的 knob 是 **import 期常量**，故直接 patch `store` 模块里已绑定的名字。
    """
    from memory_agent.memory import store as store_module

    monkeypatch.setattr(store_module, "SPARSE_BACKEND", "tfidf")
    monkeypatch.setattr(store_module, "LOCAL_HYBRID", False)
    legacy = store_module.open_store(db_path="unused")
    assert legacy._service._sparse_encoder is encode_sparse
    assert legacy.native_hybrid is False
