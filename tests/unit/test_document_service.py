import re

import pytest

from strategies.legal import (
    _extract_title,
    LegalSplitStrategy,
    SPLIT_ART_RE as ART_RE,
)
from config.config import ARTICLE_MAX_CHARS


# --- _extract_title ---

class TestExtractTitle:
    def test_extract_h1(self):
        title = _extract_title("# 中华人民共和国劳动合同法")
        assert title == "中华人民共和国劳动合同法"

    def test_extract_h1_with_extra_spaces(self):
        title = _extract_title("#   食品安全法实施细则  ")
        assert title == "食品安全法实施细则"

    def test_extract_h2_ignored(self):
        title = _extract_title("## 副标题\n正文内容")
        assert title == ""

    def test_heading_not_at_start(self):
        title = _extract_title("前言内容\n# 真正标题\n正文")
        assert title == "真正标题"

    def test_no_heading(self):
        title = _extract_title("这是一段没有标题的文本")
        assert title == ""

    def test_empty_text(self):
        title = _extract_title("")
        assert title == ""

    def test_takes_first_h1(self):
        title = _extract_title("# 第一标题\n# 第二标题")
        assert title == "第一标题"


# --- DocumentService methods ---

class TestFitWindow:
    @pytest.fixture
    def svc(self):
        return LegalSplitStrategy()

    def test_short_text_stays_intact(self, svc):
        result = svc._fit_window("短文本")
        assert result == ["短文本"]

    def test_exactly_max_chars(self, svc):
        text = "A" * ARTICLE_MAX_CHARS
        result = svc._fit_window(text)
        assert len(result) == 1
        assert result[0] == text

    def test_text_longer_than_max_splits(self, svc):
        chunk = "A" * 300 + "。" + "B" * 300 + "；" + "C" * 300
        assert len(chunk) > ARTICLE_MAX_CHARS
        result = svc._fit_window(chunk)
        assert len(result) >= 2

    def test_very_long_piece_hard_split(self, svc):
        piece = "A" * (ARTICLE_MAX_CHARS + 200)
        result = svc._fit_window(piece)
        assert len(result) >= 2

    def test_empty_text(self, svc):
        result = svc._fit_window("")
        assert result == [""]

    def test_only_separators(self, svc):
        result = svc._fit_window("。；；。")
        assert result == ["。；；。"]


class TestSplitByArticles:
    @pytest.fixture
    def svc(self):
        return LegalSplitStrategy()

    def test_basic_article_split(self, svc):
        text = "# 测试法\n\n第1条 第一条内容。\n第2条 第二条内容。\n第3条 第三条内容。"
        matches = list(ART_RE.finditer(text))
        result = svc._split_by_articles(text, "测试法", matches)
        assert len(result) >= 3

    def test_leading_text_before_first_article(self, svc):
        text = "# 测试法\n\n前言部分内容。\n第1条 第一条正文。\n第2条 第二条正文。\n第3条 第三条正文。"
        matches = list(ART_RE.finditer(text))
        result = svc._split_by_articles(text, "测试法", matches)
        assert len(result) >= 4

    def test_no_leading_text(self, svc):
        text = "第1条 第一条内容。\n第2条 第二条内容。\n第3条 第三条内容。"
        matches = list(ART_RE.finditer(text))
        result = svc._split_by_articles(text, "测试法", matches)
        assert len(result) >= 3

    def test_title_prepended_after_first(self, svc):
        text = "# 某法\n\n第1条 第一条内容。\n第2条 第二条内容。\n第3条 第三条内容。"
        matches = list(ART_RE.finditer(text))
        result = svc._split_by_articles(text, "某法", matches)
        non_first = [seg for seg in result if seg.strip().startswith("某法")]
        assert len(non_first) >= 2

    def test_non_empty_segments(self, svc):
        text = "# 法\n\n第1条 内容A。\n第2条 内容B。\n第3条 内容C。"
        matches = list(ART_RE.finditer(text))
        result = svc._split_by_articles(text, "法", matches)
        for seg in result:
            assert seg.strip() != ""
