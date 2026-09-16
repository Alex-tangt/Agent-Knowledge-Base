"""`VectorStore` 端口的 Qdrant 适配器（issue #23 / #33）：本地嵌入 + 网络化共享后端。

- **本地**（`QdrantLocalStore`，`plane=local`）：Qdrant local mode（`path=`），薄适配
  `services.vector_store_service.VectorStoreService`。检索语义不变（dense + 策略层关键词，
  D7 的本地 native-hybrid 迁移另票）。
- **网络化**（`QdrantNetworkStore`，`plane=shared`，issue #33）：Qdrant **server**
  （自建服务，公司内部同步场景）或**云托管**——`url=`[+`api_key=`]，**同一个适配器**
  （Qdrant 客户端 `path=` / `url=` / `url=+api_key=` 是同一套 API）。检索走 **store 原生
  hybrid**（服务端 `prefetch` + `FusionQuery`），**不再叠加**我们的关键词融合（ADR-0019 D4）。

`MemoryIndex` / `Reindexer` 只经工厂（`open_store`）拿实现，**不 import** ragcore 的
Qdrant 细节（端口解耦）。`api_key` 只经构造参数传入，**绝不落盘 / 落日志**（#25 约定）。
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from memory_agent.memory.ports import PLANE_LOCAL, PLANE_SHARED
from memory_agent.memory.sparse import encode_sparse
from memory_agent.settings import (
    COLLECTION_NAME,
    STORE_API_KEY,
    STORE_COLLECTION,
    STORE_HYBRID,
    STORE_URL,
)
from ragcore.services.vector_store_service import VectorStoreService


class QdrantLocalStore:
    """把 `VectorStoreService`（Qdrant local mode）适配成记忆存储端口。"""

    plane = PLANE_LOCAL
    native_hybrid = False

    def __init__(self, db_path: str, collection_name: str | None = None,
                 embeddings=None, tenant: str | None = None,
                 hybrid: bool = False,
                 sparse_encoder: Callable[[str], tuple[list[int], list[float]]] | None = encode_sparse):
        """`hybrid=True` 时本地集合也用具名 dense+sparse + 原生 fusion（机制与共享平面同源，
        供"同一机制、两种部署"的双后端对照；生产本地平面默认仍 dense + 策略层关键词）。"""
        self._service = VectorStoreService(
            collection_name=collection_name or COLLECTION_NAME,
            db_path=db_path,
            embeddings=embeddings,
            hybrid=hybrid,
            sparse_encoder=sparse_encoder,
        )
        self.tenant = tenant
        self.native_hybrid = bool(hybrid)

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

    def search_dense(self, query: str, k: int = 3,
                     payload_filter: Mapping[str, Any] | None = None) -> dict:
        """纯 dense（余弦）通道；供按阈值操作与双后端消融（#33）。"""
        scoped = dict(payload_filter or {})
        effective_tenant = self.tenant if self.tenant is not None else None
        if effective_tenant is not None:
            scoped["tenant"] = effective_tenant
        return self._service.search_dense_documents(query, k=k, payload_filter=scoped or None)

    def fetch(self, ids: Sequence[str]) -> list[dict]:
        return self._service.retrieve_documents(ids)


