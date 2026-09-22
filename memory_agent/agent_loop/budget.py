"""循环预算（**代码判定**，不依赖模型自评）。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Budget:
    """硬预算：对齐 SKILL 的"≤2 次追加检索（共 ≤3 轮）"口径。

    `max_hops` = 总轮数上限；`max_evidence` = 累计证据条数上限。
    """
    max_hops: int = 3
    max_evidence: int = 20

    def __post_init__(self) -> None:
        if self.max_hops < 1:
            raise ValueError("max_hops 至少为 1")
        if self.max_evidence < 1:
            raise ValueError("max_evidence 至少为 1")
