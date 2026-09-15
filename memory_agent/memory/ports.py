"""记忆存储端口（issue #23 / ADR-0018 / ADR-0019）：把「记忆检索依赖的存储」抽成稳定接缝。

为什么需要端口：`MemoryIndex` 不该知道 Qdrant local mode（目录锁、payload 形状、client
生命周期）。端口只描述**行为**——写（add/delete/clear/count/warmup）+ 召回
（search / 可选 search_by_keywords）。本地 Qdrant 是当前唯一实现
（`memory_agent.memory.store.QdrantLocalStore`）；云 store 适配同一端口（P2，ADR-0018）。

分类 / 驻留（#23 方向锁定 C′）：
- `classification ∈ {private, internal, public}`，缺省 `private`。
- `residency ∈ {local, cloud}`，缺省 `local`。
- **frontmatter 是真相源**；索引 payload 只是供过滤用的**镜像**。两个字段都是**可选**的，
  缺失时取默认值（保证向后兼容全局 KB；`kb.py` 不强制校验）。
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

CLASSIFICATION_VALUES = ("private", "internal", "public")
RESIDENCY_VALUES = ("local", "cloud")
DEFAULT_CLASSIFICATION = "private"
DEFAULT_RESIDENCY = "local"

PLANE_LOCAL = "local"
PLANE_CLOUD = "cloud"


@runtime_checkable
class VectorStore(Protocol):
    """记忆检索的存储端口。

    契约要点：
    - `plane` / `tenant`：来源平面与租户绑定。网关构造 store 时绑定 `tenant`；
      `search(tenant=...)` 只能**收窄**、不能放宽（ADR-0018 D2：网关唯一强制）。
    - `search` 返回与 ragcore 检索策略一致的 dict：
      `{"documents": [[...]], "metadatas": [[...]], "distances": [[...]]}`。
    - `search_by_keywords` 是**可选**关键词通道（实现缺失时策略跳过，见
      `strategies.default.DefaultRetrievalStrategy`）。
    - 点 id 由调用方给定（记忆条目用稳定 `uuid5(entry_id)`），重复 `add` 即覆盖。
    """

    plane: str
    tenant: str | None

    def add(self, texts: Sequence[str], metadata_list: list[dict] | None = None,
            ids: Sequence[str] | None = None) -> list[str]:
        """写入文本（内部负责嵌入）；返回写入的点 id 列表。"""

    def search(self, query: str, k: int = 3,
               payload_filter: Mapping[str, Any] | None = None,
               tenant: str | None = None) -> dict:
        """向量召回；`payload_filter` 为 {字段: 值} 精确匹配（下沉到存储侧）。"""

    def search_by_keywords(self, keywords: Sequence[str],
                           source_filter: str | None = None) -> list[dict]:
        """关键词子串召回（可选能力）；返回 [{document, metadata, matched, score}]。"""

    def delete(self, ids: Sequence[str]) -> bool:
        """按点 id 删除。"""

    def count(self) -> int:
        """集合当前点数。"""

    def clear(self) -> bool:
        """清空集合内所有点（保留集合）。"""

    def warmup(self) -> None:
        """预热嵌入模型（不触碰存储，不占锁）。"""
