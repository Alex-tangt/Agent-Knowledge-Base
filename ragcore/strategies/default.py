"""通用默认策略 — 面向非法律域的通用 RAG 行为。

- DefaultSplitStrategy：纯递归切分（条文感知的通用兜底）
- DefaultRetrievalStrategy：混合召回（向量语义 + 关键词子串），可退回纯向量

混合召回的融合口径与 LegalRetrievalStrategy 一致：关键词命中排在向量命中之前，
再按分数降序。这里用「>1 的分数」编码关键词命中，使默认的「分数越大越相关」
排序天然把它排到余弦相似度（∈[-1,1]）之上。
"""
import re

from langchain_core.documents import Document
from ragcore.config.config import ARTICLE_MAX_CHARS
from ragcore.strategies.base import SplitStrategy, RetrievalStrategy

_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]*")

# 关键词通道的通用停用词：疑问词 / 语气词 / 高频虚词，命中它们等于噪声。
# 中文按二元组切，故这里放的是 2 字词；ASCII 词大小写不敏感地过滤。
_STOPWORDS = frozenset({
    "如何", "什么", "怎么", "哪些", "哪个", "那些", "是否", "可以", "需要",
    "以及", "关于", "根据", "我们", "你们", "他们", "她们", "这个", "那个",
    "这些", "一个", "为什", "么样", "多少", "怎样", "为什么", "有没有",
    "the", "and", "for", "with", "that", "this", "what", "how", "why",
    "which", "when", "where", "does", "are", "was", "were",
})


def _matches_filter(meta: dict, payload_filter: dict) -> bool:
    """关键词通道的后置过滤：标量精确匹配，序列为「任一匹配」（与 Qdrant `MatchAny` 同义）。"""
    for key, value in payload_filter.items():
        actual = meta.get(key)
        if isinstance(value, (list, tuple, set, frozenset)):
            if actual not in value:
                return False
        elif actual != value:
            return False
    return True


def extract_keywords(query: str, *, max_keywords: int = 12, min_len: int = 2) -> list[str]:
    """从查询里抽取用于**子串匹配**的关键词（确定性、无分词依赖）。

    - ASCII：按词切（保留原大小写，命中精确标识符如 `BGE-M3`）。
    - 中文：按二元组切（子串匹配的最小有义单元；长串整体匹配不到）。
    - 过滤停用词，按出现顺序去重，截断到 max_keywords。
    """
    if not query:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def _add(token: str) -> None:
        key = token.lower()
        if len(token) < min_len or key in _STOPWORDS or key in seen:
            return
        seen.add(key)
        out.append(token)

    for match in _WORD_RE.finditer(query):
        _add(match.group(0))
    for run in _CJK_RUN_RE.findall(query):
        if len(run) == min_len:
            _add(run)
        elif len(run) > min_len:
            for i in range(len(run) - min_len + 1):
                _add(run[i:i + min_len])
    return out[:max_keywords]


class DefaultSplitStrategy(SplitStrategy):
    """通用递归切分，不依赖任何领域标记。"""

    def split(self, documents: list) -> list:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=ARTICLE_MAX_CHARS,
            chunk_overlap=max(60, ARTICLE_MAX_CHARS // 8),
            length_function=len,
        )
        split_docs = []
        for doc in documents:
            source = doc.metadata.get("source", "")
            for seg in splitter.split_text(doc.page_content):
                if seg.strip():
                    split_docs.append(Document(page_content=seg, metadata={"source": source}))
        return split_docs


class DefaultRetrievalStrategy(RetrievalStrategy):
    """通用混合召回：向量语义 + 关键词子串。

    - `enable_keyword=False` 退回纯向量（消融 / 兼容）。
    - 关键词通道要求 vector_store 提供 `search_by_keywords`；缺失则静默跳过
      （基类契约只要求 `search_documents`）。
    - 关键词命中的条目分数 = `1.0 + 命中关键词占比`（>1），保证排在余弦之上。
    """

    def __init__(self, enable_keyword: bool = True, keyword_cap: int = 6,
                 max_keywords: int = 12):
        self.enable_keyword = enable_keyword
        self.keyword_cap = keyword_cap
        self.max_keywords = max_keywords

    def retrieve(self, query: str, vector_store, pool_size: int,
                 payload_filter: dict | None = None) -> dict:
        pool = vector_store.search_documents(query, k=pool_size, payload_filter=payload_filter)
        if not self.enable_keyword:
            return pool

        keywords = extract_keywords(query, max_keywords=self.max_keywords)
        searcher = getattr(vector_store, "search_by_keywords", None)
        if not keywords or searcher is None:
            return pool

        extras = []
        for match in searcher(keywords):
            meta = match.get("metadata") or {}
            if payload_filter and not _matches_filter(meta, payload_filter):
                continue
            matched = max(1, int(match.get("matched", 1)))
            extras.append((1.0 + matched / len(keywords), match["document"], meta))
            if len(extras) >= self.keyword_cap:
                break

        pool_docs = pool["documents"][0] if pool.get("documents") else []
        pool_metas = pool["metadatas"][0] if pool.get("metadatas") else []
        pool_dists = pool["distances"][0] if pool.get("distances") else []

        seen = {extra[1] for extra in extras}
        docs = [extra[1] for extra in extras]
        metas = [extra[2] for extra in extras]
        dists = [extra[0] for extra in extras]
        for doc, meta, dist in zip(pool_docs, pool_metas, pool_dists):
            if doc in seen:
                continue
            seen.add(doc)
            docs.append(doc)
            metas.append(meta)
            dists.append(dist)

        return {"documents": [docs], "metadatas": [metas], "distances": [dists]}
