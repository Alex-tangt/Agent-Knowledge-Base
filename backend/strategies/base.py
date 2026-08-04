"""策略抽象基类 — 将领域特化的切分与检索逻辑从通用引擎中剥离。

- SplitStrategy：文档切分策略（入库时按 KB 配置绑定）
- RetrievalStrategy：检索策略（查询时按 KB 配置生效）
"""
from abc import ABC, abstractmethod
from typing import List


class SplitStrategy(ABC):
    """文档切分策略。输入 langchain Documents，输出切分后的 Documents。"""

    @abstractmethod
    def split(self, documents: List) -> List:
        """按领域规则将加载的文档切分为片段。"""
        raise NotImplementedError


class RetrievalStrategy(ABC):
    """混合检索策略。返回与 vector_store.search_documents 一致的格式：
    {"documents": [[...]], "metadatas": [[...]], "distances": [[...]]}
    """

    @abstractmethod
    def retrieve(self, query: str, vector_store, pool_size: int) -> dict:
        """在指定向量库上执行领域相关的混合检索，返回候选集。"""
        raise NotImplementedError
