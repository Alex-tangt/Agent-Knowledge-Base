"""one-shot census（issue #47 第 4 步）：单次 gold-evidence recall@k 曲线。

对独立 store 跑**全量**查询（含 `null_query`），产出：

- `recall@k`（k=1/5/10/20/50，另报 14 = 生产 `MEMORY_RETRIEVAL_POOL` 默认）——**条目级**：
  `|top-k ∩ 金标文章| / |金标文章|`（金标 = evidence `url` 对齐到的语料文章）。
- 分题型（inference / comparison / temporal）+ 分金标篇数（2/3/4）。
- **完整覆盖**（该题全部金标都在 top-k）与 misses。
- **覆盖问题 vs 排序问题**：金标在 top-50 都进不来 = 覆盖；进了 top-50 却不在 top-5 = 排序。
- `null_query`：只报 top-1 分数分布（ADR-0017，不校阈值）。
- `run_hash`：聚合 + 逐题排名的确定性指纹。

检索链路与生产一致：`MemoryIndex.search` → `MemoryRetriever` → `DefaultRetrievalStrategy`
（向量 + 关键词加法增强，`ragcore/strategies/default.py`），不叠加 store 原生 hybrid
（本地平面生产口径，ADR-0019 D4）。`pool_size=50` 只为让 recall@50 有意义——生产默认
仍是 14（同一次运行的排名里可读 recall@14）。

    python experiments/agentic-rag-census/run_census.py --runs base:hybrid,base:vector,full:hybrid
"""
from __future__ import annotations

import argparse
import hashlib
import json
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

from memory_agent.eval.metrics import ndcg_at_k, reciprocal_rank, recall_at_k  # noqa: E402
from memory_agent.memory.index import MemoryIndex  # noqa: E402
from memory_agent.memory.retrieval import MemoryRetriever  # noqa: E402
from memory_agent.memory.store import open_store  # noqa: E402

import multihop as mh  # noqa: E402
from build_index import VARIANTS, variant_paths  # noqa: E402

from ragcore.strategies.default import DefaultRetrievalStrategy  # noqa: E402

KS = (1, 5, 10, 20, 50)
PROD_POOL = 14  # MEMORY_RETRIEVAL_POOL 默认（docs/adr/0022）
QUESTION_TYPES = ("inference_query", "comparison_query", "temporal_query")
REPORT_JSON = os.path.join(mh.ARTIFACTS_DIR, "report.json")
PER_QUERY_JSON = os.path.join(mh.ARTIFACTS_DIR, "per_query_ids.json")
REPORT_MD = os.path.join(HERE, "report.md")


# ---------------------------------------------------------------- 检索接缝

def open_searcher(run: str, pool: int):
    """返回 `(search(query, k) -> (ids, scores), closer, backend_label)`。

    `base` 走 `MemoryIndex`（显式 manifest，`search` 带自洽核对）；`full` 无 manifest，
    直接走 `MemoryRetriever`（同一条检索链路，只是缺一致性核对）。
    """
    variant, mode = run.split(":", 1)
    if variant not in VARIANTS:
        raise SystemExit(f"未知 variant：{variant}")
    if mode not in ("hybrid", "vector"):
        raise SystemExit(f"未知 mode：{mode}")
    db_path, manifest_path = variant_paths(variant)
    store = open_store(db_path=db_path, collection_name=mh.COLLECTION, hybrid=False)
    retriever = MemoryRetriever(
        store,
        strategy=DefaultRetrievalStrategy(enable_keyword=(mode == "hybrid")),
        pool_size=pool,
    )

    if variant == "base":
        index = MemoryIndex(store=store, manifest_path=manifest_path,
                            retriever_factory=lambda _s: retriever)

        def search(query: str, k: int):
            hits = index.search(query, k=k)
            return [h["id"] for h in hits], [h["score"] for h in hits]

        return search, store.close, f"{variant}/{mode} (MemoryIndex + manifest)"

    def search(query: str, k: int):
        candidates = retriever.retrieve(query, k=k)
        return ([c[2].get("entry_id") for c in candidates],
                [float(c[0]) for c in candidates])

    return search, store.close, f"{variant}/{mode} (raw MemoryRetriever)"


# ---------------------------------------------------------------- 指标

def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def per_query_metrics(relevant: list[str], ranked: list[str]) -> dict:
    recall = {str(k): round(recall_at_k(ranked, relevant, k), 6) for k in KS}
    return {
        "recall": recall,
        "recall@14": round(recall_at_k(ranked, relevant, PROD_POOL), 6),
        "full_coverage": {str(k): recall[str(k)] >= 1.0 for k in KS},
        "ndcg@10": round(ndcg_at_k(ranked, relevant, 10), 6),
        "mrr": round(reciprocal_rank(ranked, relevant), 6),
        "coverage_missing@50": sorted(set(relevant) - set(ranked[:50])),
        "ranking_loss@5": sorted((set(relevant) & set(ranked[:50])) - set(ranked[:5])),
    }


