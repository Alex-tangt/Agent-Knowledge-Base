import uuid
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from config.config import (
    USE_LOCAL_EMBEDDINGS,
    LOCAL_EMBEDDING_MODEL,
    VECTOR_DB_PATH,
    QDRANT_COLLECTION_NAME,
)
from services.langsmith_service import langsmith_service
from utils.logger import logger
from utils.model_status import EMBEDDING_DIMENSION, STATUS


class VectorStoreService:
    def __init__(self, collection_name=None):
        self.collection_name = collection_name or QDRANT_COLLECTION_NAME
        self._client = None
        self._embeddings = None
        self._initialized = False

    def _init_client(self):
        if self._client is None:
            self._client = QdrantClient(path=VECTOR_DB_PATH)
            self._ensure_collection()
            logger.info(f"VectorStoreService initialized with Qdrant local mode, collection={self.collection_name}")

    @property
    def client(self):
        self._init_client()
        return self._client

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
                from config.config import API_KEY, BASE_URL
                self._embeddings = OpenAIEmbeddings(
                    api_key=API_KEY,
                    base_url=BASE_URL,
                )
            STATUS["embedding"] = "ready"
            logger.info("Embedding model ready")
        return self._embeddings

    def warmup(self):
        self.embeddings

    def _ensure_collection(self):
        try:
            self.client.get_collection(self.collection_name)
        except Exception:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=EMBEDDING_DIMENSION, distance=Distance.COSINE),
            )
            logger.info(f"Created Qdrant collection '{self.collection_name}' with dim={EMBEDDING_DIMENSION}")

    @langsmith_service.trace(name="vector_store_add", metadata={"service": "VectorStoreService"})
    def add_documents(self, documents, metadata_list=None):
        try:
            texts = [doc.page_content if hasattr(doc, 'page_content') else doc for doc in documents]
            ids = [str(uuid.uuid4()) for _ in range(len(documents))]

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

            self.client.upsert(collection_name=self.collection_name, points=points)
            logger.info(f"Added {len(documents)} documents to Qdrant collection '{self.collection_name}'")
            return ids
        except Exception as e:
            logger.error(f"Error adding documents to vector store: {e}")
            raise

    def _ensure_collection_exists(self):
        try:
            self.client.get_collection(self.collection_name)
        except Exception:
            self._ensure_collection()

    @langsmith_service.trace(name="vector_store_search", metadata={"service": "VectorStoreService"})
    def search_documents(self, query, k=3):
        try:
            self._ensure_collection_exists()
            query_vec = self.embeddings.embed_query(query)
            results = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vec,
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

    def _scroll_all(self):
        all_docs = []
        all_metas = []
        offset = None
        while True:
            pts, offset = self.client.scroll(
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
            docs, metas = self._scroll_all()
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

    @langsmith_service.trace(name="vector_store_anchors", metadata={"service": "VectorStoreService"})
    def search_by_anchors(self, anchors, source_filter=None):
        try:
            docs, metas = self._scroll_all()
            if not anchors:
                return []
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
            self._ensure_collection_exists()
            import uuid as _uuid
            uid_list = [_uuid.UUID(id_str) for id_str in ids]
            self.client.delete(
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
            self._ensure_collection_exists()
            info = self.client.get_collection(self.collection_name)
            count = info.points_count if info else 0
            logger.info(f"Vector store contains {count} documents")
            return count
        except Exception as e:
            logger.error(f"Error getting document count: {e}")
            return 0

    @langsmith_service.trace(name="vector_store_clear", metadata={"service": "VectorStoreService"})
    def clear_all_documents(self):
        try:
            self.client.delete_collection(self.collection_name)
            self._ensure_collection()
            logger.info(f"Recreated collection '{self.collection_name}' (all documents cleared)")
            return True
        except Exception as e:
            logger.error(f"Error clearing documents: {e}")
            raise
