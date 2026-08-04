import json
import re
from unittest.mock import patch, MagicMock

import pytest

from strategies.legal import (
    _num_to_cn,
    _parse_article,
    _extract_key_anchors,
)
from services.rag_service import (
    NO_EVIDENCE_MESSAGE,
    RAGService,
)


# --- _num_to_cn ---

class TestNumToCn:
    def test_single_digit(self):
        assert _num_to_cn(1) == "一"
        assert _num_to_cn(3) == "三"
        assert _num_to_cn(9) == "九"

    def test_ten(self):
        assert _num_to_cn(10) == "一十"

    def test_teens(self):
        assert _num_to_cn(11) == "一十一"
        assert _num_to_cn(15) == "一十五"

    def test_twenty(self):
        assert _num_to_cn(20) == "二十"
        assert _num_to_cn(25) == "二十五"

    def test_hundreds(self):
        assert _num_to_cn(100) == "一百"
        assert _num_to_cn(123) == "一百二十三"
        assert _num_to_cn(200) == "二百"
        assert _num_to_cn(305) == "三百零五"

    def test_thousands(self):
        assert _num_to_cn(1000) == "一千"
        assert _num_to_cn(1010) == "一千零一十"
        assert _num_to_cn(1100) == "一千一百"

    def test_zero(self):
        assert _num_to_cn(0) == "零"

    def test_cn_string_passthrough(self):
        assert _num_to_cn("三") == "三"
        assert _num_to_cn("十二") == "十二"

    def test_digit_string(self):
        assert _num_to_cn("5") == "五"
        assert _num_to_cn("42") == "四十二"


# --- _parse_article ---

class TestParseArticle:
    def test_law_with_article_digit(self):
        law, num = _parse_article("劳动合同法第38条规定")
        assert law == "劳动合同法"
        assert num == "38"

    def test_article_only(self):
        law, num = _parse_article("根据第5条的规定")
        assert law is None
        assert num == "5"

    def test_cn_article_number(self):
        law, num = _parse_article("刑法第一百一十二条")
        assert law == "刑法"
        assert num == "一百一十二"

    def test_multiple_law_keywords_returns_first(self):
        law, num = _parse_article("民法典和劳动法第10条")
        assert law == "民法典"
        assert num == "10"

    def test_no_article(self):
        result = _parse_article("工资支付的规定是什么")
        assert result == (None, None)

    def test_empty_query(self):
        result = _parse_article("")
        assert result == (None, None)

    def test_whitespace_in_article(self):
        law, num = _parse_article("第 8 条")
        assert num == "8"

    def test_composite_article_number(self):
        law, num = _parse_article("劳动合同法第36条关于解除")
        assert law == "劳动合同法"
        assert num == "36"


# --- _extract_key_anchors ---

class TestExtractKeyAnchors:
    def test_basic_extraction(self):
        anchors = _extract_key_anchors("工资支付标准是什么")
        assert "工资" in anchors
        assert "标准" in anchors

    def test_law_keyword_removed(self):
        anchors = _extract_key_anchors("劳动合同法解除条件")
        assert "解除" in anchors
        assert "条件" in anchors

    def test_short_query(self):
        anchors = _extract_key_anchors("你好")
        assert isinstance(anchors, set)

    def test_empty_query(self):
        anchors = _extract_key_anchors("")
        assert anchors == set()

    def test_only_stop_chars(self):
        anchors = _extract_key_anchors("的了吗呢")
        assert isinstance(anchors, set)

    def test_generic_prefix_stripped(self):
        anchors = _extract_key_anchors("我国经济体制")
        assert "经济" in anchors

    def test_trigram_quadgram_extraction(self):
        anchors = _extract_key_anchors("行政处罚程序规定")
        assert len(anchors) > 0

    def test_result_is_set_of_strings(self):
        anchors = _extract_key_anchors("社会保险缴纳义务")
        for a in anchors:
            assert isinstance(a, str)
            assert len(a) >= 2

    def test_no_all_stop_char_anchors(self):
        anchors = _extract_key_anchors("工资支付标准")
        for a in anchors:
            assert not all(
                c in "的了吗呢啊吧哪哟在了对为和是与及或这其之等也也都就还被由向从到给让把将并但而若如那"
                for c in a
            )


# --- RAGService instance methods ---

