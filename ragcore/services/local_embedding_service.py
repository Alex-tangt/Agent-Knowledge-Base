from ragcore.config.config import LOCAL_EMBEDDING_MODEL
from ragcore.config.hf import ensure_hf_offline

# 必须在 import sentence_transformers / HF 之前：模型已缓存则切离线，避免冷启动外呼（#18）。
ensure_hf_offline([LOCAL_EMBEDDING_MODEL])

from sentence_transformers import SentenceTransformer  # noqa: E402
from ragcore.utils.logger import logger  # noqa: E402


class LocalEmbeddingService:
    def __init__(self, model_name="BAAI/bge-m3"):
        try:
            self.model = SentenceTransformer(model_name, local_files_only=True)
            self._dim = self.model.get_embedding_dimension()
            logger.info(f"Local embedding model {model_name} loaded successfully (dim={self._dim})")
        except Exception as e:
            logger.error(f"Failed to load local embedding model: {e}")
            raise

    @property
    def dimension(self):
        return self._dim or 1024

    def embed_documents(self, documents):
        try:
            embeddings = self.model.encode(
                documents,
                convert_to_tensor=False,
                normalize_embeddings=True,
            )
            return embeddings.tolist()
        except Exception as e:
            logger.error(f"Error embedding documents: {e}")
            raise

    def embed_query(self, query):
        try:
            embedding = self.model.encode(
                [query],
                convert_to_tensor=False,
                normalize_embeddings=True,
            )
            return embedding[0].tolist()
        except Exception as e:
            logger.error(f"Error embedding query: {e}")
            raise
