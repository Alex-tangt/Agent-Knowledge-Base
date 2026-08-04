"""通用默认策略 — 面向非法律域的通用 RAG 行为。

- DefaultSplitStrategy：纯递归切分（条文感知的通用兜底）
- DefaultRetrievalStrategy：纯向量召回（混合检索的通用兜底）
"""
from langchain_core.documents import Document
from config.config import ARTICLE_MAX_CHARS
from strategies.base import SplitStrategy, RetrievalStrategy


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
    """纯向量召回，仅依赖 embedding 语义相似度。"""

    def retrieve(self, query: str, vector_store, pool_size: int) -> dict:
        return vector_store.search_documents(query, k=pool_size)
