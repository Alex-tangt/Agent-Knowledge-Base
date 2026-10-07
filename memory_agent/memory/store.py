"""`VectorStore` 端口的 Qdrant 适配器（issue #23 / #33）：本地嵌入 + 网络化共享后端。

- **本地**（`QdrantLocalStore`，`plane=local`）：Qdrant local mode（`path=`），薄适配
  `services.vector_store_service.VectorStoreService`。**默认走 store 原生 hybrid**
  （具名 `dense` + `sparse`，BM25 客户端编码，`FusionQuery` DBSF）——ADR-0019 **D16**；
  `MEMORY_LOCAL_HYBRID=0` 可退回 dense + 策略层手写关键词（旧行为）。
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
    LOCAL_HYBRID,
    SPARSE_BACKEND,
    SPARSE_BM25_CACHE_DIR,
    SPARSE_BM25_MODEL,
    STORE_API_KEY,
    STORE_COLLECTION,
    STORE_FUSION,
    STORE_HYBRID,
    STORE_URL,
)
from ragcore.services.vector_store_service import VectorStoreService


def _empty_result() -> dict:
    """端口契约下的**空**召回结果（`search` 的 dict 形）。

    交集为空时必须显式返回空，**绝不**退化成调用方请求或 store 绑定的任一端全量
    （#39 / ADR-0018 D2 + ADR-0019 D3）。
    """
    return {"documents": [[]], "metadatas": [[]], "distances": [[]]}


def _tenant_set(value) -> set | None:
    """把一端的 tenant 约束归一成集合；`None` = 该端**未**对此维度提出约束。"""
    if value is None:
        return None
    if isinstance(value, (list, tuple, set, frozenset)):
        return set(value)
    return {value}


def narrow_tenant(bound: str | None, requests: Sequence[Any]) -> tuple[Any, bool]:
    """绑定租户 ∩ 调用方（网关注入）请求：**只可收窄，不相交 = 空**。

    两端来源（store 构造期绑定，ADR-0019 D3；网关注入的 `payload_filter` / `tenant`，
    ADR-0018 D2）在 store 层**求交**：任一端缺失 = 该端不限制；交集为空 → 返回
    `possible=False`，调用方必须返回空结果，不得回退到任一端全量（这正是 #39 的 F2：
    旧实现用绑定租户**覆盖**请求，等于把 org-a 数据交给 org-b 身份）。

    返回 `(clause, possible)`：`clause` 为 `None`（该维度不产生子句）/ `str` / `list[str]`
    （多值，端口下沉为 `MatchAny`）。
    """
    sets: list[set] = []
    for value in requests:
        values = _tenant_set(value)
        if values is None:
            continue
        if not values:
            # 显式空集 = 「谁都不该看到」→ 空，而不是放开。
            return None, False
        sets.append(values)
    if bound is not None:
        sets.append({bound})
    if not sets:
        return None, True
    allowed = set.intersection(*sets)
    if not allowed:
        return None, False
    if len(allowed) == 1:
        return next(iter(allowed)), True
    return sorted(allowed), True


def _scope_filter(bound: str | None, payload_filter, tenant=None) -> tuple[dict | None, bool]:
    """把 `payload_filter` 里的 tenant、`tenant` 参数与 store 绑定**求交**。

    返回 `(scoped_filter, possible)`；`possible=False` 时调用方返回 `_empty_result()`。
    `payload_filter` 的其它键原样透传（它们只用于进一步收窄）。
    """
    scoped = dict(payload_filter or {})
    clause, possible = narrow_tenant(bound, [scoped.pop("tenant", None), tenant])
    if not possible:
        return None, False
    if clause is not None:
        scoped["tenant"] = clause
    return scoped or None, True


def _resolve_sparse_encoders(backend, model_name, sparse_encoder):
    """返回 `(doc_encoder, query_encoder)`（#40）。

    显式 `sparse_encoder` 优先（测试注入 / 调用方指定，query 侧同形回落）。
    否则按 `MEMORY_SPARSE_BACKEND` 选：`tfidf`（默认，零依赖自制词频）或 `bm25`
    （fastembed `Qdrant/bm25`，软依赖，只有选中才 import）。
    """
    if sparse_encoder is not None:
        return sparse_encoder, sparse_encoder
    if str(backend).lower() == "bm25":
        from memory_agent.memory.bm25 import Bm25Encoder

        encoder = Bm25Encoder(model_name, cache_dir=SPARSE_BM25_CACHE_DIR)
        return encoder.encode_document, encoder.encode_query
    return encode_sparse, encode_sparse


class QdrantLocalStore:
    """把 `VectorStoreService`（Qdrant local mode）适配成记忆存储端口。"""

    plane = PLANE_LOCAL
    native_hybrid = False

    def __init__(self, db_path: str, collection_name: str | None = None,
                 embeddings=None, tenant: str | None = None,
                 hybrid: bool = False,
                 sparse_encoder: Callable[[str], tuple[list[int], list[float]]] | None = encode_sparse,
                 sparse_query_encoder: Callable[[str], tuple[list[int], list[float]]] | None = None,
                 fusion: str | None = None):
        """`hybrid=True`（**默认**，ADR-0019 D16）时本地集合用具名 dense+sparse + 原生 fusion：
        词法线 = `MEMORY_SPARSE_BACKEND`（默认 `bm25`），融合 = `MEMORY_STORE_FUSION`（默认 `dbsf`）。
        与共享平面同机制；关掉即回 dense + 策略层手写关键词。

        `sparse_query_encoder` 供 doc/query 不对称的编码器（BM25；#40）。`fusion` 选原生融合
        方式（ADR-0019 D6：按平面选型），缺省取 `MEMORY_STORE_FUSION`（默认 `rrf`）。"""
        self._service = VectorStoreService(
            collection_name=collection_name or COLLECTION_NAME,
            db_path=db_path,
            embeddings=embeddings,
            hybrid=hybrid,
            sparse_encoder=sparse_encoder,
            sparse_query_encoder=sparse_query_encoder,
        )
        self.tenant = tenant
        self.native_hybrid = bool(hybrid)
        self.fusion = (fusion or STORE_FUSION or "rrf").lower()

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
        """向量召回。绑定 tenant 与调用方请求**求交**：只可收窄，不相交 = 返回空。

        #39：旧实现用 `self.tenant` **覆盖**调用方（网关注入）的 tenant → org-b 身份
        拿到 org-a 数据（F2）。现为交集（`narrow_tenant`），任一端不外溢。

        `native_hybrid` 时走 store 原生 hybrid（可按 `fusion` 选 RRF/DBSF），否则纯 dense。
        """
        scoped, possible = _scope_filter(self.tenant, payload_filter, tenant)
        if not possible:
            return _empty_result()
        if self.native_hybrid:
            if self.fusion == "dense":
                return self._service.search_dense_documents(
                    query, k=k, payload_filter=scoped)
            return self._service.search_hybrid_documents(
                query, k=k, payload_filter=scoped, fusion=self.fusion)
        return self._service.search_dense_documents(query, k=k, payload_filter=scoped)

    def search_documents(self, query: str, k: int = 3,
                         payload_filter: Mapping[str, Any] | None = None) -> dict:
        """ragcore 检索策略契约的别名（策略调 `search_documents`，不认 `search`）。"""
        return self.search(query, k=k, payload_filter=payload_filter)

    def search_by_keywords(self, keywords: Sequence[str],
                           source_filter: str | None = None) -> list[dict]:
        """关键词通道**也必须**认 store 绑定租户（#39 的 F1）。

        #39 前这里原样透传 `VectorStoreService.search_by_keywords` 的**全量** scroll，
        不带 tenant → 绑定 org-a 的 store 会把 org-b 条目交给无 tenant 的身份
        （策略层后置过滤只在 `payload_filter` 非空时才跑，兜不住）。

        端口契约（ADR-0019 D3）是「绑定租户只可收窄」：绑定存在时按绑定租户收窄；
        **无绑定**时不在此处收窄（策略层仍按 `payload_filter` 后置过滤）。
        """
        matches = self._service.search_by_keywords(keywords, source_filter=source_filter)
        if self.tenant is None:
            return matches
        return [m for m in matches
                if (m.get("metadata") or {}).get("tenant") == self.tenant]

    def search_dense(self, query: str, k: int = 3,
                     payload_filter: Mapping[str, Any] | None = None) -> dict:
        """纯 dense（余弦）通道；供按阈值操作与双后端消融（#33）。求交同 `search`。"""
        scoped, possible = _scope_filter(self.tenant, payload_filter)
        if not possible:
            return _empty_result()
        return self._service.search_dense_documents(query, k=k, payload_filter=scoped)

    def fetch(self, ids: Sequence[str]) -> list[dict]:
        return self._service.retrieve_documents(ids)

    def close(self) -> None:
        self._service.close()


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
                 fusion: str | None = None,
                 sparse_encoder: Callable[[str], tuple[list[int], list[float]]] | None = encode_sparse,
                 sparse_query_encoder: Callable[[str], tuple[list[int], list[float]]] | None = None):
        if not url:
            raise ValueError("QdrantNetworkStore 需要 url（自建 Qdrant 服务或云托管端点）")
        self._service = VectorStoreService(
            collection_name=collection_name or STORE_COLLECTION,
            url=url,
            api_key=api_key,
            embeddings=embeddings,
            hybrid=hybrid,
            sparse_encoder=sparse_encoder,
            sparse_query_encoder=sparse_query_encoder,
        )
        self.url = url
        self.tenant = tenant
        self.fusion = (fusion or STORE_FUSION or "rrf").lower()

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
        """store 原生 hybrid 召回；绑定 tenant 与调用方请求**求交**：只可收窄，不相交 = 空。"""
        scoped, possible = _scope_filter(self.tenant, payload_filter, tenant)
        if not possible:
            return _empty_result()
        if self.fusion == "dense":
            return self._service.search_dense_documents(query, k=k, payload_filter=scoped)
        return self._service.search_hybrid_documents(
            query, k=k, payload_filter=scoped, fusion=self.fusion)

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
        """纯 dense（余弦）通道：分数量纲与本地平面一致，供按阈值操作（去重）使用。求交同 `search`。"""
        scoped, possible = _scope_filter(self.tenant, payload_filter)
        if not possible:
            return _empty_result()
        return self._service.search_dense_documents(query, k=k, payload_filter=scoped)

    def search_hybrid(self, query: str, k: int = 3,
                      payload_filter: Mapping[str, Any] | None = None,
                      fusion: str = "rrf") -> dict:
        """store 原生 hybrid，显式选融合方式（`rrf` / `dbsf`）——供按平面选型（D6）。求交同 `search`。"""
        scoped, possible = _scope_filter(self.tenant, payload_filter)
        if not possible:
            return _empty_result()
        return self._service.search_hybrid_documents(
            query, k=k, payload_filter=scoped, fusion=fusion)

    def fetch(self, ids: Sequence[str]) -> list[dict]:
        """按点 id 取回 payload（共享平面无文件，读回靠 DB）。"""
        return self._service.retrieve_documents(ids)

    def close(self) -> None:
        """释放网络化长连接（进程退出 / 用例结束后调用，避免连接泄漏告警）。"""
        self._service.close()


