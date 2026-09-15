"""DefaultRetrievalStrategy：混合召回（向量 + 关键词）与关键词抽取。

不碰模型 / Qdrant：用假 store 锁住融合顺序与过滤语义。
"""
import pytest

from strategies.default import DefaultRetrievalStrategy, extract_keywords


# --- extract_keywords ---

class TestExtractKeywords:
    def test_ascii_identifier_kept_with_case(self):
        kws = extract_keywords("BGE-M3 检索")
        assert "BGE-M3" in kws

    def test_cjk_bigrams(self):
        kws = extract_keywords("记忆条目检索")
        assert "记忆" in kws
        assert "条目" in kws

    def test_stopwords_filtered(self):
        kws = extract_keywords("如何记忆")
        assert "如何" not in kws
        assert "记忆" in kws

    def test_empty_query(self):
        assert extract_keywords("") == []

    def test_max_keywords_caps_and_keeps_order(self):
        kws = extract_keywords("甲乙丙丁戊己庚辛壬癸子丑寅卯", max_keywords=3)
        assert len(kws) == 3
        assert kws == ["甲乙", "乙丙", "丙丁"]

    def test_deterministic(self):
        q = "混合检索 fusion 加权方案"
        assert extract_keywords(q) == extract_keywords(q)

    def test_ascii_lowercase_stopword_filtered(self):
        assert extract_keywords("the memory") == ["memory"]


class FakeVectorStore:
    def __init__(self, vector_hits=None, keyword_hits=None):
        self._vector_hits = vector_hits or []
        self._keyword_hits = keyword_hits or []
        self.calls = []

    def search_documents(self, query, k=3, payload_filter=None):
        self.calls.append(("vector", query, payload_filter))
        hits = self._vector_hits[:k]
        return {
            "documents": [[h[0] for h in hits]],
            "metadatas": [[h[2] for h in hits]],
            "distances": [[h[1] for h in hits]],
        }

    def search_by_keywords(self, keywords, source_filter=None):
        self.calls.append(("keywords", tuple(keywords)))
        return self._keyword_hits


def _vector_hit(text, score, entry_id):
    return (text, score, {"entry_id": entry_id, "writable": True})


def _keyword_hit(text, matched, entry_id, writable=True):
    return {"document": text, "metadata": {"entry_id": entry_id, "writable": writable},
            "matched": matched, "score": matched}


class TestDefaultRetrievalStrategy:
    def test_keyword_hits_rank_above_vector(self):
        store = FakeVectorStore(
            vector_hits=[_vector_hit("vec doc", 0.9, "v")],
            keyword_hits=[_keyword_hit("kw doc", 1, "k")],
        )
        result = DefaultRetrievalStrategy().retrieve("记忆检索", store, pool_size=5)
        docs = result["documents"][0]
        dists = result["distances"][0]
        assert docs == ["kw doc", "vec doc"]
        assert dists[0] > 1.0 > dists[1]

    def test_dedup_keyword_hit_already_in_vector_pool(self):
        store = FakeVectorStore(
            vector_hits=[_vector_hit("same doc", 0.9, "s")],
            keyword_hits=[_keyword_hit("same doc", 1, "s")],
        )
        result = DefaultRetrievalStrategy().retrieve("记忆", store, pool_size=5)
        assert result["documents"][0] == ["same doc"]

    def test_enable_keyword_false_is_pure_vector(self):
        store = FakeVectorStore(
            vector_hits=[_vector_hit("vec doc", 0.5, "v")],
            keyword_hits=[_keyword_hit("kw doc", 1, "k")],
        )
        result = DefaultRetrievalStrategy(enable_keyword=False).retrieve(
            "记忆", store, pool_size=5)
        assert result["documents"][0] == ["vec doc"]
        assert all(call[0] == "vector" for call in store.calls)

    def test_payload_filter_applied_to_keyword_extras(self):
        store = FakeVectorStore(
            vector_hits=[],
            keyword_hits=[
                _keyword_hit("readonly doc", 1, "r", writable=False),
                _keyword_hit("writable doc", 1, "w", writable=True),
            ],
        )
        result = DefaultRetrievalStrategy().retrieve(
            "记忆", store, pool_size=5, payload_filter={"writable": True})
        assert result["documents"][0] == ["writable doc"]

    def test_keyword_cap_limits_extras(self):
        store = FakeVectorStore(
            vector_hits=[],
            keyword_hits=[_keyword_hit(f"doc {i}", 1, str(i)) for i in range(10)],
        )
        result = DefaultRetrievalStrategy(keyword_cap=3).retrieve(
            "记忆", store, pool_size=5)
        assert len(result["documents"][0]) == 3

    def test_store_without_keyword_channel_falls_back(self):
        class VectorOnly:
            def search_documents(self, query, k=3, payload_filter=None):
                return {"documents": [["only"]], "metadatas": [[{"entry_id": "o"}]],
                        "distances": [[0.5]]}

        result = DefaultRetrievalStrategy().retrieve("记忆", VectorOnly(), pool_size=5)
        assert result["documents"][0] == ["only"]

    def test_empty_query_skips_keyword_channel(self):
        store = FakeVectorStore(
            vector_hits=[_vector_hit("vec", 0.5, "v")],
            keyword_hits=[_keyword_hit("kw", 1, "k")],
        )
        result = DefaultRetrievalStrategy().retrieve("", store, pool_size=5)
        assert result["documents"][0] == ["vec"]
