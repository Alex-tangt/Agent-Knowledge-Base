from ragcore.config.config import LOCAL_EMBEDDING_MODEL
from ragcore.config.hf import ensure_hf_offline

from sentence_transformers import SentenceTransformer  # noqa: E402
from ragcore.utils.logger import logger  # noqa: E402


class LocalEmbeddingService:
    def __init__(self, model_name="BAAI/bge-m3"):
        # 在**构造时**按真实 model_name 复查缓存/离线：缓存命中 → local_files_only
        # （零外呼）；缺失 → 允许下载（新机器首次可用）。放在 __init__ 而非 import 期，
        # 因为同一进程里 embed 常先加载并置全局离线，随后 reranker 缺失时需要在构造点
        # 撤销离线才能下载（#53 final test 在全新 WSL 上暴露）。
        local_only = ensure_hf_offline([model_name])
        try:
            self.model = SentenceTransformer(model_name, local_files_only=local_only)
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
