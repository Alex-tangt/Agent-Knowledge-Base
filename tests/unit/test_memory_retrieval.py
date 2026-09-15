"""MemoryRetriever：策略召回（向量 + 关键词）+ 可选交叉编码器重排。"""
from memory_agent.memory.retrieval import MemoryRetriever


def _vector_hit(text, score, entry_id, writable=True):
    return (text, score, {"entry_id": entry_id, "writable": writable})


def _keyword_hit(text, matched, entry_id, writable=True):
    return {"document": text, "metadata": {"entry_id": entry_id, "writable": writable},
            "matched": matched, "score": matched}


class FakeStore:
    def __init__(self, vector_hits=None, keyword_hits=None):
        self._vector_hits = vector_hits or []
        self._keyword_hits = keyword_hits or []
        self.vector_calls = []

    def search_documents(self, query, k=3, payload_filter=None):
        self.vector_calls.append(payload_filter)
        hits = self._vector_hits
        if payload_filter:
            hits = [h for h in hits
                    if all(h[2].get(key) == value for key, value in payload_filter.items())]
        hits = hits[:k]
        return {
            "documents": [[h[0] for h in hits]],
            "metadatas": [[h[2] for h in hits]],
            "distances": [[h[1] for h in hits]],
        }

    def search_by_keywords(self, keywords, source_filter=None):
        return self._keyword_hits


class FakeReranker:
    def __init__(self, scores):
        self.scores = scores
        self.calls = 0

    def rerank(self, query, documents, top_k=5):
        self.calls += 1
        ranked = sorted(((self.scores[doc], doc) for doc in documents), reverse=True)
        return ranked[:top_k]


def test_without_reranker_keeps_strategy_order():
    store = FakeStore(
        vector_hits=[_vector_hit("vec doc", 0.9, "v")],
        keyword_hits=[_keyword_hit("kw doc", 1, "k")],
    )
    result = MemoryRetriever(store).retrieve("记忆检索", k=5)
    assert [m["entry_id"] for _, _, m in result] == ["k", "v"]


def test_reranker_reorders_and_reports_logits():
    store = FakeStore(
        vector_hits=[_vector_hit("a", 0.9, "a"), _vector_hit("b", 0.8, "b")],
        keyword_hits=[],
    )
    reranker = FakeReranker({"a": 0.1, "b": 2.5})
    result = MemoryRetriever(store, reranker=reranker).retrieve("query", k=2)
    assert [m["entry_id"] for _, _, m in result] == ["b", "a"]
    assert [score for score, _, _ in result] == [2.5, 0.1]


def test_k_slices_after_rerank():
    store = FakeStore(vector_hits=[_vector_hit(f"d{i}", 0.1 * i, f"d{i}") for i in range(5)])
    reranker = FakeReranker({f"d{i}": float(i) for i in range(5)})
    result = MemoryRetriever(store, reranker=reranker).retrieve("q", k=2)
    assert [m["entry_id"] for _, _, m in result] == ["d4", "d3"]


def test_lazy_reranker_factory_called_once():
    store = FakeStore(vector_hits=[_vector_hit("a", 0.5, "a")])
    built = []

    def factory():
        built.append(1)
        return FakeReranker({"a": 1.0})

    retriever = MemoryRetriever(store, reranker_factory=factory)
    assert built == []
    retriever.retrieve("q", k=1)
    retriever.retrieve("q", k=1)
    assert built == [1]


def test_payload_filter_pushed_to_vector_and_keyword_channels():
    store = FakeStore(
        vector_hits=[_vector_hit("w", 0.9, "w", writable=True),
                     _vector_hit("r", 0.9, "r", writable=False)],
        keyword_hits=[_keyword_hit("kw-r", 1, "kr", writable=False),
                      _keyword_hit("kw-w", 1, "kw", writable=True)],
    )
    result = MemoryRetriever(store).retrieve("记忆", k=5, payload_filter={"writable": True})
    assert store.vector_calls == [{"writable": True}]
    assert [m["entry_id"] for _, _, m in result] == ["kw", "w"]
