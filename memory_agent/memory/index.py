"""派生索引：条目级向量索引 + manifest（可从 Markdown 全量重建）。

- 索引载体：Qdrant local mode（独立路径，见 memory_agent/config.py）。
- manifest：id -> 条目元数据 + 文件路径，供 memory_get 读回真相源。
- 索引与 manifest 都是派生物，可丢弃、可重建（docs/adr/0006）。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone

from memory_agent import _bootstrap
from memory_agent.memory.entries import Entry
from memory_agent.settings import COLLECTION_NAME, INDEX_DB_PATH, MANIFEST_PATH, MAX_ENTRY_CHARS

MANIFEST_VERSION = 1


class IndexNotBuiltError(RuntimeError):
    """索引尚未构建。"""


class MemoryIndex:
    def __init__(self, store=None, manifest_path: str | None = None,
                 db_path: str | None = None):
        self._store = store
        self._db_path = db_path or INDEX_DB_PATH
        self._manifest_path = manifest_path or MANIFEST_PATH
        self._entries: dict[str, dict] = {}
        self._built_at: str | None = None
        self._store_lock = threading.Lock()
        self._load_manifest()

    @property
    def store(self):
        if self._store is None:
            with self._store_lock:
                if self._store is None:
                    _bootstrap.ensure_ragcore_on_path()
                    from services.vector_store_service import VectorStoreService
                    self._store = VectorStoreService(
                        collection_name=COLLECTION_NAME, db_path=self._db_path)
        return self._store

    @property
    def is_built(self) -> bool:
        return bool(self._entries)

    def rebuild(self, entries: list[Entry]) -> dict:
        """从 Markdown 全量重建：清空集合 -> 逐条目写入 -> 落 manifest。"""
        store = self.store
        store.clear_all_documents()

        texts, metas = [], []
        for entry in entries:
            payload = entry.to_payload(MAX_ENTRY_CHARS)
            texts.append(payload.pop("text"))
            metas.append(payload)
        if texts:
            store.add_documents(texts, metadata_list=metas)

        self._entries = {entry.id: entry.to_manifest() for entry in entries}
        self._built_at = datetime.now(timezone.utc).isoformat()
        self._save_manifest()
        return {
            "entries": len(entries),
            "writable": sum(1 for e in entries if e.writable),
            "readonly": sum(1 for e in entries if not e.writable),
            "built_at": self._built_at,
        }

    def search(self, query: str, k: int = 5, writable_only: bool = False) -> list[dict]:
        """条目级语义检索。score 为余弦相似度（越大越相关）。

        writable_only 的过滤在向量库侧执行（否则 top-k 之后再筛会欠填）。
        """
        if not query or not query.strip():
            raise ValueError("query 不能为空")
        payload_filter = {"writable": True} if writable_only else None
        result = self.store.search_documents(query, k=k, payload_filter=payload_filter)
        documents = result["documents"][0]
        metadatas = result["metadatas"][0]
        scores = result["distances"][0]

        hits: list[dict] = []
        for text, meta, score in zip(documents, metadatas, scores):
            hits.append({
                "id": meta.get("entry_id"),
                "title": meta.get("title"),
                "source": meta.get("source"),
                "writable": bool(meta.get("writable")),
                "type": meta.get("type"),
                "tags": meta.get("tags") or [],
                "status": meta.get("status"),
                "score": float(score),
                "snippet": " ".join(text.split())[:240],
            })
        hits.sort(key=lambda hit: hit["score"], reverse=True)
        return hits

    def get(self, entry_id: str) -> dict:
        """按 id 读回真实 Markdown 内容（真相源是文件，不是索引）。"""
        meta = self._entries.get(entry_id)
        if meta is None:
            raise KeyError(entry_id)
        path = meta["path"]
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()
        return {"id": entry_id, "content": content, **meta}

    def stats(self) -> dict:
        return {
            "entries": len(self._entries),
            "writable": sum(1 for m in self._entries.values() if m["writable"]),
            "readonly": sum(1 for m in self._entries.values() if not m["writable"]),
            "built_at": self._built_at,
        }

    def _load_manifest(self) -> None:
        if not os.path.isfile(self._manifest_path):
            return
        try:
            with open(self._manifest_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return
        self._entries = data.get("entries", {})
        self._built_at = data.get("built_at")

    def _save_manifest(self) -> None:
        os.makedirs(os.path.dirname(self._manifest_path), exist_ok=True)
        data = {
            "version": MANIFEST_VERSION,
            "built_at": self._built_at,
            "entries": self._entries,
        }
        directory = os.path.dirname(self._manifest_path)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".tmp", dir=directory, delete=False
        ) as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            temp_path = handle.name
        os.replace(temp_path, self._manifest_path)
