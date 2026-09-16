import time
import threading
import uuid
from contextlib import contextmanager

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    Fusion,
    FusionQuery,
    MatchAny,
    MatchValue,
    Modifier,
    PointStruct,
    Prefetch,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)
from ragcore.config.config import (
    USE_LOCAL_EMBEDDINGS,
    LOCAL_EMBEDDING_MODEL,
    VECTOR_DB_PATH,
    QDRANT_COLLECTION_NAME,
)
from ragcore.services.embedding_provider import get_local_embedding_service
from ragcore.services.langsmith_service import langsmith_service
from ragcore.utils.logger import logger
from ragcore.utils.model_status import EMBEDDING_DIMENSION, STATUS

# local mode 独占锁：被别的进程挡住时短暂重试（锁只在别的进程的调用期存在）
_LOCK_RETRY_ATTEMPTS = 6
_LOCK_RETRY_DELAY = 0.05

# 同一进程内的 client 互斥（issue #19）：Qdrant local mode 的独占锁是「按目录 + 全进程」
# 的——同一进程里并发构造第二个 client 也会直接 RuntimeError（实测 4 线程 3 个立刻失败），
# 退避重试兜不住。单实例 daemon 并发服务多会话时必须把这把锁串起来。
_SESSION_LOCK = threading.RLock()

# 原生 hybrid 融合前每路的候选深度下限（issue #33）。
_HYBRID_PREFETCH_DEFAULT = 20


def _field_condition(key, value):
    """payload 过滤子句：标量 → 精确匹配；序列 → 任一匹配（多值 ABAC）。"""
    if isinstance(value, (list, tuple, set, frozenset)):
        return FieldCondition(key=key, match=MatchAny(any=list(value)))
    return FieldCondition(key=key, match=MatchValue(value=value))


