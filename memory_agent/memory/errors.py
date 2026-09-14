"""索引错误类型：让「未构建」与「不自洽」可被调用方分别识别、且都显式失败。"""
from __future__ import annotations


class IndexNotBuiltError(RuntimeError):
    """索引尚未构建（无指针 / manifest 为空）。"""


class IndexConsistencyError(RuntimeError):
    """索引不自洽：manifest 条数 ≠ 集合点数（或重建中途失败）。

    出现即表示「表面正常、实际搜不到」的状态被检测到——宁可显式报错，也不静默
    返回空结果（issue #13 的硬要求）。
    """