class QdrantNetworkStore:
    """网络化 Qdrant（共享平面）：自建 server 与云托管**同一个适配器**（issue #33）。

    - `url=` 必填；`api_key=` 可选（云托管 / 开了鉴权的自建服务）。二者只从构造参数 /
      进程环境来，**绝不落盘**（#25）。
    - **store 原生 hybrid**：集合为具名 `dense` + `sparse`，检索在 Qdrant 侧用
      `prefetch` + `FusionQuery`（RRF/DBSF）融合。`native_hybrid=True` 让检索层不再
      叠加我们的关键词融合（ADR-0019 D4）。
    - `tenant` / `payload_filter` **只透传**（白名单由网关构造，适配器不做授权）。
    - 写入是 **DB upsert/delete**（共享域无 git / 代目录 / 指针切换，ADR-0025 D16）。
    """

    plane = PLANE_SHARED
    native_hybrid = True

    def __init__(self, url: str, api_key: str | None = None,
                 collection_name: str | None = None, embeddings=None,
                 tenant: str | None = None, hybrid: bool = True,
                 sparse_encoder: Callable[[str], tuple[list[int], list[float]]] | None = encode_sparse):
        if not url:
            raise ValueError("QdrantNetworkStore 需要 url（自建 Qdrant 服务或云托管端点）")
        self._service = VectorStoreService(
            collection_name=collection_name or STORE_COLLECTION,
            url=url,
            api_key=api_key,
            embeddings=embeddings,
            hybrid=hybrid,
            sparse_encoder=sparse_encoder,
        )
        self.url = url
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
        """store 原生 hybrid 召回；绑定 tenant 只可收窄不可放宽（ADR-0018 D2）。"""
        scoped = dict(payload_filter or {})
        effective_tenant = self.tenant if self.tenant is not None else tenant
        if effective_tenant is not None:
            scoped["tenant"] = effective_tenant
        return self._service.search_documents(query, k=k, payload_filter=scoped or None)

    def search_documents(self, query: str, k: int = 3,
                         payload_filter: Mapping[str, Any] | None = None) -> dict:
        return self.search(query, k=k, payload_filter=payload_filter)

    def search_by_keywords(self, keywords: Sequence[str],
                           source_filter: str | None = None) -> list[dict]:
        """**不提供** Python 关键词通道：词法信号已由 store 原生 sparse 通道承载。

        返回空 = 检索层的可选关键词通道静默跳过（`DefaultRetrievalStrategy`），
        避免把词法信号**算两次**（ADR-0019 D4 的"无双重融合"）。保留方法只为满足端口。
        """
        return []

    def search_dense(self, query: str, k: int = 3,
                     payload_filter: Mapping[str, Any] | None = None) -> dict:
        """纯 dense（余弦）通道：分数量纲与本地平面一致，供按阈值操作（去重）使用。"""
        scoped = dict(payload_filter or {})
        effective_tenant = self.tenant if self.tenant is not None else None
        if effective_tenant is not None:
            scoped["tenant"] = effective_tenant
        return self._service.search_dense_documents(query, k=k, payload_filter=scoped or None)

    def fetch(self, ids: Sequence[str]) -> list[dict]:
        """按点 id 取回 payload（共享平面无文件，读回靠 DB）。"""
        return self._service.retrieve_documents(ids)


def open_store(db_path: str | None = None, *, tenant: str | None = None,
               url: str | None = None, api_key: str | None = None,
               collection_name: str | None = None, hybrid: bool | None = None,
               embeddings=None,
               sparse_encoder: Callable[[str], tuple[list[int], list[float]]] | None = None):
    """store 工厂：`url`（或配置的 `MEMORY_STORE_URL`）优先 → 网络化共享后端；否则本地。

    - 显式 `url` / `api_key` / `hybrid` 覆盖配置。
    - 本地平面保持默认 dense + 策略层关键词（`QdrantLocalStore`）。
    """
    if sparse_encoder is None:
        sparse_encoder = encode_sparse
    resolved_url = url if url is not None else STORE_URL
    if resolved_url:
        resolved_hybrid = STORE_HYBRID if hybrid is None else hybrid
        return QdrantNetworkStore(
            url=resolved_url,
            api_key=api_key if api_key is not None else STORE_API_KEY,
            collection_name=collection_name,
            embeddings=embeddings,
            tenant=tenant,
            hybrid=resolved_hybrid,
            sparse_encoder=sparse_encoder,
        )
    return QdrantLocalStore(
        db_path=db_path, collection_name=collection_name, tenant=tenant,
        embeddings=embeddings, hybrid=bool(hybrid),
        sparse_encoder=sparse_encoder,
    )