class VectorStoreService:
    """Qdrant local mode 包装。

    注意 local mode 的锁语义（实测）：锁在 **client 构造期**持有、`close()` 释放，
    构造函数没有绕过开关。因此这里**不缓存 client**——每次操作开/关一个
    （实测 ~19ms/次，相对嵌入开销是噪声）。好处是锁只在调用期存在，
    opencode 常驻的 MCP 服务不会整天独占存储目录，索引重建 / CLI / 冒烟脚本
    得以与其共存。
    """

    def __init__(self, collection_name=None, db_path=None, embeddings=None,
                 url=None, api_key=None, hybrid=False, sparse_encoder=None,
                 sparse_query_encoder=None):
        """两种形态共用同一套 API（issue #33）：

        - **本地嵌入**（默认）：`path=db_path`，client 按操作开/关（local mode 独占锁）。
        - **网络化**（`url=` [+ `api_key=`]）：Qdrant server（自建服务）或云托管。
          `api_key` 只经构造参数传入，**绝不写日志 / payload**。

        `hybrid=True` 时集合为 **具名 dense + sparse**，检索走 **store 原生 hybrid**
        （`prefetch` + `FusionQuery`），策略层不再叠加自己的融合（ADR-0019 D4/D5）。
        sparse 向量由 `sparse_encoder(text) -> (indices, values)` 提供（app 层 BYOE，
        见 `memory_agent.memory.sparse`）；dense 维度仍取 `EMBEDDING_DIMENSION`。
        """
        self.collection_name = collection_name or QDRANT_COLLECTION_NAME
        self._db_path = db_path or VECTOR_DB_PATH
        self._url = url
        self._api_key = api_key
        self._embeddings = embeddings
        self.hybrid = bool(hybrid)
        self._sparse_encoder = sparse_encoder
        # BM25 的 doc/query 权重不对称（#40）：查询侧编码器缺省回落到文档编码器
        # （tfidf 编码器对称，两者同形）。
        self._sparse_query_encoder = sparse_query_encoder or sparse_encoder
        self._logged_init = False
        # 网络化：**长连接 client 复用**（server 自管并发；每次操作新建 client 会把
        # 纯 HTTP 的往返放大成秒级，实测 3.0s/题 vs 0.38s/题）。本地嵌入模式仍按操作开/关。
        self._remote_client = None

    @property
    def embeddings(self):
        if self._embeddings is None:
            STATUS["embedding"] = "loading"
            logger.info("Loading embedding model...")
            if USE_LOCAL_EMBEDDINGS:
                # 进程级单例（issue #43）：避免每次换代重建 store 都重载一次权重，
                # 也让 OpenAI 兼容 /v1/embeddings 端点与本 store 共用同一份 BGE-M3。
                self._embeddings = get_local_embedding_service(LOCAL_EMBEDDING_MODEL)
            else:
                from langchain_openai import OpenAIEmbeddings
                from ragcore.config.llm import require_llm
                llm = require_llm()
                self._embeddings = OpenAIEmbeddings(
                    api_key=llm.api_key,
                    base_url=llm.base_url,
                )
            STATUS["embedding"] = "ready"
            logger.info("Embedding model ready")
        return self._embeddings

    def warmup(self):
        """预热嵌入模型。不触碰 Qdrant，因此不占锁。"""
        self.embeddings

    @property
    def is_remote(self) -> bool:
        return bool(self._url)

    def _get_remote_client(self):
        """网络化 client 的惰性单例（进程内复用；server 端并发安全）。"""
        if self._remote_client is not None:
            return self._remote_client
        with _SESSION_LOCK:
            if self._remote_client is None:
                client = QdrantClient(url=self._url, api_key=self._api_key)
                try:
                    self._ensure_collection(client)
                except Exception:
                    client.close()
                    raise
                self._remote_client = client
                if not self._logged_init:
                    logger.info(
                        f"VectorStoreService connected Qdrant remote, "
                        f"url={self._url}, collection={self.collection_name}"
                    )
                    self._logged_init = True
        return self._remote_client

    def close(self):
        """释放网络化长连接（本地模式无长持有，空操作）。"""
        if self._remote_client is not None:
            try:
                self._remote_client.close()
            finally:
                self._remote_client = None

    def _open_client(self):
        if self.is_remote:
            return self._get_remote_client()

        last_error = None
        for attempt in range(_LOCK_RETRY_ATTEMPTS):
            try:
                client = QdrantClient(path=self._db_path)
            except RuntimeError as exc:
                if "already accessed" not in str(exc):
                    raise
                last_error = exc
                time.sleep(_LOCK_RETRY_DELAY * (attempt + 1))
                continue
            try:
                self._ensure_collection(client)
            except Exception:
                client.close()
                raise
            if not self._logged_init:
                logger.info(
                    f"VectorStoreService opened Qdrant local mode, "
                    f"path={self._db_path}, collection={self.collection_name}"
                )
                self._logged_init = True
            return client
        logger.error(f"Qdrant local mode lock contention: {last_error}")
        raise last_error

    @contextmanager
    def _session(self):
        """打开一个仅在本次操作内有效的 Qdrant client（锁随 close 释放）。

        进程内再串一道 `_SESSION_LOCK`：Qdrant local mode 同进程也不能并发开 client。
        锁覆盖「构造 -> 操作 -> close」整段，故同一进程的检索/写入天然排队。
        网络化模式无本地锁语义，不进这把锁（server 自管并发）。
        """
        if self.is_remote:
            # 长连接不随操作关闭（close() 由调用方显式释放）。
            yield self._get_remote_client()
            return
        with _SESSION_LOCK:
            client = self._open_client()
            try:
                yield client
            finally:
                client.close()

    def _ensure_collection(self, client):
        try:
            client.get_collection(self.collection_name)
            return
        except Exception:
            pass

        if self.hybrid:
            client.create_collection(
                collection_name=self.collection_name,
                vectors_config={
                    "dense": VectorParams(size=EMBEDDING_DIMENSION, distance=Distance.COSINE)
                },
                sparse_vectors_config={
                    "sparse": SparseVectorParams(modifier=Modifier.IDF)
                },
            )
            logger.info(
                f"Created hybrid Qdrant collection '{self.collection_name}' "
                f"(dense dim={EMBEDDING_DIMENSION} + sparse IDF)"
            )
            return

        client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(size=EMBEDDING_DIMENSION, distance=Distance.COSINE),
        )
        logger.info(f"Created Qdrant collection '{self.collection_name}' with dim={EMBEDDING_DIMENSION}")

    def _sparse_vector(self, text: str, *, query: bool = False) -> SparseVector:
        encoder = self._sparse_query_encoder if query else self._sparse_encoder
        if encoder is None:
            raise RuntimeError("hybrid 集合需要 sparse_encoder（app 层 BYOE）")
        indices, values = encoder(text)
        return SparseVector(indices=list(indices), values=list(values))

    @langsmith_service.trace(name="vector_store_add", metadata={"service": "VectorStoreService"})
    def add_documents(self, documents, metadata_list=None, ids=None):
        """写入向量点。

        ids 省略时生成随机 uuid；调用方给稳定 id（如 uuid5(entry_id)）即可让同一
        条目重复 upsert 覆盖旧点，而不是产生重复点（memory_agent 增量索引依赖此）。
        """
        try:
            texts = [doc.page_content if hasattr(doc, 'page_content') else doc for doc in documents]
            if ids is None:
                ids = [str(uuid.uuid4()) for _ in range(len(documents))]
            else:
                ids = [str(i) for i in ids]
                if len(ids) != len(documents):
                    raise ValueError("ids 长度必须与 documents 一致")

            if metadata_list is None:
                metadata_list = []
                for i, doc in enumerate(documents):
                    src = doc.metadata.get("source", f"document_{i}") if hasattr(doc, 'metadata') else f"document_{i}"
                    metadata_list.append({"source": src})

            embeddings = self.embeddings.embed_documents(texts)

            points = []
            for i in range(len(texts)):
                payload = {"text": texts[i]}
                payload.update(metadata_list[i])
                if self.hybrid:
                    vector = {
                        "dense": embeddings[i],
                        "sparse": self._sparse_vector(texts[i]),
                    }
                else:
                    vector = embeddings[i]
                points.append(PointStruct(id=ids[i], vector=vector, payload=payload))

            with self._session() as client:
                client.upsert(collection_name=self.collection_name, points=points)
            logger.info(f"Added {len(documents)} documents to Qdrant collection '{self.collection_name}'")
            return ids
        except Exception as e:
            logger.error(f"Error adding documents to vector store: {e}")
            raise

    @langsmith_service.trace(name="vector_store_search", metadata={"service": "VectorStoreService"})
    def search_documents(self, query, k=3, payload_filter=None):
        """该 store 的**原生检索**：hybrid 集合 → 原生 hybrid；否则 → 纯 dense。

        `payload_filter` 在 Qdrant 侧过滤（而非取回后再筛），避免欠填。
        值是标量 → 精确匹配（`MatchValue`）；值是 list/tuple/set → 任一匹配
        （`MatchAny`，供网关多值 ABAC，如 `classification ∈ {private, internal}`）。
        """
        if self.hybrid:
            return self.search_hybrid_documents(query, k=k, payload_filter=payload_filter)
        return self.search_dense_documents(query, k=k, payload_filter=payload_filter)

    def search_dense_documents(self, query, k=3, payload_filter=None):
        """**纯 dense**（余弦）检索——无论集合是否 hybrid 都可用。

        分数量纲 = 余弦，跨平面可比；用作**按阈值**的操作（如去重）的通道：hybrid 的
        融合分（RRF/DBSF）量纲不同，不能套余弦阈值（ADR-0019 D6）。
        """
        try:
            query_vec = self.embeddings.embed_query(query)
            query_filter = None
            if payload_filter:
                query_filter = Filter(
                    must=[_field_condition(key, value) for key, value in payload_filter.items()]
                )
            with self._session() as client:
                if self.hybrid:
                    results = client.query_points(
                        collection_name=self.collection_name,
                        query=query_vec,
                        using="dense",
                        query_filter=query_filter,
                        limit=k,
                        with_payload=True,
                    )
                else:
                    results = client.query_points(
                        collection_name=self.collection_name,
                        query=query_vec,
                        query_filter=query_filter,
                        limit=k,
                        with_payload=True,
                    )

            logger.info(f"Found {len(results.points)} dense documents for query: {query}")
            return self._format_results(results.points)
        except Exception as e:
            logger.error(f"Error searching dense documents: {e}")
            raise

    @staticmethod
    def _stabilize(result: dict) -> dict:
        """融合结果的**确定性同分排序**（issue #33）。

        实测：Qdrant server 的 `FusionQuery` 对**同分**（RRF 分数相等很常见）的并列项
        顺序不可复现（dense / sparse 单路是确定的）；同一集合、同一 query 两次调用
        会在并列处换序，`run_hash` 因此不稳定。这里按 `(score 降序, entry_id 升序)`
        重排——只影响**同分**并列，不改语义，却让共享平面的评测可复现。
        """
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        dists = result.get("distances", [[]])[0]
        order = sorted(
            range(len(docs)),
            key=lambda i: (-float(dists[i]), str((metas[i] or {}).get("entry_id")
                                                 or docs[i])),
        )
        return {
            "documents": [[docs[i] for i in order]],
            "metadatas": [[metas[i] for i in order]],
            "distances": [[dists[i] for i in order]],
        }

    @staticmethod
    def _format_results(points) -> dict:
        docs = []
        metas = []
        dists = []
        for r in points:
            payload = r.payload or {}
            docs.append(payload.get("text", ""))
            metas.append({k: v for k, v in payload.items() if k != "text"})
            dists.append(r.score)
        return {"documents": [docs], "metadatas": [metas], "distances": [dists]}

    def search_hybrid_documents(self, query, k=3, payload_filter=None,
                                prefetch_limit=None, fusion="rrf"):
        """**store 原生 hybrid**（issue #33 / ADR-0019 D4）：dense + sparse 两路
        `prefetch` 在 Qdrant 侧融合（RRF / DBSF）。**不在 Python 里再融合一次**。

        - `payload_filter` 透传（网关白名单构造，适配器不做授权）。
        - `fusion ∈ {"rrf", "dbsf"}`；分数量纲由 store 定义（ADR-0019 D6，不跨后端共用阈值）。
        - `prefetch_limit` 缺省 `max(k, 20)`：融合前每路的候选深度。
        """
        if self._sparse_encoder is None:
            raise RuntimeError("search_hybrid_documents 需要 sparse_encoder（app 层 BYOE）")
        try:
            query_vec = self.embeddings.embed_query(query)
            query_filter = None
            if payload_filter:
                query_filter = Filter(
                    must=[_field_condition(key, value) for key, value in payload_filter.items()]
                )
            depth = int(prefetch_limit or max(int(k), _HYBRID_PREFETCH_DEFAULT))
            fusion_query = {
                "rrf": Fusion.RRF,
                "dbsf": Fusion.DBSF,
            }.get(str(fusion).lower())
            if fusion_query is None:
                raise ValueError(f"未知 fusion：{fusion!r}（应为 rrf / dbsf）")
            # 过滤**必须挂在每个 prefetch 上**：实测 Qdrant **local mode 在有 prefetch 时
            # 忽略顶层 `query_filter`**（server 会生效）→ 只靠顶层过滤会在本地泄漏跨租户命中。
            # 两处都挂：本地靠 prefetch、server 双保险（仍是同一 filter，只可收窄）。
            with self._session() as client:
                results = client.query_points(
                    collection_name=self.collection_name,
                    prefetch=[
                        Prefetch(query=query_vec, using="dense", limit=depth,
                                 filter=query_filter),
                        Prefetch(query=self._sparse_vector(query, query=True),
                                 using="sparse", limit=depth, filter=query_filter),
                    ],
                    query=FusionQuery(fusion=fusion_query),
                    query_filter=query_filter,
                    limit=k,
                    with_payload=True,
                )
            logger.info(
                f"Native hybrid ({fusion}) found {len(results.points)} documents for query: {query}"
            )
            return self._stabilize(self._format_results(results.points))
        except Exception as e:
            logger.error(f"Error in hybrid search: {e}")
            raise

    def _scroll_all(self, client):
        all_docs = []
        all_metas = []
        offset = None
        while True:
            pts, offset = client.scroll(
                collection_name=self.collection_name,
                limit=1000,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for pt in pts:
                payload = pt.payload or {}
                all_docs.append(payload.get("text", ""))
                all_metas.append({k: v for k, v in payload.items() if k != "text"})
            if offset is None:
                break
        return all_docs, all_metas

    @langsmith_service.trace(name="vector_store_keyword", metadata={"service": "VectorStoreService"})
    def search_by_keyword(self, keyword, source_filter=None):
        try:
            with self._session() as client:
                docs, metas = self._scroll_all(client)
            out = []
            for doc, meta in zip(docs, metas):
                if keyword in doc and (
                    source_filter is None or source_filter in (meta.get("source") or "")
                ):
                    out.append({"document": doc, "metadata": meta})
            logger.info(f"keyword '{keyword}' matched {len(out)} chunks")
            return out
        except Exception as e:
            logger.error(f"Error in keyword search: {e}")
            return []

    @langsmith_service.trace(name="vector_store_keywords", metadata={"service": "VectorStoreService"})
    def search_by_keywords(self, keywords, source_filter=None):
        """多关键词子串匹配：一次扫描，命中任一关键词即入选。

        返回 `[{"document", "metadata", "matched", "score"}]`，按
        (命中关键词数, 命中次数) 降序。`matched` 供调用方做融合加权。
        与 `search_by_anchors` 的区别：不做强/弱锚点门槛，纯命中即召回。
        """
        try:
            keywords = [k for k in (keywords or []) if k]
            if not keywords:
                return []
            with self._session() as client:
                docs, metas = self._scroll_all(client)
            out = []
            for doc, meta in zip(docs, metas):
                if source_filter and source_filter not in (meta.get("source") or ""):
                    continue
                matched = [k for k in keywords if k in doc]
                if not matched:
                    continue
                out.append({
                    "document": doc,
                    "metadata": meta,
                    "matched": len(matched),
                    "score": sum(doc.count(k) for k in matched),
                })
            out.sort(key=lambda x: (x["matched"], x["score"]), reverse=True)
            logger.info(f"keywords {keywords} matched {len(out)} chunks")
            return out
        except Exception as e:
            logger.error(f"Error in keywords search: {e}")
            return []

    @langsmith_service.trace(name="vector_store_anchors", metadata={"service": "VectorStoreService"})
    def search_by_anchors(self, anchors, source_filter=None):
        try:
            if not anchors:
                return []
            with self._session() as client:
                docs, metas = self._scroll_all(client)
            strong_anchors = {a for a in anchors if len(a) >= 3}
            scored = []
            for doc, meta in zip(docs, metas):
                if source_filter and source_filter not in (meta.get("source") or ""):
                    continue
                matched = [a for a in anchors if a in doc]
                if not matched:
                    continue
                matched_strong = [a for a in matched if len(a) >= 3]
                weak_count = sum(1 for a in matched if len(a) < 3)
                if not (len(matched_strong) >= 1 or weak_count >= 2):
                    continue
                score = sum(doc.count(a) for a in matched)
                if "下列职权" in doc:
                    score += 8
                if "行使职权" in doc:
                    score += 3
                scored.append({"document": doc, "metadata": meta, "score": score})
            scored.sort(key=lambda x: x["score"], reverse=True)
            logger.info(f"anchors {anchors} matched {len(scored)} chunks")
            return scored
        except Exception as e:
            logger.error(f"Error in anchor search: {e}")
            return []

    @langsmith_service.trace(name="vector_store_delete", metadata={"service": "VectorStoreService"})
    def delete_documents(self, ids):
        try:
            import uuid as _uuid
            uid_list = [_uuid.UUID(id_str) for id_str in ids]
            with self._session() as client:
                client.delete(
                    collection_name=self.collection_name,
                    points_selector=uid_list,
                )
            logger.info(f"Deleted {len(ids)} documents from vector store")
            return True
        except Exception as e:
            logger.error(f"Error deleting documents: {e}")
            raise

    def retrieve_documents(self, ids):
        """按点 id 取回 payload（不做语义检索）——供按 id 读条目用（共享平面，issue #33）。

        与 `delete_documents` 同口径：id 一律按 UUID 解析。返回 payload dict 列表，
        未命中的 id 直接缺席（调用方按返回判断存在性）。
        """
        try:
            import uuid as _uuid
            uid_list = [_uuid.UUID(str(id_str)) for id_str in ids]
            with self._session() as client:
                records = client.retrieve(
                    collection_name=self.collection_name,
                    ids=uid_list,
                    with_payload=True,
                    with_vectors=False,
                )
            return [dict(record.payload or {}) for record in records]
        except Exception as e:
            logger.error(f"Error retrieving documents by id: {e}")
            raise

    @langsmith_service.trace(name="vector_store_count", metadata={"service": "VectorStoreService"})
    def get_document_count(self):
        try:
            with self._session() as client:
                info = client.get_collection(self.collection_name)
            count = info.points_count if info else 0
            logger.info(f"Vector store contains {count} documents")
            return count
        except Exception as e:
            logger.error(f"Error getting document count: {e}")
            return 0

    @langsmith_service.trace(name="vector_store_clear", metadata={"service": "VectorStoreService"})
    def clear_all_documents(self):
        """清空集合内所有点。

        实测（local mode）：`delete_collection` / `recreate_collection` 只摘掉元数据，
        同名 `create_collection` 会把磁盘上的旧点**复活**（3 -> 0 -> 3）。所以这里
        不丢集合，改用空 filter 的 `FilterSelector` 删光所有点。
        """
        try:
            with self._session() as client:
                client.delete(
                    collection_name=self.collection_name,
                    points_selector=FilterSelector(filter=Filter()),
                )
            logger.info(f"Cleared all documents in collection '{self.collection_name}'")
            return True
        except Exception as e:
            logger.error(f"Error clearing documents: {e}")
            raise
