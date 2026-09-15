"""通用默认策略 — 面向非法律域的通用 RAG 行为。

- DefaultSplitStrategy：纯递归切分（条文感知的通用兜底）
- DefaultRetrievalStrategy：混合召回（向量语义 + 关键词子串），可退回纯向量

**融合口径（#30，2026-09-15）**：加法关键词增强——`score = 余弦 + keyword_weight *
(命中关键词数 / 关键词总数)`。旧口径是「关键词命中分数 >1，无条件排在余弦之前」，
实测在记忆语料上把 recall@1 从 0.6407 拖到 0.2500（关键词噪声淹没向量信号，见
`experiments/fusion-selection/` 与 `docs/adr/0022` 的 #30 追加节）。新口径下关键词只在
权重幅度内给「有词面佐证」的候选加分，不再翻转明显更高的余弦；关键词独占候选
（不在向量池里）作为池尾补充。
"""
import re

from langchain_core.documents import Document
from ragcore.config.config import ARTICLE_MAX_CHARS
from ragcore.strategies.base import SplitStrategy, RetrievalStrategy

# 关键词增强的默认权重（#30）：在 45 题评测集上，beta∈[0.05, 0.08] 是 recall@1 平台
# （0.7074）；>0.1 开始把纯向量本来对的题压掉，<0.05 增益不足。取平台下沿 0.05。
DEFAULT_KEYWORD_WEIGHT = 0.05

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
    """通用混合召回：向量语义 + 关键词子串（加法增强）。

    - `enable_keyword=False` 退回纯向量（消融 / 兼容）。
    - 关键词通道要求 vector_store 提供 `search_by_keywords`；缺失则静默跳过
      （基类契约只要求 `search_documents`）。
    - 融合分数 = `余弦 + keyword_weight * (matched / len(keywords))`：关键词只加分，
      且幅度有界（默认 0.05），不会像旧口径那样无条件压过余弦。
    - `keyword_weight=0` 时分数即余弦（纯向量排序）；关键词独占候选（不在向量池里）
      以 `keyword_weight * 强度` 排在池尾补充，最多 `keyword_cap` 条。
    - 候选身份按 `metadata.entry_id`（缺失回退文本）去重——**跨仓库同文**是不同条目，
      不应被文本去重折叠。
    """

    def __init__(self, enable_keyword: bool = True, keyword_cap: int = 6,
                 max_keywords: int = 12,
                 keyword_weight: float = DEFAULT_KEYWORD_WEIGHT):
        self.enable_keyword = enable_keyword
        self.keyword_cap = keyword_cap
        self.max_keywords = max_keywords
        self.keyword_weight = keyword_weight

    @staticmethod
    def _identity(doc: str, meta: dict) -> str:
        return meta.get("entry_id") or doc

    def retrieve(self, query: str, vector_store, pool_size: int,
                 payload_filter: dict | None = None) -> dict:
        pool = vector_store.search_documents(query, k=pool_size, payload_filter=payload_filter)
        if not self.enable_keyword:
            return pool

        keywords = extract_keywords(query, max_keywords=self.max_keywords)
        searcher = getattr(vector_store, "search_by_keywords", None)
        if not keywords or searcher is None:
            return pool

        # 关键词匹配强度（全量命中，不截断——向量池内的命中也要参与加分）。
        strength: dict[str, float] = {}
        extras: list[tuple[str, str, dict]] = []
        for match in searcher(keywords):
            meta = match.get("metadata") or {}
            if payload_filter and any(
                meta.get(key) != value for key, value in payload_filter.items()
            ):
                continue
            key = self._identity(match["document"], meta)
            matched = max(1, int(match.get("matched", 1)))
            norm = matched / len(keywords)
            if norm > strength.get(key, 0.0):
                strength[key] = norm
            extras.append((key, match["document"], meta))

        pool_docs = pool["documents"][0] if pool.get("documents") else []
        pool_metas = pool["metadatas"][0] if pool.get("metadatas") else []
        pool_dists = pool["distances"][0] if pool.get("distances") else []

        scored: list[tuple[float, str, dict]] = []
        seen: set[str] = set()
        for doc, meta, dist in zip(pool_docs, pool_metas, pool_dists):
            key = self._identity(doc, meta or {})
            if key in seen:
                continue
            seen.add(key)
            scored.append(
                (float(dist) + self.keyword_weight * strength.get(key, 0.0), doc, meta))

        added = 0
        for key, doc, meta in extras:
            if key in seen:
                continue
            seen.add(key)
            scored.append((self.keyword_weight * strength.get(key, 0.0), doc, meta))
            added += 1
            if added >= self.keyword_cap:
                break

        scored.sort(key=lambda item: item[0], reverse=True)
        return {
            "documents": [[item[1] for item in scored]],
            "metadatas": [[item[2] for item in scored]],
            "distances": [[item[0] for item in scored]],
        }
