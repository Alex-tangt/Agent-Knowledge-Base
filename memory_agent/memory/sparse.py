"""词法稀疏编码（BYOE，app 层）：给 Qdrant 原生 hybrid 提供 sparse 向量。

为什么在 app 层算：ADR-0025 D16 把**嵌入（BYOE）**划给 app 层；ADR-0019 D7 的
"内建 BM25 免 fastembed"**只在 Qdrant server**成立，本地嵌入模式必须客户端编码。
这里用一个**零依赖、确定性**的词频编码器覆盖**两个平面**（本地 `path=` 与网络
`url=`），使同一份 sparse 语义在两个后端上完全一致——这是 #33 "同一评测集在两后端
可比"的前提。

IDF 不在本地算：集合的 sparse 向量配置带 `modifier=Idf`，由 Qdrant（本地或服务端）
按集合统计施加。app 只提供**词频权重** `1 + ln(tf)`（次线性，抑制高频词）。

已知取舍：
- token 映射用 blake2b 哈希到固定维度空间，**不建全局词表**（确定性、无状态、两后端
  无需对齐词表）。哈希碰撞会合并罕见词——在小语料里概率可忽略。
- 中文按**二元组**切（与 `ragcore/strategies/default.py::extract_keywords` 同口径），
  ASCII 按词切并小写化。
"""
from __future__ import annotations

import hashlib
import math
import re

# 稀疏索引空间（2^20）：足够大以压低碰撞，又远小于 Qdrant 索引上限。
SPARSE_DIM = 1 << 20

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-+]*")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")

# 与 default.py 同口径的停用词（疑问 / 高频虚词），命中等于噪声。
_STOPWORDS = frozenset({
    "如何", "什么", "怎么", "哪些", "哪个", "那些", "是否", "可以", "需要",
    "以及", "关于", "根据", "我们", "你们", "他们", "她们", "这个", "那个",
    "这些", "一个", "为什", "么样", "多少", "怎样", "为什么", "有没有",
    "the", "and", "for", "with", "that", "this", "what", "how", "why",
    "which", "when", "where", "does", "are", "was", "were",
})


def tokenize(text: str) -> list[str]:
    """确定性分词：ASCII 词（小写）+ 中文二元组；过滤停用词与单字噪声。"""
    tokens: list[str] = []
    for match in _WORD_RE.finditer(text or ""):
        token = match.group(0).lower()
        if len(token) >= 2 and token not in _STOPWORDS:
            tokens.append(token)
    for run in _CJK_RUN_RE.findall(text or ""):
        if len(run) == 1:
            continue
        if len(run) == 2:
            if run not in _STOPWORDS:
                tokens.append(run)
            continue
        for i in range(len(run) - 1):  # 二元组
            bigram = run[i:i + 2]
            if bigram not in _STOPWORDS:
                tokens.append(bigram)
    return tokens


def _index_of(token: str) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % SPARSE_DIM


def encode_sparse(text: str) -> tuple[list[int], list[float]]:
    """文本 -> `(indices, values)`（供 `SparseVector`）。

    值 = `1 + ln(tf)`（次线性词频）。空文本返回空向量（Qdrant 侧视为无稀疏命中）。
    """
    counts: dict[str, int] = {}
    for token in tokenize(text):
        counts[token] = counts.get(token, 0) + 1
    if not counts:
        return [], []
    indices: list[int] = []
    values: list[float] = []
    for token, tf in counts.items():
        indices.append(_index_of(token))
        values.append(1.0 + math.log(tf))
    return indices, values


__all__ = ["SPARSE_DIM", "encode_sparse", "tokenize"]
