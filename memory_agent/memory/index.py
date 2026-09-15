"""派生索引：条目级向量索引 + manifest（可从 Markdown 全量重建）。

- 索引载体：Qdrant local mode，落在**当前代**目录 `INDEX_DIR/<gen>/qdrant`。
- manifest：id -> 条目元数据 + 文件路径 + 内容 hash，供 memory_get 读回真相源，
  也是增量刷新时「未变跳过」的依据。
- 代目录 + 指针（layout.py）：全量重建在新代里建好后原子切换指针；搜索只认指针。
- 索引与 manifest 都是派生物，可丢弃、可重建（docs/adr/0006、0011）。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone

from memory_agent.memory.entries import Entry, point_id_for
from memory_agent.memory.errors import IndexConsistencyError, IndexNotBuiltError
from memory_agent.memory.layout import IndexLayout
from memory_agent.memory.locks import INDEX_LOCK, locked
from memory_agent.memory.ports import DEFAULT_CLASSIFICATION, DEFAULT_RESIDENCY
from memory_agent.memory.store import open_store
from memory_agent.settings import MAX_ENTRY_CHARS

MANIFEST_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _provenance(store) -> dict:
    """命中的来源标注：存储平面 + 网关绑定的租户（#23 / ADR-0018）。

    单 store 时对所有命中相同；联邦召回时由多 store 聚合各自标注。
    """
    return {"plane": getattr(store, "plane", None), "tenant": getattr(store, "tenant", None)}


def upsert_entries(store, entries: list[Entry], max_chars: int = MAX_ENTRY_CHARS) -> int:
    """把条目嵌入并 upsert 到 store（点 id 由 entry_id 决定，重复写入即覆盖）。"""
    if not entries:
        return 0
    texts, metas, ids = [], [], []
    for entry in entries:
        payload = entry.to_payload(max_chars)
        texts.append(payload.pop("text"))
        metas.append(payload)
        ids.append(point_id_for(entry.id))
    store.add(texts, metadata_list=metas, ids=ids)
    return len(texts)


class MemoryIndex:
    def __init__(self, store=None, manifest_path: str | None = None,
                 db_path: str | None = None, layout: IndexLayout | None = None,
                 entry_loader=None, retriever=None, reranker=None,
                 retriever_factory=None, store_factory=None):
        self._store = store
        self._layout = layout or IndexLayout()
        self._store_factory = store_factory or open_store
        self._entry_loader = entry_loader
        self._explicit = manifest_path is not None or db_path is not None
        self._explicit_manifest = manifest_path
        self._explicit_db = db_path
        self._gen: str | None = None
        self._manifest_path: str | None = None
        self._db_path: str | None = None
        self._entries: dict[str, dict] = {}
        self._built_at: str | None = None
        self._store_lock = threading.Lock()
        self._retriever = retriever
        self._explicit_retriever = retriever is not None
        self._retriever_factory = retriever_factory
        self._injected_reranker = reranker
        self._sync()

    # ------------------------------------------------------------- lifecycle

    def _sync(self) -> None:
        """把状态对齐到指针指向的当前代（指针变了就换 manifest / Qdrant 路径）。

        显式路径模式（测试注入 store + manifest_path）不参与指针解析。
        """
        if self._explicit:
            if self._manifest_path != self._explicit_manifest:
                self._manifest_path = self._explicit_manifest
                self._db_path = self._explicit_db
                self._entries = {}
                self._built_at = None
                if self._manifest_path:
                    self._load_manifest()
            return

        gen = self._layout.read_pointer()
        if gen == self._gen and (gen is None or self._manifest_path):
            return
        self._gen = gen
        self._entries = {}
        self._built_at = None
        self._store = None  # 新一代 = 新 Qdrant 目录，旧句柄作废
        if not self._explicit_retriever:
            self._retriever = None  # 检索器持有旧 store，一并作废
        if gen is None:
            self._manifest_path = None
            self._db_path = None
        else:
            self._manifest_path = self._layout.manifest_path(gen)
            self._db_path = self._layout.db_path(gen)
            self._load_manifest()

    @property
    def store(self):
        """当前代的存储（`VectorStore` 端口）；只经工厂构造，不 import 具体实现。"""
        self._sync()
        if self._store is None:
            with self._store_lock:
                if self._store is None:
                    if not self._db_path:
                        raise IndexNotBuiltError(
                            "记忆索引尚未构建：请先运行 "
                            "venv\\Scripts\\python.exe memory_agent/build_index.py"
                        )
                    self._store = self._store_factory(self._db_path)
        return self._store

    @property
    def retriever(self):
        """记忆检索接缝（策略召回 + 可选重排），跟随当前代的 store。"""
        if self._retriever is None:
            if self._retriever_factory is not None:
                self._retriever = self._retriever_factory(self.store)
                return self._retriever
            from memory_agent.memory.retrieval import MemoryRetriever, default_reranker_factory
            from memory_agent.settings import RERANK_ENABLED
            factory = default_reranker_factory if (
                self._injected_reranker is None and RERANK_ENABLED) else None
            self._retriever = MemoryRetriever(
                self.store, reranker=self._injected_reranker, reranker_factory=factory)
        return self._retriever

    @property
    def gen(self) -> str | None:
        self._sync()
        return self._gen

    @property
    def is_built(self) -> bool:
        self._sync()
        return bool(self._entries)

    # ------------------------------------------------------------ full build

    @locked(INDEX_LOCK)
    def rebuild(self, entries: list[Entry]) -> dict:
        """把给定条目全量写入**当前显式路径**（清空 -> 逐条写 -> 落 manifest）。

        这是单代/测试用的低级入口；生产的全量重建走 `Reindexer`（新代 + 原子切指针）。
        """
        self._sync()
        if self._manifest_path is None:
            raise IndexNotBuiltError(
                "rebuild 需要显式 manifest_path；生产全量重建请用 Reindexer"
            )
        store = self.store
        store.clear()
        upsert_entries(store, entries)

        self._entries = {entry.id: entry.to_manifest() for entry in entries}
        self._built_at = _now()
        self._save_manifest()
        return {
            "entries": len(entries),
            "writable": sum(1 for e in entries if e.writable),
            "readonly": sum(1 for e in entries if not e.writable),
            "built_at": self._built_at,
        }

    # -------------------------------------------------------------- refresh

    @locked(INDEX_LOCK)
    def refresh(self, entries: list[Entry] | None = None) -> dict:
        """按条目增量重建当前代：hash 未变跳过、变更重嵌、消失的条目清点。

        只对**受影响条目**做嵌入；条目删除 / 改名留下的孤儿点按稳定点 id 删除。
        未变条目的元数据沿用 manifest（hash 相同 = 内容相同）。
        """
        self._sync()
        if self._manifest_path is None:
            raise IndexNotBuiltError(
                "索引尚未构建：请先运行 "
                "venv\\Scripts\\python.exe memory_agent/build_index.py"
            )
        corpus = entries if entries is not None else self._load_entries()
        fresh = {entry.id: entry for entry in corpus}

        manifest = dict(self._entries)
        to_embed: list[Entry] = []
        added = updated = 0
        for entry_id, entry in fresh.items():
            old = manifest.get(entry_id)
            if old is None:
                added += 1
                to_embed.append(entry)
            elif old.get("hash") != entry.content_hash:
                updated += 1
                to_embed.append(entry)
        skipped = len(fresh) - added - updated
        removed = [entry_id for entry_id in manifest if entry_id not in fresh]

        if to_embed:
            upsert_entries(self.store, to_embed)
            for entry in to_embed:
                manifest[entry.id] = entry.to_manifest()
        if removed:
            self.store.delete([point_id_for(i) for i in removed])
            for entry_id in removed:
                manifest.pop(entry_id, None)

        self._entries = manifest
        if to_embed or removed:
            self._built_at = _now()
            self._save_manifest()
        return {
            "entries": len(manifest),
            "added": added,
            "updated": updated,
            "skipped": skipped,
            "removed": len(removed),
            "embedded": len(to_embed),
        }

    # ------------------------------------------------------------------ read

    @locked(INDEX_LOCK)
    def search(self, query: str, k: int = 5, writable_only: bool = False) -> list[dict]:
        """条目级语义检索。score 为余弦相似度（越大越相关）。

        writable_only 的过滤在向量库侧执行（否则 top-k 之后再筛会欠填）。
        检索前核对自洽性：manifest 条数 ≠ 集合点数 → 显式报错，不静默返回空。
        每条命中带 `classification` / `residency`（payload 镜像）与 `provenance`
        （来源平面 / 租户，issue #23）。
        """
        if not query or not query.strip():
            raise ValueError("query 不能为空")
        store = self.store
        self._ensure_consistent()
        payload_filter = {"writable": True} if writable_only else None

        hits: list[dict] = []
        for score, text, meta in self.retriever.retrieve(
                query, k=k, payload_filter=payload_filter):
            hits.append({
                "id": meta.get("entry_id"),
                "title": meta.get("title"),
                "source": meta.get("source"),
                "writable": bool(meta.get("writable")),
                "type": meta.get("type"),
                "tags": meta.get("tags") or [],
                "status": meta.get("status"),
                "classification": meta.get("classification") or DEFAULT_CLASSIFICATION,
                "residency": meta.get("residency") or DEFAULT_RESIDENCY,
                "provenance": _provenance(store),
                "score": float(score),
                "snippet": " ".join(text.split())[:240],
            })
        hits.sort(key=lambda hit: hit["score"], reverse=True)
        return hits

    @locked(INDEX_LOCK)
    def get(self, entry_id: str) -> dict:
        """按 id 读回真实 Markdown 内容（真相源是文件，不是索引）。"""
        self._sync()
        meta = self._entries.get(entry_id)
        if meta is None:
            raise KeyError(entry_id)
        path = meta["path"]
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()
        return {"id": entry_id, "content": content, **meta}

    def known_ids(self) -> list[str]:
        """manifest 里的全部条目 id（评测集核对用，不读文件）。"""
        self._sync()
        return list(self._entries)

    @locked(INDEX_LOCK)
    def stats(self) -> dict:
        self._sync()
        return {
            "entries": len(self._entries),
            "writable": sum(1 for m in self._entries.values() if m["writable"]),
            "readonly": sum(1 for m in self._entries.values() if not m["writable"]),
            "built_at": self._built_at,
        }

    @locked(INDEX_LOCK)
    def status(self) -> dict:
        """当前代 / 条数 / 点数 / 是否自洽（供 memory_index_status）。"""
        self._sync()
        built = bool(self._entries)
        info = {
            "built": built,
            "gen": self._gen,
            "entries": len(self._entries),
            "built_at": self._built_at,
        }
        if self._gen:
            info["path"] = self._layout.gen_dir(self._gen)
        if built:
            points = self.store.count()
            info["points"] = points
            info["consistent"] = points == len(self._entries)
        return info

    # ------------------------------------------------------------- internals

    def _ensure_consistent(self) -> None:
        self._sync()
        if not self._entries:
            return
        points = self.store.count()
        if points != len(self._entries):
            raise IndexConsistencyError(
                f"索引不自洽：manifest {len(self._entries)} 条 != 集合 {points} 点。"
                "请以 memory_reindex(cursor=None) 全量重建（旧代仍在服务，Markdown 未丢）。"
            )

    def _load_entries(self) -> list[Entry]:
        if self._entry_loader is not None:
            return self._entry_loader()
        from memory_agent.corpus.loader import load_corpus
        return load_corpus()

    def _load_manifest(self) -> None:
        if not self._manifest_path or not os.path.isfile(self._manifest_path):
            return
        try:
            with open(self._manifest_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return
        self._entries = data.get("entries", {})
        self._built_at = data.get("built_at")

    def _save_manifest(self) -> None:
        assert self._manifest_path is not None
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
