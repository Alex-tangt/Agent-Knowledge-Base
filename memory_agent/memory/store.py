"""本地 Qdrant 的 `VectorStore` 实现（issue #23）：端口 -> ragcore 服务的适配器。

- 薄适配，不改检索语义：所有调用透传给 `services.vector_store_service.VectorStoreService`
  （Qdrant local mode 的按操作开/关 client、进程内串行化、退避重试都由它兜住）。
- `MemoryIndex` / `Reindexer` 通过 `open_store(db_path)` 拿实现，**不 import** ragcore
  的 Qdrant 细节（端口解耦，见 ADR-0019）。
- `search_documents` 是端口 `search` 的别名：ragcore 检索策略消费 dict 形契约。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from memory_agent.memory.ports import PLANE_LOCAL
from memory_agent.settings import COLLECTION_NAME
from ragcore.services.vector_store_service import VectorStoreService


class QdrantLocalStore:
    """把 `VectorStoreService`（Qdrant local mode）适配成记忆存储端口。"""

    plane = PLANE_LOCAL

    def __init__(self, db_path: str, collection_name: str | None = None,
                 embeddings=None, tenant: str | None = None):
        self._service = VectorStoreService(
            collection_name=collection_name or COLLECTION_NAME,
            db_path=db_path,
            embeddings=embeddings,
        )
        self.tenant = tenant

    # ----------------------------------------------------------------- writes

    def add(self, texts: Sequence[str], metadata_list: list[dict] | None = None,
            ids: Sequence[str] | None = None) -> list[str]:
        return self._service.add_documents(texts, metadata_list=metadata_list, ids=ids)

    def delete(self, ids: Sequence[str]) -> bool:
        return self._service.delete_documents(ids)

    def count(self) -> int:
        return self._service.get_document_count()

    def clear(self) -> bool:
        return self._service.clear_all_documents()

    def warmup(self) -> None:
        return self._service.warmup()

    # ------------------------------------------------------------------ read

    def search(self, query: str, k: int = 3,
               payload_filter: Mapping[str, Any] | None = None,
               tenant: str | None = None) -> dict:
        """向量召回。绑定 tenant 的 store 不允许被调用方放宽（ADR-0018 D2）。"""
        scoped = dict(payload_filter or {})
        effective_tenant = self.tenant if self.tenant is not None else tenant
        if effective_tenant is not None:
            scoped["tenant"] = effective_tenant
        return self._service.search_documents(query, k=k, payload_filter=scoped or None)

    def search_documents(self, query: str, k: int = 3,
                         payload_filter: Mapping[str, Any] | None = None) -> dict:
        """ragcore 检索策略契约的别名（策略调 `search_documents`，不认 `search`）。"""
        return self.search(query, k=k, payload_filter=payload_filter)

    def search_by_keywords(self, keywords: Sequence[str],
                           source_filter: str | None = None) -> list[dict]:
        return self._service.search_by_keywords(keywords, source_filter=source_filter)


def open_store(db_path: str, *, tenant: str | None = None) -> QdrantLocalStore:
    """默认 store 工厂：按代目录路径打开本地 Qdrant 适配器。"""
    return QdrantLocalStore(db_path=db_path, tenant=tenant)
