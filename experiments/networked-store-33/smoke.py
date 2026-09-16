"""#33 真实运行 smoke：自建 Qdrant 服务上的网络化 store（共享平面）。

一次真实运行（不是 mock）：真实 BGE-M3 嵌入 + 真实 Qdrant server（docker，`url=`）。
断言对象 = **外部可观察行为**：端口契约 / store 原生 hybrid / tenant 过滤不泄漏 /
绑定租户不可放宽 / 共享域写路径只碰 DB（无 git / 代目录 / 指针）。

凭据只从进程环境 / `MEMORY_ENV_FILE` 读（`MEMORY_STORE_URL` / `MEMORY_STORE_API_KEY`），
证据里只记"是否设置"，**绝不落值**。

用法：
    $env:MEMORY_STORE_URL = "http://127.0.0.1:6333"
    <repo>/venv/Scripts/python.exe experiments/networked-store-33/smoke.py --entries 8
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from memory_agent import _bootstrap  # noqa: E402

_bootstrap.configure_stderr_logging()

from memory_agent.memory.entries import point_id_for  # noqa: E402
from memory_agent.memory.ports import PLANE_SHARED, VectorStore  # noqa: E402
from memory_agent.memory.shared_writer import SharedMemoryWriter  # noqa: E402
from memory_agent.memory.store import QdrantNetworkStore  # noqa: E402
from memory_agent.settings import load_env_file  # noqa: E402

COLLECTION = "memory_smoke_33"


def _load_entries(limit: int):
    from memory_agent.corpus.loader import load_corpus
    entries = [e for e in load_corpus() if (e.body or "").strip()]
    entries.sort(key=lambda e: e.id)
    return entries[:limit]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entries", type=int, default=8)
    ap.add_argument("--out", default=os.path.join(HERE, "smoke_result.json"))
    args = ap.parse_args(argv)

    load_env_file()
    url = os.environ.get("MEMORY_STORE_URL", "http://127.0.0.1:6333")
    result: dict = {
        "url": url,
        "api_key_set": bool(os.environ.get("MEMORY_STORE_API_KEY")),  # 只记是否设置
        "entries": args.entries,
        "checks": {},
    }

    store = QdrantNetworkStore(url=url, collection_name=COLLECTION)
    entries = _load_entries(args.entries)
    store.clear()

    # 1) 端口契约 + 真实嵌入写入（dense BGE-M3 + app 层 sparse）
    store.warmup()
    texts = [e.embedding_text(6000) for e in entries]
    ids = [point_id_for(e.id) for e in entries]
    metas = [{"entry_id": e.id, "title": e.title, "tenant": "org-a" if i % 2 else "org-b"}
             for i, e in enumerate(entries)]
    store.add(texts, metadata_list=metas, ids=ids)
    result["checks"]["port_contract"] = isinstance(store, VectorStore)
    result["checks"]["plane"] = store.plane == PLANE_SHARED
    result["checks"]["count_matches"] = store.count() == len(entries)

    # 2) store 原生 hybrid：用首条正文里的一个词查，应命中首条
    probe = entries[0].title or texts[0][:20]
    hits = store.search(probe, k=3)
    result["checks"]["hybrid_returns"] = len(hits["documents"][0]) > 0
    result["checks"]["hybrid_top_is_probe"] = (
        hits["metadatas"][0][0].get("entry_id") == entries[0].id if hits["metadatas"][0] else False)

    # 3) tenant 过滤不得泄漏（共享集合 + 行级过滤，ADR-0018 D2）
    only_a = store.search(probe, k=len(entries), payload_filter={"tenant": "org-a"})
    result["checks"]["tenant_filter_excludes_other"] = all(
        m["tenant"] == "org-a" for m in only_a["metadatas"][0])

    # 4) 绑定租户不可被调用方放宽
    bound = QdrantNetworkStore(url=url, collection_name=COLLECTION, tenant="org-a")
    widened = bound.search(probe, k=len(entries), payload_filter={"tenant": "org-b"})
    result["checks"]["bound_tenant_not_widenable"] = all(
        m["tenant"] == "org-a" for m in widened["metadatas"][0])

    # 5) dense 通道量纲 = 余弦（与本地平面可比，供按阈值操作）
    dense = store.search_dense(texts[0], k=1)
    result["checks"]["dense_cosine_self"] = round(dense["distances"][0][0], 4)

    # 6) 共享域写路径 = DB，无 git / 代目录 / 指针（D16）
    index_dir = os.path.join(HERE, "_smoke_index_dir")
    os.environ["MEMORY_INDEX_DIR"] = index_dir
    writer = SharedMemoryWriter(store, owner="smoke", tenant="org-a")
    added = writer.add(title="Smoke shared note", body="hello shared plane",
                       domain="topics", type="topic", tags=["smoke"], slug="smoke-shared-note")
    result["checks"]["shared_add_written"] = added["status"] == "written"
    result["checks"]["shared_add_is_db_mode"] = added["index"]["mode"] == "db"
    result["checks"]["no_pointer_written"] = not os.path.exists(index_dir)
    archived = writer.archive(entry_id="topics/smoke-shared-note", reason="smoke",
                              confirm=True)
    result["checks"]["shared_archive_confirm"] = archived["status"] == "written"

    store.clear()
    failures = [k for k, v in result["checks"].items()
                if v is False or (isinstance(v, (int, float)) and v < 0.99)]
    result["passed"] = not failures
    result["failures"] = failures

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"wrote {args.out}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
