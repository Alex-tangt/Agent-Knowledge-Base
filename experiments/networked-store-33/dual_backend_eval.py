"""#33 双后端对照评测：**同一确定性评测集**在本地后端与共享后端上跑。

主证据（同机制、跨部署）：
- **local-path**：显式本地 store（`path=`，Qdrant local mode），hybrid 集合 —
  从语料现建（BGE-M3 一次），保证两后端喂**同一份点**。
- **shared-url**：把上一步的**全部点（dense+sparse 向量 + payload）逐字复制**到自建
  Qdrant server 的集合，再用 `url=` 跑同一评测集。
  两后端因此应当**逐位相同**（`run_hash` 相同 = 排名完全相同），直接证明
  "同一评测集在两后端上可比"。

参考（平面各自原生，ADR-0019 D5/D6）：**production** = 生产指针索引 gen-2（本地平面
当前形态：dense + 策略层关键词），与上面两条不同语料版本，仅作参考、不与 hybrid 逐位比。

重活只跑一次：语料嵌入只在 local 建库时发生；shared 靠**复制点**而不是重新嵌入。
用法：
    $env:MEMORY_STORE_URL = "http://127.0.0.1:6333"
    $env:MEMORY_READONLY_REPOS_CONFIG = "<主树>/memory_agent/readonly_repos.json"
    <repo>/venv/Scripts/python.exe experiments/networked-store-33/dual_backend_eval.py --rebuild
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

LOCAL_COLLECTION = "memory_entries"


def _run_eval(extra: list[str]) -> None:
    cmd = [sys.executable, "-m", "memory_agent.eval.retrieval_eval", *extra]
    print("+", " ".join(cmd), file=sys.stderr)
    proc = subprocess.run(cmd, cwd=REPO_ROOT, env=os.environ.copy())
    if proc.returncode != 0:
        raise SystemExit(f"retrieval_eval 失败（exit {proc.returncode}）：{extra}")


def copy_points(local_dir: str, url: str, src_collection: str, dst_collection: str) -> int:
    """把本地集合的全部点（含命名 dense + sparse 向量与 payload）复制到 server 集合。"""
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointStruct

    src = QdrantClient(path=local_dir)
    dst = QdrantClient(url=url)
    try:
        info = src.get_collection(src_collection)
        if dst.collection_exists(dst_collection):
            dst.delete_collection(dst_collection)
        dst.create_collection(
            dst_collection,
            vectors_config=info.config.params.vectors,
            sparse_vectors_config=info.config.params.sparse_vectors,
        )
        total, offset = 0, None
        while True:
            points, offset = src.scroll(src_collection, limit=256, offset=offset,
                                        with_vectors=True, with_payload=True)
            if points:
                dst.upsert(dst_collection, points=[
                    PointStruct(id=p.id, vector=p.vector, payload=p.payload) for p in points])
                total += len(points)
            if offset is None:
                break
        return total
    finally:
        src.close()
        dst.close()


def _load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _row(label: str, report: dict, latency: dict | None) -> dict:
    agg = report["aggregate"]
    return {
        "backend": label,
        "queries": agg["queries_total"],
        "recall@1": agg["recall"]["1"],
        "recall@5": agg["recall"]["5"],
        "nDCG@10": agg["ndcg@10"],
        "MRR": agg["mrr"],
        "misses": len(agg["misses"]),
        "run_hash": report["meta"]["run_hash"],
        "mean_s": (latency or {}).get("mean"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("MEMORY_STORE_URL", "http://127.0.0.1:6333"))
    ap.add_argument("--collection", default="memory_eval_33")
    ap.add_argument("--workdir", default=os.path.join(
        os.environ.get("TEMP", "/tmp"), "networked-store-33"))
    ap.add_argument("--index-dir", default=os.path.join(
        os.path.dirname(REPO_ROOT), "Agent-Knowledge-Base", "memory_agent", "vector_db"),
        help="生产指针索引目录（默认主树 vector_db）")
    ap.add_argument("--eval-set", default=os.path.join(
        REPO_ROOT, "memory_agent", "eval", "retrieval_eval_set.json"))
    ap.add_argument("--mode", default="hybrid")
    ap.add_argument("--rebuild", action="store_true", help="（重新）从语料建本地 hybrid 库")
    ap.add_argument("--skip-production", action="store_true")
    ap.add_argument("--out", default=os.path.join(HERE, "eval_results.json"))
    args = ap.parse_args(argv)

    os.makedirs(args.workdir, exist_ok=True)
    local_dir = os.path.join(args.workdir, "local")
    manifest = os.path.join(args.workdir, "manifest.json")
    local_out = os.path.join(args.workdir, "local_result.json")
    shared_out = os.path.join(args.workdir, "shared_result.json")
    prod_out = os.path.join(args.workdir, "production_result.json")

    common = ["--eval-set", args.eval_set, "--mode", args.mode,
              "--k", "1", "3", "5", "10"]
    built = os.path.isfile(os.path.join(local_dir, "meta.json")) or os.path.isdir(local_dir)

    if args.rebuild or not built:
        _run_eval([*common, "--store-dir", local_dir, "--manifest", manifest,
                   "--rebuild", "--out", local_out])
    else:
        _run_eval([*common, "--store-dir", local_dir, "--manifest", manifest, "--out", local_out])

    copied = copy_points(local_dir, args.url, LOCAL_COLLECTION, args.collection)
    print(f"copied {copied} points to {args.url}/{args.collection}", file=sys.stderr)

    _run_eval([*common, "--store-url", args.url, "--collection", args.collection,
               "--manifest", manifest, "--out", shared_out])

    rows = []
    local_report = _load(local_out)
    shared_report = _load(shared_out)
    rows.append(_row("local-path (hybrid)", local_report, local_report["meta"].get("latency_s")))
    rows.append(_row("shared-url (hybrid)", shared_report, shared_report["meta"].get("latency_s")))

    if not args.skip_production:
        env = os.environ.copy()
        env["MEMORY_INDEX_DIR"] = args.index_dir
        cmd = [sys.executable, "-m", "memory_agent.eval.retrieval_eval",
               *common, "--out", prod_out]
        print("+", " ".join(cmd), file=sys.stderr)
        proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env)
        if proc.returncode == 0:
            prod_report = _load(prod_out)
            rows.append(_row("production gen-2 (dense+kw)", prod_report,
                             prod_report["meta"].get("latency_s")))

    result = {
        "url": args.url,
        "collection": args.collection,
        "mode": args.mode,
        "copied_points": copied,
        "eval_set": args.eval_set,
        "rows": rows,
        "local_equals_shared": rows[0]["run_hash"] == rows[1]["run_hash"],
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
