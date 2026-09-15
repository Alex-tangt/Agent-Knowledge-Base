"""DefaultRetrievalStrategy：混合召回（向量 + 关键词）与关键词抽取。

不碰模型 / Qdrant：用假 store 锁住融合顺序与过滤语义。

融合口径（#30）：**加法关键词增强**——`score = 余弦 + keyword_weight * (命中词数/关键词数)`。
关键词不再无条件压过余弦（旧行为），只在权重幅度内把「有词面佐证」的候选往上提。
"""

import pytest

from ragcore.strategies.default import DefaultRetrievalStrategy, extract_keywords


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
    def test_keyword_matched_vector_doc_is_boosted(self):
        store = FakeVectorStore(
            vector_hits=[_vector_hit("doc a", 0.80, "a"), _vector_hit("doc b", 0.78, "b")],
            keyword_hits=[_keyword_hit("doc b", 3, "b")],
        )
        result = DefaultRetrievalStrategy().retrieve("记忆检索", store, pool_size=5)
        docs = result["documents"][0]
        dists = result["distances"][0]
        assert docs == ["doc b", "doc a"]
        assert dists[0] == pytest.approx(0.78 + 0.05)
        assert dists[1] == pytest.approx(0.80)

    def test_keyword_boost_does_not_override_large_cosine_gap(self):
        store = FakeVectorStore(
            vector_hits=[_vector_hit("doc a", 0.90, "a"), _vector_hit("doc b", 0.50, "b")],
            keyword_hits=[_keyword_hit("doc b", 3, "b")],
        )
        result = DefaultRetrievalStrategy().retrieve("记忆检索", store, pool_size=5)
        assert result["documents"][0] == ["doc a", "doc b"]

    def test_keyword_weight_zero_keeps_cosine_order(self):
        store = FakeVectorStore(
            vector_hits=[_vector_hit("doc a", 0.80, "a"), _vector_hit("doc b", 0.78, "b")],
            keyword_hits=[_keyword_hit("doc b", 3, "b")],
        )
        result = DefaultRetrievalStrategy(keyword_weight=0.0).retrieve(
            "记忆检索", store, pool_size=5)
        assert result["documents"][0] == ["doc a", "doc b"]

    def test_keyword_only_extras_rank_below_vector(self):
        store = FakeVectorStore(
            vector_hits=[_vector_hit("vec doc", 0.5, "v")],
            keyword_hits=[_keyword_hit("kw doc", 3, "k")],
        )
        result = DefaultRetrievalStrategy().retrieve("记忆检索", store, pool_size=5)
        docs = result["documents"][0]
        assert docs == ["vec doc", "kw doc"]
        assert result["distances"][0][0] > result["distances"][0][1]

    def test_dedup_keyword_hit_already_in_vector_pool(self):
        store = FakeVectorStore(
            vector_hits=[_vector_hit("same doc", 0.78, "s")],
            keyword_hits=[_keyword_hit("same doc", 3, "s")],
        )
        result = DefaultRetrievalStrategy().retrieve("记忆检索", store, pool_size=5)
        assert result["documents"][0] == ["same doc"]
        assert result["distances"][0][0] == pytest.approx(0.83)

    def test_dedup_by_entry_id_not_text(self):
        store = FakeVectorStore(
            vector_hits=[_vector_hit("same text", 0.80, "a"), _vector_hit("same text", 0.70, "b")],
            keyword_hits=[],
        )
        result = DefaultRetrievalStrategy().retrieve("记忆检索", store, pool_size=5)
        assert result["documents"][0] == ["same text", "same text"]

    def test_enable_keyword_false_is_pure_vector(self):
        store = FakeVectorStore(
            vector_hits=[_vector_hit("vec doc", 0.5, "v")],
            keyword_hits=[_keyword_hit("kw doc", 3, "k")],
        )
        result = DefaultRetrievalStrategy(enable_keyword=False).retrieve(
            "记忆检索", store, pool_size=5)
        assert result["documents"][0] == ["vec doc"]
        assert all(call[0] == "vector" for call in store.calls)

    def test_payload_filter_applied_to_keyword_extras(self):
        store = FakeVectorStore(
            vector_hits=[],
            keyword_hits=[
                _keyword_hit("readonly doc", 3, "r", writable=False),
                _keyword_hit("writable doc", 3, "w", writable=True),
            ],
        )
        result = DefaultRetrievalStrategy().retrieve(
            "记忆检索", store, pool_size=5, payload_filter={"writable": True})
        assert result["documents"][0] == ["writable doc"]

    def test_keyword_cap_limits_extras(self):
        store = FakeVectorStore(
            vector_hits=[],
            keyword_hits=[_keyword_hit(f"doc {i}", 3, str(i)) for i in range(10)],
        )
        result = DefaultRetrievalStrategy(keyword_cap=3).retrieve(
            "记忆检索", store, pool_size=5)
        assert len(result["documents"][0]) == 3

    def test_store_without_keyword_channel_falls_back(self):
        class VectorOnly:
            def search_documents(self, query, k=3, payload_filter=None):
                return {"documents": [["only"]], "metadatas": [[{"entry_id": "o"}]],
                        "distances": [[0.5]]}

        result = DefaultRetrievalStrategy().retrieve("记忆检索", VectorOnly(), pool_size=5)
        assert result["documents"][0] == ["only"]

    def test_empty_query_skips_keyword_channel(self):
        store = FakeVectorStore(
            vector_hits=[_vector_hit("vec", 0.5, "v")],
            keyword_hits=[_keyword_hit("kw", 1, "k")],
        )
        result = DefaultRetrievalStrategy().retrieve("", store, pool_size=5)
        assert result["documents"][0] == ["vec"]
