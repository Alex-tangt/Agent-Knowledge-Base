"""分块全量重建：代目录 + 指针切换（issue #13 / ADR-0011）。

为什么分块：MCP 客户端调用超时是几十秒量级，整轮 60 条嵌入 CPU 上要数分钟 → 单次
调用必超时。`memory_reindex(cursor=None, batch=16)` 每次只嵌入 batch 条，调用方拿
`cursor` 续调，`done=true` 时已原子切换指针。零后台线程、零 job 表（D3）。

原子性：新代在 `INDEX_DIR/<gen>/` 完整建好（qdrant/ + manifest.json），最后
`os.replace(指针)`。中途死掉只留一个未接管的代目录，指针仍指旧代——绝不出现
「空索引 + 陈旧 manifest」。完成前核对 manifest 条数 == 集合点数，不等则**报错不切换**。
"""
from __future__ import annotations

import json
import os
import tempfile

from memory_agent.memory.entries import Entry
from memory_agent.memory.errors import IndexConsistencyError
from memory_agent.memory.index import _now, upsert_entries
from memory_agent.memory.layout import IndexLayout
from memory_agent.memory.locks import INDEX_LOCK, locked
from memory_agent.memory.store import open_store
from memory_agent.settings import DEFAULT_REINDEX_BATCH


def _default_loader():
    from memory_agent.corpus.loader import load_corpus
    return load_corpus()


class Reindexer:
    def __init__(self, layout: IndexLayout | None = None, entry_loader=None,
                 store_factory=None):
        self._layout = layout or IndexLayout()
        self._load = entry_loader or _default_loader
        self._store_factory = store_factory or open_store

    def run_all(self, batch: int = DEFAULT_REINDEX_BATCH) -> dict:
        """把整轮重建跑完（CLI 用；服务端 MCP 用分块 cursor）。"""
        result = self.reindex(cursor=None, batch=batch)
        while not result["done"]:
            result = self.reindex(cursor=result["cursor"], batch=batch)
        return result

    @locked(INDEX_LOCK)
    def reindex(self, cursor: dict | None = None, batch: int = DEFAULT_REINDEX_BATCH) -> dict:
        batch = max(1, min(int(batch), 256))
        if cursor is None:
            return self._begin(batch)
        return self._advance(cursor, batch)

    # ------------------------------------------------------------- internals

    def _begin(self, batch: int) -> dict:
        entries = self._load()
        if not entries:
            raise IndexConsistencyError(
                "语料为空：拒绝重建（否则会切出一个空索引，静默搜不到任何东西）"
            )
        gen = self._layout.next_gen()
        os.makedirs(self._layout.gen_dir(gen), exist_ok=True)
        plan = [
            {"id": e.id, "path": e.path, "source": e.source, "writable": e.writable}
            for e in entries
        ]
        store = self._store_factory(self._layout.db_path(gen))
        self._write_json(self._layout.plan_path(gen), plan)
        self._write_manifest(gen, {"version": 1, "built_at": None, "entries": {}})
        return self._process(gen, plan, 0, batch, store)

    def _advance(self, cursor: dict, batch: int) -> dict:
        gen = cursor.get("gen")
        offset = int(cursor.get("offset", 0))
        plan = self._read_json(self._layout.plan_path(gen)) if gen else None
        if not plan:
            raise IndexConsistencyError(
                f"重建计划不存在（cursor 已过期或代 {gen!r} 不存在）："
                "请以 cursor=None 重新开始"
            )
        store = self._store_factory(self._layout.db_path(gen))
        return self._process(gen, plan, offset, batch, store)

    def _process(self, gen: str, plan: list[dict], offset: int, batch: int, store) -> dict:
        chunk = plan[offset:offset + batch]
        entries: list[Entry] = []
        skipped: list[str] = []
        for item in chunk:
            try:
                entries.append(Entry.from_file(
                    item["path"], source=item["source"],
                    writable=item["writable"], entry_id=item["id"],
                ))
            except FileNotFoundError:
                skipped.append(item["id"])  # 计划后被删；不嵌入、不计数

        upsert_entries(store, entries)
        manifest = self._read_manifest(gen)
        for entry in entries:
            manifest["entries"][entry.id] = entry.to_manifest()

        next_offset = offset + len(chunk)
        done = next_offset >= len(plan)
        result = {
            "gen": gen,
            "total": len(plan),
            "processed": len(entries),
            "skipped": skipped,
            "done": done,
        }
        if not done:
            self._write_manifest(gen, manifest)
            result["cursor"] = {"gen": gen, "offset": next_offset}
            return result

        points = store.count()
        counted = len(manifest["entries"])
        if points != counted:
            raise IndexConsistencyError(
                f"重建未通过自洽核对：manifest {counted} 条 != 集合 {points} 点"
                f"（代 {gen} 未启用，旧代继续服务）"
            )
        manifest["built_at"] = _now()
        self._write_manifest(gen, manifest)
        self._layout.write_pointer(gen)
        self._layout.prune()
        result.update({
            "cursor": None,
            "entries": counted,
            "writable": sum(1 for m in manifest["entries"].values() if m["writable"]),
            "readonly": sum(1 for m in manifest["entries"].values() if not m["writable"]),
            "built_at": manifest["built_at"],
        })
        return result

    def _write_json(self, path: str, data) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".tmp", dir=os.path.dirname(path), delete=False
        ) as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            temp_path = handle.name
        os.replace(temp_path, path)

    def _read_json(self, path: str):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

    def _write_manifest(self, gen: str, manifest: dict) -> None:
        self._write_json(self._layout.manifest_path(gen), manifest)

    def _read_manifest(self, gen: str) -> dict:
        data = self._read_json(self._layout.manifest_path(gen))
        if not isinstance(data, dict):
            return {"version": 1, "built_at": None, "entries": {}}
        data.setdefault("entries", {})
        data.setdefault("version", 1)
        data.setdefault("built_at", None)
        return data
