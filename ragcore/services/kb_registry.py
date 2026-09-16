"""**兼容别名（deprecated）**：`知识库（kb）→ 视图（view）` 命名迁移（#37）。

新代码请用 `ragcore.services.view_registry`。本模块只为**尚未迁移**的内部接缝
（`rag_service` / `router_graph`）与外部旧调用保留同义名，不承载任何逻辑。

别名映射：
- `KBRegistry`        → `ViewRegistry`
- `KB_REGISTRY_FILE`  → `VIEW_REGISTRY_FILE`（`legal_web/view_registry.json`）
- `kb_registry`       → `view_registry`
"""
from ragcore.services.view_registry import (
    VIEW_REGISTRY_FILE,
    ViewRegistry,
    view_registry,
)

KB_REGISTRY_FILE = VIEW_REGISTRY_FILE
KBRegistry = ViewRegistry
kb_registry = view_registry

__all__ = ["KBRegistry", "KB_REGISTRY_FILE", "kb_registry"]
