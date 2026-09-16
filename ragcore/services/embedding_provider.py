"""进程级本地嵌入单例（issue #43）。

为什么需要它：
- `VectorStoreService` 每次换代重建 store 都会新建一个 `LocalEmbeddingService`
  → 同一进程把 BGE-M3（~2.2GB 权重 / ~3.9GB 常驻）反复加载（实测：每次 `memory_reindex`
  步进都出现一次 `Loading embedding model...`）。
- 共享 daemon 现在还要把同一份 BGE-M3 暴露成 OpenAI 兼容 `/v1/embeddings`（#43）——
  端点与索引**必须共用同一个实例**，否则一个进程里塞两份 ~3.9GB。

本模块**不 import 重型依赖**（sentence_transformers 在首次取用时才惰性加载），
所以 `vector_store_service` / `mcp_server` 可以放心在模块顶层 import 它。
"""
from __future__ import annotations

import threading

from ragcore.config.config import LOCAL_EMBEDDING_MODEL

_INSTANCE = None
_LOCK = threading.Lock()


def get_local_embedding_service(model_name: str | None = None):
    """返回进程内唯一的本地嵌入服务（首次调用时才加载权重）。

    行为保持：与原先每次 `LocalEmbeddingService(model)` 完全一致（同模型、
    同 `normalize_embeddings=True`），只是不再重复构造。
    """
    global _INSTANCE
    if _INSTANCE is None:
        with _LOCK:
            if _INSTANCE is None:
                from ragcore.services.local_embedding_service import LocalEmbeddingService
                _INSTANCE = LocalEmbeddingService(model_name or LOCAL_EMBEDDING_MODEL)
    return _INSTANCE


def reset_local_embedding_service() -> None:
    """测试用：丢弃单例。"""
    global _INSTANCE
    _INSTANCE = None


__all__ = ["get_local_embedding_service", "reset_local_embedding_service"]
