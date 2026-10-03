"""循环预算（**代码判定**，不依赖模型自评）。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Budget:
    """硬预算：对齐 SKILL 的"≤2 次追加检索（共 ≤3 轮）"口径。

    `max_rounds` = **总轮数**上限（含首轮；命名与插件 `memory_research(max_hops=2)` 的
    "追加次数"区分开——同一个词在两侧语义不同，这里用 `rounds` 明确是总轮数）。
    `max_evidence` = 累计**展示给模型**的条目数上限。
    """
    max_rounds: int = 3
    max_evidence: int = 20

    def __post_init__(self) -> None:
        if self.max_rounds < 1:
            raise ValueError("max_rounds 至少为 1")
        if self.max_evidence < 1:
            raise ValueError("max_evidence 至少为 1")