def summarize(rows: list[dict]) -> dict:
    answerable = [r for r in rows if r["relevant"]]
    n = len(answerable)
    return {
        "count": n,
        "recall": {str(k): round(_mean(r["recall"][str(k)] for r in answerable), 6)
                   for k in KS},
        "recall@14": round(_mean(r["recall@14"] for r in answerable), 6),
        "full_coverage": {str(k): round(
            sum(1 for r in answerable if r["recall"][str(k)] >= 1.0) / n, 6)
            if n else 0.0 for k in KS},
        "ndcg@10": round(_mean(r["ndcg@10"] for r in answerable), 6),
        "mrr": round(_mean(r["mrr"] for r in answerable), 6),
    }


def run_signature(rows: list[dict]) -> dict:
    """聚合 + 逐题排名（id only）——确定性指纹的规范化输入。"""
    canonical = {
        "rows": [
            {"id": r["id"], "relevant": sorted(r["relevant"]), "ranked": r["ranked"]}
            for r in rows
        ]
    }
    return canonical


# ---------------------------------------------------------------- 单次运行

def window_breakdown(rows: list[dict], window_status: dict, k: int) -> dict:
    """按"该题该金标文章的 fact 是否在送嵌窗口内"切分 gold 槽的 recall@k（离线诊断）。"""
    buckets = {"all": [0, 0], "partial": [0, 0], "none": [0, 0]}
    for row in rows:
        if not row["relevant"]:
            continue
        topk = set(row["ranked"][:k])
        for gold in row["relevant"]:
            status = window_status.get((row["id"], gold), "none")
            buckets[status][1] += 1
            if gold in topk:
                buckets[status][0] += 1
    return {status: {"recalled": hit, "slots": total,
                     "recall": round(hit / total, 6) if total else None}
            for status, (hit, total) in buckets.items()}