def open_store(db_path: str | None = None, *, tenant: str | None = None,
               url: str | None = None, api_key: str | None = None,
               collection_name: str | None = None, hybrid: bool | None = None,
               embeddings=None,
               sparse_encoder: Callable[[str], tuple[list[int], list[float]]] | None = None,
               sparse_query_encoder: Callable[[str], tuple[list[int], list[float]]] | None = None,
               fusion: str | None = None,
               sparse_backend: str | None = None):
    """store 工厂：`url`（或配置的 `MEMORY_STORE_URL`）优先 → 网络化共享后端；否则本地。

    - 显式 `url` / `api_key` / `hybrid` / `fusion` 覆盖配置。
    - 词法稀疏编码器按 `sparse_backend`（缺省 `MEMORY_SPARSE_BACKEND`）选：**bm25（默认，D16）** | tfidf。
    - 本地平面默认 hybrid（`MEMORY_LOCAL_HYBRID`，D16）；`hybrid=False` 才回 dense-only。
    """
    doc_encoder, query_encoder = _resolve_sparse_encoders(
        sparse_backend or SPARSE_BACKEND, SPARSE_BM25_MODEL, sparse_encoder)
    if sparse_query_encoder is not None:
        query_encoder = sparse_query_encoder
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
            fusion=fusion,
            sparse_encoder=doc_encoder,
            sparse_query_encoder=query_encoder,
        )
    return QdrantLocalStore(
        db_path=db_path, collection_name=collection_name, tenant=tenant,
        embeddings=embeddings, hybrid=LOCAL_HYBRID if hybrid is None else bool(hybrid),
        sparse_encoder=doc_encoder, sparse_query_encoder=query_encoder,
        fusion=fusion,
    )
