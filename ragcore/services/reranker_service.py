from sentence_transformers import CrossEncoder
from config.config import LOCAL_RERANKER_MODEL, RERANK_MAX_SEQ_LENGTH
from utils.logger import logger


class RerankerService:
    """交叉编码器重排器。

    `max_seq_length` 是送排的 token 上限（issue #28）：只在「query+doc 超过该上限」
    时截断，短文本不生效（sbert 按 batch 内最长对动态 padding）。传 `None` 或把 env
    `RERANK_MAX_SEQ_LENGTH` 设为 none/off/0 即不设上限，回退模型默认（≈8192）。
    """

    def __init__(self, model_name=None, max_seq_length=RERANK_MAX_SEQ_LENGTH):
        model_name = model_name or LOCAL_RERANKER_MODEL
        kwargs = {"trust_remote_code": True, "local_files_only": True}
        if max_seq_length is not None:
            kwargs["max_length"] = int(max_seq_length)
        try:
            self.model = CrossEncoder(model_name, **kwargs)
            logger.info(
                f"Reranker model {model_name} loaded successfully "
                f"(max_seq_length={self.model.max_seq_length})"
            )
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