def run_one(run: str, eval_set: dict, pool: int, limit: int | None,
            trace_path: str | None, window_status: dict | None = None) -> dict:
    queries = eval_set["queries"]
    if limit is not None:
        queries = queries[:limit]
    search, close, backend = open_searcher(run, pool)

    done: dict[str, dict] = {}
    if trace_path and os.path.isfile(trace_path):
        with open(trace_path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    record = json.loads(line)
                    done[record["id"]] = record
        print(f"[{run}] resume: {len(done)} 条", file=sys.stderr)

    handle = open(trace_path, "a", encoding="utf-8") if trace_path else None
    started = time.time()
    try:
        search("warmup", 1)
        for i, query in enumerate(queries, start=1):
            if query["id"] in done:
                continue
            t0 = time.time()
            ids, scores = search(query["query"], k=pool)
            record = {
                "id": query["id"],
                "question_type": query["question_type"],
                "kind": query["kind"],
                "evidence_count": query["evidence_count"],
                "relevant": list(query["relevant"]),
                "ranked": ids,
                "ranked_scores": scores,
                "elapsed_s": round(time.time() - t0, 4),
            }
            done[record["id"]] = record
            if handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
            if i % 100 == 0:
                print(f"[{run}] {i}/{len(queries)}", file=sys.stderr)
    finally:
        if handle:
            handle.close()
        close()

    rows = [done[q["id"]] for q in queries if q["id"] in done]
    elapsed = round(time.time() - started, 2)

    for row in rows:
        if row["kind"] == "no_answer" or not row["relevant"]:
            row.update({"recall": {}, "recall@14": 0.0, "full_coverage": {},
                        "ndcg@10": 0.0, "mrr": 0.0,
                        "coverage_missing@50": [], "ranking_loss@5": []})
        else:
            row.update(per_query_metrics(row["relevant"], row["ranked"]))

    answerable = [r for r in rows if r["relevant"]]
    by_type = {t: summarize([r for r in answerable if r["question_type"] == t])
               for t in QUESTION_TYPES}
    by_count = {str(n): summarize([r for r in answerable if r["evidence_count"] == n])
                for n in (2, 3, 4)}
    overall = summarize(answerable)

    null_rows = [r for r in rows if r["kind"] == "no_answer"]
    null_stats = {
        "count": len(null_rows),
        "top1_score": {
            "mean": round(_mean(r["ranked_scores"][0] for r in null_rows
                                if r["ranked_scores"]), 6),
            "max": round(max((r["ranked_scores"][0] for r in null_rows
                              if r["ranked_scores"]), default=0.0), 6),
        },
    }

    misses = {
        "queries_full@50": sum(1 for r in answerable if r["recall"][str(50)] >= 1.0),
        "queries_with_missing@50": sum(1 for r in answerable
                                       if r["coverage_missing@50"]),
        "queries_with_ranking_loss@5": sum(1 for r in answerable if r["ranking_loss@5"]),
        "coverage_missing_slots@50": sum(len(r["coverage_missing@50"]) for r in answerable),
        "ranking_loss_slots@5": sum(len(r["ranking_loss@5"]) for r in answerable),
        "rank_ceiling@5_from_pool50": round(
            _mean(r["recall"][str(50)] for r in answerable), 6),
        "recall@5": round(_mean(r["recall"][str(5)] for r in answerable), 6),
    }
    misses["ranking_headroom@5"] = round(
        misses["rank_ceiling@5_from_pool50"] - misses["recall@5"], 6)

    latency = sorted(r["elapsed_s"] for r in rows)
    result = {
        "run": run,
        "backend": backend,
        "pool": pool,
        "overall": overall,
        "by_question_type": by_type,
        "by_evidence_count": by_count,
        "misses": misses,
        "null_query": null_stats,
        "window_recall": (
            {"@5": window_breakdown(answerable, window_status, 5),
             "@50": window_breakdown(answerable, window_status, 50)}
            if window_status else None),
        "latency_s": {
            "mean": round(_mean(latency), 4),
            "p50": latency[len(latency) // 2] if latency else None,
            "p95": latency[min(int(len(latency) * 0.95), len(latency) - 1)]
            if latency else None,
            "max": latency[-1] if latency else None,
        },
        "elapsed_s": elapsed,
        "rows": rows,
    }
    return result


# ---------------------------------------------------------------- isolation

def _main_worktree() -> str:
    """主工作树路径（production 索引 / venv 所在；`git worktree list` 第一项）。"""
    import subprocess

    proc = subprocess.run(["git", "-C", REPO_ROOT, "worktree", "list", "--porcelain"],
                          capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            return line.split(" ", 1)[1].strip()
    return REPO_ROOT


_VOLATILE_SUFFIXES = (".log", ".pid", ".lock", ".tmp")


def _dir_signature(path: str) -> str | None:
    """索引内容签名（名字+大小+mtime）——证明生产索引未被写。

    排除运行时易变文件（`*.log` / `*.pid` / `*.lock` / `*.tmp`）：真实 daemon 常驻，
    它的日志与按操作持有的锁一直在动，与本实验无关。
    """
    if not os.path.isdir(path):
        return None
    parts = []
    for root, _dirs, files in os.walk(path):
        for name in sorted(files):
            if name.lower().endswith(_VOLATILE_SUFFIXES):
                continue
            full = os.path.join(root, name)
            try:
                stat = os.stat(full)
            except OSError:
                continue
            parts.append(f"{os.path.relpath(full, path)}:{stat.st_size}:{stat.st_mtime_ns}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def isolation_snapshot() -> dict:
    """真实 KB / 生产索引（主树）/ 真实 daemon 的只读快照（前后对比证明未触碰）。

    本实验只写 `experiments/agentic-rag-census/store/**`（显式 `db_path`），
    从不引用 `MEMORY_INDEX_DIR` 指针。
    """
    import subprocess

    kb = os.environ.get("AGENT_KB_DIR") or r"C:\Users\Tan\.config\opencode\knowledge"
    main_index_dir = os.environ.get("MEMORY_INDEX_DIR") or os.path.join(
        _main_worktree(), "memory_agent", "vector_db")
    snap = {"agent_kb_dir": kb, "prod_memory_index_dir": main_index_dir}
    if os.path.isdir(os.path.join(kb, ".git")):
        proc = subprocess.run(["git", "-C", kb, "status", "--porcelain"],
                              capture_output=True, text=True)
        snap["kb_status_sha"] = hashlib.sha256(proc.stdout.encode()).hexdigest()[:16]
    pointer = os.path.join(main_index_dir, "CURRENT")
    snap["prod_index_current"] = (open(pointer, encoding="utf-8").read().strip()
                                  if os.path.isfile(pointer) else None)
    snap["prod_index_dir_sig"] = _dir_signature(main_index_dir)
    try:
        import urllib.request

        with urllib.request.build_opener(
                urllib.request.ProxyHandler({})).open(
                "http://127.0.0.1:8765/health", timeout=1.0) as response:
            snap["daemon_health"] = response.status
    except Exception:  # noqa: BLE001 - daemon 未跑不阻断
        snap["daemon_health"] = None
    return snap


# ---------------------------------------------------------------- 报告

def _fmt(value) -> str:
    return f"{value:.4f}" if isinstance(value, float) else str(value)


def render_markdown(report: dict) -> str:
    lines = [
        "# MultiHop-RAG one-shot census（#47 Phase A 层 1）",
        "",
        f"- 数据：`{report['meta']['dataset']}`（{report['meta']['license']}），"
        f"语料 {report['meta']['corpus_articles']} 篇 / 查询 {report['meta']['queries_total']} 条"
        f"（可答 {report['meta']['queries_answerable']} + null {report['meta']['null_query']}）。",
        f"- 检索链路：`MemoryIndex.search` → `MemoryRetriever` → "
        f"`DefaultRetrievalStrategy`（向量 + 关键词）；pool={report['meta']['pool']}"
        f"（生产默认 14，另列 recall@14）；rerank 关。",
        f"- 单位：条目级（金标 = evidence `url` 对齐的语料文章）。",
        f"- k = {KS}。",
        "",
        "## recall@k（全部可答题）",
        "",
        "| run | " + " | ".join(f"recall@{k}" for k in KS)
        + " | recall@14 | nDCG@10 | MRR | 完整覆盖@50 |",
        "|" + "---|" * (len(KS) + 5),
    ]
    for name, run in report["runs"].items():
        agg = run["overall"]
        cells = [name] + [_fmt(agg["recall"][str(k)]) for k in KS]
        cells += [_fmt(agg["recall@14"]), _fmt(agg["ndcg@10"]), _fmt(agg["mrr"]),
                  _fmt(agg["full_coverage"]["50"])]
        lines.append("| " + " | ".join(cells) + " |")

    lines += ["", "## 分题型 recall@k", ""]
    for name, run in report["runs"].items():
        lines += [f"### {name}", "", "| 题型 | n | " + " | ".join(
            f"recall@{k}" for k in KS) + " | 完整覆盖@50 |",
            "|" + "---|" * (len(KS) + 3)]
        for qtype, agg in run["by_question_type"].items():
            cells = [qtype, str(agg["count"])] + [
                _fmt(agg["recall"][str(k)]) for k in KS]
            cells.append(_fmt(agg["full_coverage"]["50"]))
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")

    lines += ["## 分金标篇数（unique evidence）recall@k", ""]
    for name, run in report["runs"].items():
        lines += [f"### {name}", "", "| 篇数 | n | " + " | ".join(
            f"recall@{k}" for k in KS) + " |",
            "|" + "---|" * (len(KS) + 2)]
        for count, agg in run["by_evidence_count"].items():
            cells = [count, str(agg["count"])] + [
                _fmt(agg["recall"][str(k)]) for k in KS]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")

    lines += ["## 覆盖 vs 排序（pool=50）", "", "",
              "| run | gold 全在@50 | 有缺失@50 | 排序亏@5 | 覆盖缺槽@50 | 排序亏槽@5 | "
              "recall@5 | rank 天花板(=recall@50) | 排序余量 |",
              "|" + "---|" * 9]
    for name, run in report["runs"].items():
        m = run["misses"]
        lines.append("| " + " | ".join([
            name, f"{m['queries_full@50']}/{run['overall']['count']}",
            str(m["queries_with_missing@50"]), str(m["queries_with_ranking_loss@5"]),
            str(m["coverage_missing_slots@50"]), str(m["ranking_loss_slots@5"]),
            _fmt(m["recall@5"]), _fmt(m["rank_ceiling@5_from_pool50"]),
            _fmt(m["ranking_headroom@5"])]) + " |")

    lines += ["", "## 送嵌窗口 × gold 槽 recall（截断表征诊断）", "",
              "`all` = 该题该文章的 gold fact 全在 6000 字送嵌窗口内；`partial`/`none` = "
              "部分/全部落在窗口外（`base` 变体看不到）。",
              "", "| run | status | gold 槽 | recall@5 | recall@50 |", "|" + "---|" * 5]
    for name, run in report["runs"].items():
        wr = run.get("window_recall")
        if not wr:
            continue
        for status in ("all", "partial", "none"):
            a5, a50 = wr["@5"][status], wr["@50"][status]
            lines.append(f"| {name} | {status} | {a50['slots']} | "
                         f"{_fmt(a5['recall'] or 0.0)} | {_fmt(a50['recall'] or 0.0)} |")

    lines += ["", "## null_query（描述性，不校阈值；ADR-0017）", "",
              "| run | n | top-1 分数 mean | top-1 max |", "|---|---|---|---|"]
    for name, run in report["runs"].items():
        n = run["null_query"]
        lines.append(f"| {name} | {n['count']} | {n['top1_score']['mean']} | "
                     f"{n['top1_score']['max']} |")

    lines += ["", "## 确定性", "", "| run | run_hash | elapsed_s | "
              "延迟 mean/p50/p95/max (s) |", "|---|---|---|---|"]
    for name, run in report["runs"].items():
        lat = run["latency_s"]
        lines.append(f"| {name} | {run['run_hash']} | {run['elapsed_s']} | "
                     f"{lat['mean']}/{lat['p50']}/{lat['p95']}/{lat['max']} |")

    lines += ["", "## 隔离", "", "```json",
              json.dumps(report["meta"]["isolation"], ensure_ascii=False, indent=2),
              "```", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="MultiHop-RAG one-shot census")
    parser.add_argument("--runs", default="base:hybrid,base:vector",
                        help="逗号分隔 run；`full:hybrid` 需先 build_index --variant full")
    parser.add_argument("--pool", type=int, default=50)
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条（冒烟）")
    parser.add_argument("--trace-dir", default=None,
                        help="逐题 JSONL 断点续跑目录（缺省 artifacts/trace）")
    args = parser.parse_args(argv)

    eval_set, entries = mh.ensure_prepared()
    corpus, raw_queries = mh.load_dataset()
    window_status = mh.fact_window_status(corpus, raw_queries)
    with open(mh.INSPECTION_JSON, "r", encoding="utf-8") as handle:
        inspection = json.load(handle)
    if args.limit is not None:
        eval_set = dict(eval_set, queries=eval_set["queries"][:args.limit])
    known = {e.id for e in entries}
    missing = sorted({rid for q in eval_set["queries"] for rid in q["relevant"]
                      if rid not in known})
    if missing:
        raise SystemExit(f"金标不在索引里（{len(missing)}）：{missing[:5]}")

    runs = [r.strip() for r in args.runs.split(",") if r.strip()]
    trace_dir = args.trace_dir or os.path.join(mh.ARTIFACTS_DIR, "trace")
    os.makedirs(trace_dir, exist_ok=True)

    before = isolation_snapshot()
    results = {}
    for run in runs:
        trace = os.path.join(trace_dir, run.replace(":", "_") + ".jsonl")
        results[run] = run_one(run, eval_set, args.pool, args.limit, trace, window_status)
        sig = run_signature(results[run]["rows"])
        results[run]["run_hash"] = hashlib.sha256(
            json.dumps(sig, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]
    after = isolation_snapshot()

    report = {
        "meta": {
            "dataset": mh.DATASET,
            "license": mh.LICENSE,
            "source_repo": mh.SOURCE_REPO,
            "corpus_articles": inspection["corpus"]["articles"],
            "queries_total": inspection["queries"]["total"],
            "queries_answerable": inspection["queries"]["answerable"],
            "null_query": inspection["queries"]["null_query"],
            "pool": args.pool,
            "prod_pool": PROD_POOL,
            "rerank": False,
            "inspection": inspection,
            "store": {"base": variant_paths("base")[0], "full": variant_paths("full")[0]},
            "isolation": {"before": before, "after": after, "unchanged": before == after},
        },
        "runs": results,
    }

    # 逐题明细：id 只留整数序号（`multihop:<i:04d}` ↔ `corpus.json[i]`），无数据集正文。
    def short(entry_id):
        return int(entry_id.split(":", 1)[1]) if ":" in entry_id else entry_id

    per_query = {name: [
        {"id": r["id"], "question_type": r["question_type"],
         "evidence_count": r["evidence_count"],
         "relevant": [short(x) for x in r["relevant"]],
         "ranked": [short(x) for x in r["ranked"]],
         "recall": r.get("recall", {})}
        for r in run["rows"]
    ] for name, run in results.items()}

    with open(REPORT_MD, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report))
    for run in results.values():  # report.json 只留聚合；逐题明细在 per_query_ids.json
        run.pop("rows", None)
    with open(REPORT_JSON, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
    with open(PER_QUERY_JSON, "w", encoding="utf-8") as handle:
        json.dump(per_query, handle, ensure_ascii=False)

    print(f"[out] {REPORT_JSON}")
    print(f"[out] {PER_QUERY_JSON}")
    print(f"[out] {REPORT_MD}")
    for name, run in results.items():
        agg = run["overall"]
        print(f"{name}: recall@5={agg['recall']['5']} recall@50={agg['recall']['50']} "
              f"full@50={agg['full_coverage']['50']} hash={run['run_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
