"""取条目正文的**单一来源**（生产一致性关键）。

⚠ 教训：`manifest[entry]["path"]` 是**原始 .md**（含 YAML frontmatter），而索引 payload 里存的是
**渲染后的正文**（frontmatter 剥离、标题 `# ...`）。两者前 512 字天差地别 —— 用错来源会让
"送排文本"和"信号编码文本"都不是生产看到的那份，监督信号直接失效。

本模块统一从 **Qdrant payload** 取正文（= 生产检索/重排实际使用的文本）。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def resolve_gen(index_dir: str, gen: str | None) -> str:
    if gen:
        return gen
    with open(os.path.join(index_dir, "CURRENT"), encoding="utf-8") as fh:
        return fh.read().strip()


def manifest_entries(index_dir: str, gen: str) -> dict:
    with open(os.path.join(index_dir, gen, "manifest.json"), encoding="utf-8") as fh:
        return json.load(fh)["entries"]


def load_texts(index_dir: str, gen: str, source: str = "qdrant") -> tuple[list[str], list[str]]:
    """返回 `(entry_ids, texts)`，顺序 = manifest 顺序。

    `source="qdrant"`（默认）→ 索引 payload 正文；`"file"` → 原始 .md（**仅供对照**）。
    """
    entries = manifest_entries(index_dir, gen)
    ids = list(entries.keys())
    if source == "file":
        texts = []
        for entry_id in ids:
            with open(entries[entry_id]["path"], encoding="utf-8") as fh:
                texts.append(fh.read())
        return ids, texts

    from memory_agent import settings
    from ragcore.services.vector_store_service import VectorStoreService

    service = VectorStoreService(collection_name=settings.COLLECTION_NAME,
                                 db_path=os.path.join(index_dir, gen, "qdrant"))
    try:
        with service._session() as client:          # noqa: SLF001（实验脚本，复用同一扫描路径）
            docs, metas = service._scroll_all(client)  # noqa: SLF001
    finally:
        service.close()
    by_id = {}
    for doc, meta in zip(docs, metas):
        entry_id = (meta or {}).get("entry_id")
        if entry_id:
            by_id[entry_id] = doc
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise RuntimeError(f"索引里缺 {len(missing)} 条（例：{missing[:3]}）——文本来源不一致")
    return ids, [by_id[i] for i in ids]
