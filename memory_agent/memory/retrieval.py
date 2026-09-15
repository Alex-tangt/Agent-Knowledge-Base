"""记忆检索接缝（issue #24）：把记忆检索接到 ragcore 的检索策略 + 重排。

- 召回：`ragcore.strategies.DefaultRetrievalStrategy`（向量 + 关键词），不再直接调
  `VectorStoreService.search_documents` 绕过策略层。
- 重排：可选复用 `ragcore.services.reranker_service.RerankerService`（交叉编码器）。
- 这里就是 #20「追加已定决策 · 检索链路归属」定的**目标链路**（向量 + 关键词 + rerank）；
  评测基座只调用它，不另起炉灶。

重排默认**关**：daemon 已常驻一份 BGE-M3（约 3.9GB），再加一份 reranker 会重演 #19
的内存压力。需要目标链路的评测 / 场景用 `MEMORY_RERANK=1` 打开。
"""
from __future__ import annotations

from ragcore.strategies.default import DefaultRetrievalStrategy


def default_reranker_factory():
    """惰性构造 ragcore 的交叉编码器重排器（不到重排这一步不加载权重）。"""
    from ragcore.services.reranker_service import RerankerService
    from memory_agent.settings import RERANK_MODEL
    return RerankerService(RERANK_MODEL)


class MemoryRetriever:
    """记忆检索：策略召回（向量 + 关键词）+ 可选交叉编码器重排。

    返回按相关性**降序**的候选 `[(score, text, metadata)]`，score 越大越相关：
    - 未重排：关键词命中批次分数 >1，其次为向量余弦相似度（∈[-1,1]）。
    - 重排后：交叉编码器原始分数（logit）。
    """

    def __init__(self, store, *, strategy=None, reranker=None,
                 reranker_factory=None, pool_size=None, rerank_max_chars=None):
        self.store = store
        self.strategy = strategy or DefaultRetrievalStrategy()
        self._reranker = reranker
        self._reranker_factory = reranker_factory
        from memory_agent.settings import RERANK_MAX_CHARS, RETRIEVAL_POOL
        if pool_size is None:
            pool_size = RETRIEVAL_POOL
        if rerank_max_chars is None:
            rerank_max_chars = RERANK_MAX_CHARS
        self.pool_size = pool_size
        self.rerank_max_chars = rerank_max_chars

    @property
    def reranker(self):
        if self._reranker is None and self._reranker_factory is not None:
            self._reranker = self._reranker_factory()
        return self._reranker

    def retrieve(self, query: str, *, k: int = 5,
                 payload_filter: dict | None = None) -> list[tuple[float, str, dict]]:
        result = self.strategy.retrieve(
            query, self.store, pool_size=self.pool_size, payload_filter=payload_filter)
        docs = result["documents"][0] if result.get("documents") else []
        metas = result["metadatas"][0] if result.get("metadatas") else []
        dists = result["distances"][0] if result.get("distances") else []
        candidates = [
            (float(dist), doc, meta or {})
            for doc, meta, dist in zip(docs, metas, dists)
        ]
        reranker = self.reranker
        if reranker is not None and candidates:
            candidates = self._rerank(query, candidates, reranker)
        return candidates[:k]

    def _rerank(self, query, candidates, reranker):
        """交叉编码器重排整个候选池，返回同形降序候选。

        送进 reranker 的正文按 `rerank_max_chars` 截断（BGE reranker 默认
        max_seq_length=8192，整条 6000 字条目会慢一个数量级，见 settings）。
        """
        unique: dict[str, tuple] = {}
        for score, doc, meta in candidates:
            unique.setdefault(doc, (score, doc, meta))
        by_truncated: dict[str, str] = {}
        for doc in unique:
            by_truncated.setdefault(doc[:self.rerank_max_chars], doc)

        ranked = reranker.rerank(query, list(by_truncated.keys()), top_k=len(by_truncated))
        out = []
        covered: set[str] = set()
        for score, text in ranked:
            doc = by_truncated.get(text)
            if doc is None or doc in covered:
                continue
            covered.add(doc)
            entry = unique[doc]
            out.append((float(score), entry[1], entry[2]))
        for doc, entry in unique.items():  # 重排未覆盖的兜底（理论上不发生）
            if doc not in covered:
                out.append(entry)
        return out
