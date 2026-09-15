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
    MatchValue,
    PointStruct,
    VectorParams,
)
from config.config import (
    USE_LOCAL_EMBEDDINGS,
    LOCAL_EMBEDDING_MODEL,
    VECTOR_DB_PATH,
    QDRANT_COLLECTION_NAME,
)
from services.langsmith_service import langsmith_service
from utils.logger import logger
from utils.model_status import EMBEDDING_DIMENSION, STATUS

# local mode 独占锁：被别的进程挡住时短暂重试（锁只在别的进程的调用期存在）
_LOCK_RETRY_ATTEMPTS = 6
_LOCK_RETRY_DELAY = 0.05

# 同一进程内的 client 互斥（issue #19）：Qdrant local mode 的独占锁是「按目录 + 全进程」
# 的——同一进程里并发构造第二个 client 也会直接 RuntimeError（实测 4 线程 3 个立刻失败），
# 退避重试兜不住。单实例 daemon 并发服务多会话时必须把这把锁串起来。
_SESSION_LOCK = threading.RLock()


class VectorStoreService:
    """Qdrant local mode 包装。

    注意 local mode 的锁语义（实测）：锁在 **client 构造期**持有、`close()` 释放，
    构造函数没有绕过开关。因此这里**不缓存 client**——每次操作开/关一个
    （实测 ~19ms/次，相对嵌入开销是噪声）。好处是锁只在调用期存在，
    opencode 常驻的 MCP 服务不会整天独占存储目录，索引重建 / CLI / 冒烟脚本
    得以与其共存。
    """

    def __init__(self, collection_name=None, db_path=None, embeddings=None):
        self.collection_name = collection_name or QDRANT_COLLECTION_NAME
        self._db_path = db_path or VECTOR_DB_PATH
        self._embeddings = embeddings
        self._logged_init = False

    @property
    def embeddings(self):
        if self._embeddings is None:
            STATUS["embedding"] = "loading"
            logger.info("Loading embedding model...")
            if USE_LOCAL_EMBEDDINGS:
                from services.local_embedding_service import LocalEmbeddingService
                self._embeddings = LocalEmbeddingService(LOCAL_EMBEDDING_MODEL)
            else:
                from langchain_openai import OpenAIEmbeddings
                from config.llm import require_llm
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

    def _open_client(self):
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
        """
        with _SESSION_LOCK:
            client = self._open_client()
            try:
                yield client
            finally:
                client.close()

    def _ensure_collection(self, client):
        try:
            client.get_collection(self.collection_name)
        except Exception:
            client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=EMBEDDING_DIMENSION, distance=Distance.COSINE),
            )
            logger.info(f"Created Qdrant collection '{self.collection_name}' with dim={EMBEDDING_DIMENSION}")

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
                points.append(PointStruct(
                    id=ids[i],
                    vector=embeddings[i],
                    payload=payload,
                ))

            with self._session() as client:
                client.upsert(collection_name=self.collection_name, points=points)
            logger.info(f"Added {len(documents)} documents to Qdrant collection '{self.collection_name}'")
            return ids
        except Exception as e:
            logger.error(f"Error adding documents to vector store: {e}")
            raise

    @langsmith_service.trace(name="vector_store_search", metadata={"service": "VectorStoreService"})
    def search_documents(self, query, k=3, payload_filter=None):
        """向量检索。payload_filter 为 {字段: 值} 的精确匹配约束，在 Qdrant 侧过滤
        （而非取回后再筛），以避免过滤后欠填。"""
        try:
            query_vec = self.embeddings.embed_query(query)
            query_filter = None
            if payload_filter:
                query_filter = Filter(must=[
                    FieldCondition(key=key, match=MatchValue(value=value))
                    for key, value in payload_filter.items()
                ])
            with self._session() as client:
                results = client.query_points(
                    collection_name=self.collection_name,
                    query=query_vec,
                    query_filter=query_filter,
                    limit=k,
                    with_payload=True,
                )

            docs = []
            metas = []
            dists = []
            for r in results.points:
                payload = r.payload or {}
                docs.append(payload.get("text", ""))
                metas.append({k: v for k, v in payload.items() if k != "text"})
                dists.append(r.score)

            logger.info(f"Found {len(docs)} relevant documents for query: {query}")
            return {
                "documents": [docs],
                "metadatas": [metas],
                "distances": [dists],
            }
        except Exception as e:
            logger.error(f"Error searching documents: {e}")
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