class TestRAGServiceMethods:
    @pytest.fixture
    def rag(self):
        with patch.object(RAGService, "__init__", lambda self: None):
            svc = RAGService()
        return svc

    def _make_retrieved(self, docs=None, distances=None, sources=None):
        docs = docs or ["文档A内容", "文档B内容", "文档C内容"]
        distances = distances or [0.3, 0.5, 0.7]
        sources = sources or ["law_a.pdf", "law_b.pdf", "law_c.pdf"]
        metadatas = [{"source": s} for s in sources]
        return {
            "documents": [docs],
            "metadatas": [metadatas],
            "distances": [distances],
        }

    # --- _select_adaptive ---

    def test_select_within_factor(self, rag):
        import services.rag_service as rs
        original_factor = rs.ADAPTIVE_FACTOR
        original_max = rs.ADAPTIVE_MAX
        try:
            rs.ADAPTIVE_FACTOR = 1.8
            rs.ADAPTIVE_MAX = 8
            retrieved = self._make_retrieved(
                docs=["A", "B", "C", "D", "E"],
                distances=[0.5, 0.8, 0.9, 1.5, 2.0],
            )
            result = rag._select_adaptive(retrieved)
            selected_docs = result["documents"][0]
            assert len(selected_docs) == 3
            assert selected_docs == ["A", "B", "C"]
        finally:
            rs.ADAPTIVE_FACTOR = original_factor
            rs.ADAPTIVE_MAX = original_max

    def test_select_respects_adaptive_max(self, rag):
        import services.rag_service as rs
        original_factor = rs.ADAPTIVE_FACTOR
        original_max = rs.ADAPTIVE_MAX
        try:
            rs.ADAPTIVE_FACTOR = 100.0
            rs.ADAPTIVE_MAX = 2
            retrieved = self._make_retrieved(
                docs=["A", "B", "C", "D"],
                distances=[0.1, 0.2, 0.3, 0.4],
            )
            result = rag._select_adaptive(retrieved)
            assert len(result["documents"][0]) == 2
        finally:
            rs.ADAPTIVE_FACTOR = original_factor
            rs.ADAPTIVE_MAX = original_max

    def test_select_empty_docs(self, rag):
        retrieved = {"documents": [[]], "metadatas": [[]], "distances": [[]]}
        result = rag._select_adaptive(retrieved)
        assert result == retrieved

    def test_select_single_doc(self, rag):
        retrieved = self._make_retrieved(
            docs=["only doc"], distances=[10.0]
        )
        result = rag._select_adaptive(retrieved)
        assert len(result["documents"][0]) == 1

    def test_select_no_distances_key(self, rag):
        retrieved = {"documents": [["doc"]], "metadatas": [[{"source": "s"}]], "distances": [[]]}
        result = rag._select_adaptive(retrieved)
        assert result == retrieved

    # --- _has_evidence ---

    def test_has_evidence_true(self, rag):
        from config import config
        original = config.RELEVANCE_THRESHOLD
        try:
            config.RELEVANCE_THRESHOLD = 0.85
            retrieved = self._make_retrieved(distances=[0.3, 0.6])
            assert rag._has_evidence(retrieved) is True
        finally:
            config.RELEVANCE_THRESHOLD = original

    def test_has_evidence_false_above_threshold(self, rag):
        from config import config
        original = config.RELEVANCE_THRESHOLD
        try:
            config.RELEVANCE_THRESHOLD = 0.85
            retrieved = self._make_retrieved(distances=[0.9, 1.2])
            assert rag._has_evidence(retrieved) is False
        finally:
            config.RELEVANCE_THRESHOLD = original

    def test_has_evidence_empty(self, rag):
        retrieved = {"documents": [[]], "metadatas": [[]], "distances": [[]]}
        assert rag._has_evidence(retrieved) is False

    def test_has_evidence_threshold_none(self, rag):
        import services.rag_service as rs
        original = rs.RELEVANCE_THRESHOLD
        try:
            rs.RELEVANCE_THRESHOLD = None
            retrieved = self._make_retrieved(distances=[0.99, 1.5])
            assert rag._has_evidence(retrieved) is True
        finally:
            rs.RELEVANCE_THRESHOLD = original

    # --- _extract_sources ---

    def test_extract_sources(self, rag):
        retrieved = self._make_retrieved(
            docs=["内容A", "内容B"],
            distances=[0.1234, 0.5678],
            sources=["/data/law_a.pdf", "/data/law_b.pdf"],
        )
        sources = rag._extract_sources(retrieved)
        assert len(sources) == 2
        assert sources[0] == {
            "content": "内容A",
            "source": "law_a.pdf",
            "score": 0.1234,
            "chunk_id": 1,
        }
        assert sources[1] == {
            "content": "内容B",
            "source": "law_b.pdf",
            "score": 0.5678,
            "chunk_id": 2,
        }

    def test_extract_sources_empty(self, rag):
        retrieved = {"documents": [[]], "metadatas": [[]], "distances": [[]]}
        sources = rag._extract_sources(retrieved)
        assert sources == []

    def test_extract_sources_missing_metadata(self, rag):
        retrieved = {
            "documents": [["内容"]],
            "metadatas": [[]],
            "distances": [[0.5]],
        }
        sources = rag._extract_sources(retrieved)
        assert sources[0]["source"] == "文档1"

    def test_extract_sources_missing_distance(self, rag):
        retrieved = {
            "documents": [["内容"]],
            "metadatas": [[{"source": "x.pdf"}]],
            "distances": [[]],
        }
        sources = rag._extract_sources(retrieved)
        assert sources[0]["score"] is None

    # --- build_rag_prompt ---

    def test_build_rag_prompt_includes_context(self, rag):
        retrieved = self._make_retrieved(
            docs=["劳动合同法第三十八条原文"],
            sources=["labor_law.pdf"],
        )
        prompt = rag.build_rag_prompt("劳动者如何解除合同？", retrieved)
        assert "劳动合同法第三十八条原文" in prompt
        assert "labor_law.pdf" in prompt
        assert "劳动者如何解除合同？" in prompt
        assert "[1]" in prompt

    def test_build_rag_prompt_empty_docs(self, rag):
        retrieved = {"documents": [[]], "metadatas": [[]], "distances": [[]]}
        prompt = rag.build_rag_prompt("test query", retrieved)
        assert "test query" in prompt

    def test_build_rag_prompt_sources_annotation(self, rag):
        retrieved = self._make_retrieved(
            docs=["A内容", "B内容"],
            sources=["a.pdf", "b.pdf"],
        )
        prompt = rag.build_rag_prompt("query", retrieved)
        assert "[1]" in prompt
        assert "[2]" in prompt

    # --- _no_evidence_chunk ---

    def test_no_evidence_chunk(self, rag):
        chunk = rag._no_evidence_chunk()
        data = json.loads(chunk.strip())
        assert data["type"] == "content"
        assert data["content"] == NO_EVIDENCE_MESSAGE
        assert data["sources"] == []
