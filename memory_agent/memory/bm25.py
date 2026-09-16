"""BM25 稀疏编码（`fastembed`，BYOE，app 层）——ADR-0019 D5/D7 的落地。

为什么在 app 层：Qdrant **local mode** 没有服务端 BM25（「内建 BM25 免 fastembed」只在
server ≥1.15.3 成立，见 `experiments/qdrant-local-mode-capabilities/`），本地嵌入模式
必须客户端编码。这里用 `fastembed` 的 `Qdrant/bm25` 产出 BM25 词权重（tf 饱和），
IDF 仍由集合的 `SparseVectorParams(modifier=Idf)` 施加。

**doc / query 不对称**：BM25 的文档侧有 tf 饱和、查询侧通常是词命中——fastembed 用
`embed`（文档）与 `query_embed`（查询）分开表达。故本编码器**同时**暴露
`encode_document` / `encode_query`，接缝按用途取（`VectorStoreService.sparse_query_encoder`）。

`fastembed` 是**可选软依赖**（`memory-agent[bm25]`/本地手动装）：只有显式把
`MEMORY_SPARSE_BACKEND=bm25` 时才 import，不压默认包体（#26）。
"""
from __future__ import annotations

DEFAULT_BM25_MODEL = "Qdrant/bm25"


class Bm25Encoder:
    """把文本编码成 Qdrant `SparseVector` 的 `(indices, values)`（BM25 词权重）。"""

    def __init__(self, model_name: str = DEFAULT_BM25_MODEL, cache_dir: str | None = None):
        from fastembed.sparse import SparseTextEmbedding

        self.model_name = model_name
        self._model = SparseTextEmbedding(model_name, cache_dir=cache_dir)

    @staticmethod
    def _to_tuple(embedding) -> tuple[list[int], list[float]]:
        return [int(i) for i in embedding.indices], [float(v) for v in embedding.values]

    def encode_document(self, text: str) -> tuple[list[int], list[float]]:
        return self._to_tuple(next(iter(self._model.embed([text]))))

    def encode_query(self, text: str) -> tuple[list[int], list[float]]:
        return self._to_tuple(next(iter(self._model.query_embed([text]))))

    def __call__(self, text: str) -> tuple[list[int], list[float]]:
        """默认按**文档**口径编码（端口的历史契约是单 callable = 文档编码器）。"""
        return self.encode_document(text)


__all__ = ["Bm25Encoder", "DEFAULT_BM25_MODEL"]
