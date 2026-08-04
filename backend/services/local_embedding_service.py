from sentence_transformers import SentenceTransformer
from utils.logger import logger


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
