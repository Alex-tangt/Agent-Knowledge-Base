from ragcore.config.config import LOCAL_RERANKER_MODEL, RERANK_MAX_SEQ_LENGTH
from ragcore.config.hf import ensure_hf_offline

# 必须在 import sentence_transformers / HF 之前：模型已缓存则切离线，避免冷启动外呼（#18）。
# 返回值 = 是否已切离线：缓存命中 → True，缺失 → False（保持联网，首次下载可用）。
# 用它决定 `local_files_only`，否则新机器永远下不到重排模型（同 embedding，#53）。
_OFFLINE = ensure_hf_offline([LOCAL_RERANKER_MODEL])

from sentence_transformers import CrossEncoder  # noqa: E402
from ragcore.utils.logger import logger  # noqa: E402


class RerankerService:
    """交叉编码器重排器。

    `max_seq_length` 是送排的 token 上限（issue #28）：只在「query+doc 超过该上限」
    时截断，短文本不生效（sbert 按 batch 内最长对动态 padding）。传 `None` 或把 env
    `RERANK_MAX_SEQ_LENGTH` 设为 none/off/0 即不设上限，回退模型默认（≈8192）。
    """

    def __init__(self, model_name=None, max_seq_length=RERANK_MAX_SEQ_LENGTH):
        model_name = model_name or LOCAL_RERANKER_MODEL
        kwargs = {"trust_remote_code": True, "local_files_only": _OFFLINE}
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
