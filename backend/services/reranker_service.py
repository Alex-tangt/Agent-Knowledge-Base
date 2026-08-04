from sentence_transformers import CrossEncoder
from utils.logger import logger


class RerankerService:
    def __init__(self, model_name="BAAI/bge-reranker-v2-m3"):
        try:
            self.model = CrossEncoder(
                model_name,
                trust_remote_code=True,
                local_files_only=True,
            )
            logger.info(f"Reranker model {model_name} loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load reranker model: {e}")
            raise

    def rerank(self, query, documents, top_k=5):
        if not documents:
            return []

        pairs = [[query, doc] for doc in documents]
        try:
            scores = self.model.predict(pairs, convert_to_tensor=False)
        except Exception as e:
            logger.error(f"Error predicting rerank scores: {e}")
            raise

        ranked = list(zip(scores, documents))
        ranked.sort(key=lambda x: x[0], reverse=True)
        return ranked[:top_k]
