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

# 「已退役」状态（#42 / ADR-0025 D19）：被 supersede / archive 后不再参与默认读视图。
# 语义是**排除已退役**，不是「只要 current」——只读语料条目常无 `status`（None），
# 若按白名单筛会把它误伤。故只剔除明确退役的两个值，`current` / `draft` / `None` 都保留。
RETIRED_STATUSES = frozenset({"superseded", "archived"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _provenance(store) -> dict:
    """命中的来源标注：存储平面 + 网关绑定的租户（#23 / ADR-0018）。

    单 store 时对所有命中相同；联邦召回时由多 store 聚合各自标注。
    """
    return {"plane": getattr(store, "plane", None), "tenant": getattr(store, "tenant", None)}


def _fingerprint_of_entries(entries: list[Entry]) -> dict[str, list[int]]:
    """从条目自身的 stat 派生指纹（测试注入 entry_loader 时用）。"""
    out: dict[str, list[int]] = {}
    for entry in entries:
        if entry.mtime_ns is None or entry.size is None:
            continue
        out[os.path.normcase(entry.path)] = [entry.mtime_ns, entry.size]
    return out


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
                 retriever_factory=None, store_factory=None, fingerprint_provider=None):
        self._store = store
        self._layout = layout or IndexLayout()
        self._store_factory = store_factory or open_store
        self._entry_loader = entry_loader
        self._fingerprint_provider = fingerprint_provider
        self._explicit = manifest_path is not None or db_path is not None
        self._explicit_manifest = manifest_path
        self._explicit_db = db_path
        self._gen: str | None = None
        self._manifest_path: str | None = None
        self._db_path: str | None = None
        self._entries: dict[str, dict] = {}
        self._manifest_fingerprint: dict[str, list[int]] | None = None
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
                self._manifest_fingerprint = None
                self._built_at = None
                if self._manifest_path:
                    self._load_manifest()
            return

        gen = self._layout.read_pointer()
        if gen == self._gen and (gen is None or self._manifest_path):
            return
        self._gen = gen
        self._entries = {}
        self._manifest_fingerprint = None
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
        self._manifest_fingerprint = _fingerprint_of_entries(entries)
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
    def refresh(self, entries: list[Entry] | None = None, *,
                complete: bool | None = None,
                fingerprint: dict[str, list[int]] | None = None) -> dict:
        """按条目增量重建当前代：hash 未变跳过、变更重嵌、消失的条目清点。

        只对**受影响条目**做嵌入；条目删除 / 改名留下的孤儿点按稳定点 id 删除。
        未变条目的元数据沿用 manifest（hash 相同 = 内容相同）。

        孤儿安全（#36 / ADR-0014）：`complete=False`（来源配置读不出 / 有来源根不可达）
        时**不删**任何 manifest 条目，只报告 `deferred_removed`——避免把「暂时够不到」
        的语料当孤儿误删。
        """
        self._sync()
        if self._manifest_path is None:
            raise IndexNotBuiltError(
                "索引尚未构建：请先运行 "
                "venv\\Scripts\\python.exe memory_agent/build_index.py"
            )
        if entries is None:
            # 显式路径（测试注入）不参与运行时语料扫描——指纹从装载到的条目派生。
            if not self._explicit and (fingerprint is None or complete is None):
                scan_fp, scan_complete = self._scan()
                if fingerprint is None:
                    fingerprint = scan_fp
                if complete is None:
                    complete = scan_complete
            corpus = self._load_entries()
        else:
            corpus = entries
        if fingerprint is None:
            fingerprint = _fingerprint_of_entries(corpus)
        if complete is None:
            complete = True
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
        missing = [entry_id for entry_id in manifest if entry_id not in fresh]
        removed = missing if complete else []

        if to_embed:
            upsert_entries(self.store, to_embed)
            for entry in to_embed:
                manifest[entry.id] = entry.to_manifest()
        if removed:
            self.store.delete([point_id_for(i) for i in removed])
            for entry_id in removed:
                manifest.pop(entry_id, None)

        self._entries = manifest
        changed = bool(to_embed or removed)
        if changed or fingerprint != self._manifest_fingerprint:
            self._manifest_fingerprint = fingerprint
            if changed:
                self._built_at = _now()
            self._save_manifest()
        return {
            "entries": len(manifest),
            "added": added,
            "updated": updated,
            "skipped": skipped,
            "removed": len(removed),
            "deferred_removed": len(missing) - len(removed),
            "embedded": len(to_embed),
            "complete": complete,
        }

    # ------------------------------------------------------------------ read

    @locked(INDEX_LOCK)
    def search(self, query: str, k: int = 5, writable_only: bool = False,
               payload_filter: dict | None = None,
               exclude_retired: bool = False) -> list[dict]:
        """条目级语义检索。score 为余弦相似度（越大越相关）。

        过滤在向量库侧执行（否则 top-k 之后再筛会欠填）。`payload_filter` 由网关
        按身份 entitlement 构造（**只可收窄**，见 `memory_agent.gateway.authz`）；
        `writable_only` 与它合并。
        检索前核对自洽性：manifest 条数 ≠ 集合点数 → 显式报错，不静默返回空。
        每条命中带 `classification` / `residency` / `tenant`（payload 镜像）与
        `provenance`（来源平面 / 租户，issue #23）。
        另带 `owner`（域所有者，读侧可见；#45 / ADR-0025 D19）——只读条目 = 来源 label。

        `exclude_retired`（#42 / ADR-0025 D19）：排除 `status ∈ {superseded, archived}`
        的条目，**保留 `current` / `draft` / 无 `status`**（只读语料常无 status，不能被
        误伤）。默认 `False`（不静默改行为），由 skill 指导 agent 显式开启。
        这是**视图 / 质量**策略而非授权边界：授权过滤仍下沉到 store，这里只对候选池
        做后置剔除，并**多取一些候选**（到召回池深度）以免 top-k 欠填。
        """
        if not query or not query.strip():
            raise ValueError("query 不能为空")
        self._maybe_refresh()
        store = self.store
        self._ensure_consistent()
        merged = dict(payload_filter or {})
        if writable_only:
            merged["writable"] = True

        # 开了退役过滤就多取候选（召回池深度），筛完再截回 k，避免 top-k 直接欠填。
        fetch_k = k
        if exclude_retired:
            fetch_k = max(k, getattr(self.retriever, "pool_size", k))

        hits: list[dict] = []
        for score, text, meta in self.retriever.retrieve(
                query, k=fetch_k, payload_filter=merged or None):
            if exclude_retired and meta.get("status") in RETIRED_STATUSES:
                continue
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
                "tenant": meta.get("tenant"),
                "owner": meta.get("owner"),
                "provenance": _provenance(store),
                "score": float(score),
                "snippet": " ".join(text.split())[:240],
            })
        hits.sort(key=lambda hit: hit["score"], reverse=True)
        return hits[:k]

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

    def _scan(self) -> tuple[dict[str, list[int]], bool]:
        """当前收录的廉价指纹（stat-only）；测试可注入 `fingerprint_provider`。"""
        if self._fingerprint_provider is not None:
            return self._fingerprint_provider()
        from memory_agent.corpus.loader import scan_fingerprint
        return scan_fingerprint()

    def _maybe_refresh(self) -> None:
        """查询时惰性刷新（#36 / ADR-0025 D9）：指纹不同才做增量重建。

        只在**生产模式**（非显式路径）下触发；显式路径（测试注入 store + manifest）
        不参与运行时语料扫描。
        """
        if self._explicit or self._manifest_path is None or not self._entries:
            return
        fingerprint, complete = self._scan()
        if fingerprint == (self._manifest_fingerprint or {}):
            return
        self.refresh(complete=complete, fingerprint=fingerprint)

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
        self._manifest_fingerprint = data.get("fingerprint")
        self._built_at = data.get("built_at")

    def _save_manifest(self) -> None:
        assert self._manifest_path is not None
        os.makedirs(os.path.dirname(self._manifest_path), exist_ok=True)
        data = {
            "version": MANIFEST_VERSION,
            "built_at": self._built_at,
            "fingerprint": self._manifest_fingerprint,
            "entries": self._entries,
        }
        directory = os.path.dirname(self._manifest_path)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".tmp", dir=directory, delete=False
        ) as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            temp_path = handle.name
        os.replace(temp_path, self._manifest_path)
