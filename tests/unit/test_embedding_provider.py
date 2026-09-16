"""本地嵌入单例（#43）：进程内只加载一次，且 store / embeddings 端点共用同一实例。

不加载真实权重——把 `ragcore.services.local_embedding_service` 换成假模块。
"""
import sys
import types

import ragcore.services.embedding_provider as provider


class _FakeService:
    instances = 0

    def __init__(self, model_name):
        _FakeService.instances += 1
        self.model_name = model_name


def _install_fake(monkeypatch):
    _FakeService.instances = 0
    fake = types.ModuleType("ragcore.services.local_embedding_service")
    fake.LocalEmbeddingService = _FakeService
    monkeypatch.setitem(sys.modules, "ragcore.services.local_embedding_service", fake)


def test_get_local_embedding_service_is_a_singleton(monkeypatch):
    _install_fake(monkeypatch)
    provider.reset_local_embedding_service()

    first = provider.get_local_embedding_service("BAAI/bge-m3")
    second = provider.get_local_embedding_service("BAAI/bge-m3")

    assert first is second
    assert _FakeService.instances == 1


def test_reset_allows_a_fresh_load(monkeypatch):
    _install_fake(monkeypatch)
    provider.reset_local_embedding_service()

    first = provider.get_local_embedding_service()
    provider.reset_local_embedding_service()
    second = provider.get_local_embedding_service()

    assert first is not second
    assert _FakeService.instances == 2


def test_vector_store_shares_the_singleton(monkeypatch):
    from ragcore.services import vector_store_service as vss

    sentinel = object()
    monkeypatch.setattr(vss, "get_local_embedding_service", lambda model=None: sentinel)

    store = vss.VectorStoreService(collection_name="probe", db_path="unused")
    assert store.embeddings is sentinel
