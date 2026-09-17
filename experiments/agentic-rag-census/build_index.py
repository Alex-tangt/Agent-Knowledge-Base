"""importer（issue #47 第 1 步）：609 篇语料 → **独立 store / collection**。

两个变体（`--variant`）：

- `base`：生产默认口径——走 `MemoryIndex(...).rebuild(entries)`，单条送嵌截断
  `MAX_ENTRY_CHARS=6000`（`memory_agent/settings.py`），并落 manifest。
- `full`：敏感性对照——`upsert_entries(store, entries, max_chars=30000)`，把整篇
  正文送嵌（BGE-M3 自身 max_seq_length=8192 token 仍是硬上限），不落 manifest
  （只作 frozen 检索，不需要真相源读回）。

**只写实验目录**（`store/<variant>/`），绝不指向生产索引 / 真实 KB / 真实 daemon。

    python experiments/agentic-rag-census/build_index.py --variant base
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from memory_agent import _bootstrap  # noqa: E402

_bootstrap.configure_stderr_logging()
_bootstrap.configure_hf_offline()

from memory_agent.memory.index import MemoryIndex, upsert_entries  # noqa: E402
from memory_agent.memory.store import open_store  # noqa: E402

import multihop as mh  # noqa: E402

VARIANTS = {
    # variant: (store 子目录, 送嵌截断)
    "base": ("base", None),
    "full": ("full", 30000),
}


def variant_paths(variant: str) -> tuple[str, str]:
    sub, _ = VARIANTS[variant]
    root = os.path.join(mh.STORE_DIR, sub)
    return os.path.join(root, "qdrant"), os.path.join(root, "manifest.json")


def build(variant: str = "base", force: bool = False, limit: int | None = None) -> dict:
    db_path, manifest_path = variant_paths(variant)
    max_chars = VARIANTS[variant][1]
    if (not force) and os.path.isdir(db_path) and (
            max_chars is not None or os.path.isfile(manifest_path)):
        return {"variant": variant, "skipped": True, "db_path": db_path}

    eval_set, entries = mh.ensure_prepared()
    if limit is not None:
        entries = entries[:limit]
    started = time.time()
    if max_chars is None:
        index = MemoryIndex(
            store=open_store(db_path=db_path, collection_name=mh.COLLECTION, hybrid=False),
            manifest_path=manifest_path,
        )
        stats = index.rebuild(entries)
        index.store.close()
    else:
        store = open_store(db_path=db_path, collection_name=mh.COLLECTION, hybrid=False)
        store.clear()
        stats = {"entries": upsert_entries(store, entries, max_chars=max_chars)}
        store.close()
    return {
        "variant": variant, "skipped": False, "db_path": db_path,
        "manifest": manifest_path if max_chars is None else None,
        "max_chars": max_chars if max_chars is not None else "MAX_ENTRY_CHARS(6000)",
        "entries": stats.get("entries"), "elapsed_s": round(time.time() - started, 2),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="MultiHop-RAG independent store importer")
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="base")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="只灌前 N 篇（冒烟用）")
    args = parser.parse_args(argv)

    result = build(args.variant, force=args.force, limit=args.limit)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
