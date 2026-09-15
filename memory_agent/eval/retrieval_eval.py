"""记忆检索确定性评测 harness（issue #24）。

对象 = **记忆检索**（不是法律 RAG）。评测链路 = 目标链路（向量 + 关键词 + rerank）；
默认 `hybrid-rerank`，可 `--mode` 做消融。一次运行产出 recall@k / nDCG@10 / MRR，
不调 LLM、可重复（run_hash 相同）。

用法（索引与只读语料配置见 memory_agent/settings.py）：

    venv\\Scripts\\python.exe memory_agent/eval/retrieval_eval.py \\
        --eval-set memory_agent/eval/retrieval_eval_set.json \\
        --mode hybrid-rerank --out memory_agent/eval/retrieval_baseline.json
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

from memory_agent import _bootstrap  # noqa: E402

_bootstrap.configure_stderr_logging()
_bootstrap.ensure_ragcore_on_path()

from memory_agent.eval.metrics import evaluate  # noqa: E402
from memory_agent.memory.index import MemoryIndex  # noqa: E402
from memory_agent.memory.retrieval import MemoryRetriever, default_reranker_factory  # noqa: E402

MODES = ("vector", "hybrid", "hybrid-rerank")
DEFAULT_EVAL_SET = os.path.join(HERE, "retrieval_eval_set.json")


def build_retriever_factory(mode: str):
    """按消融模式构造 retriever 工厂（注入 MemoryIndex，仍走 MemoryIndex.search）。"""
    from strategies.default import DefaultRetrievalStrategy

    def factory(store):
        strategy = DefaultRetrievalStrategy(enable_keyword=(mode != "vector"))
        reranker_factory = default_reranker_factory if mode == "hybrid-rerank" else None
        return MemoryRetriever(store, strategy=strategy, reranker_factory=reranker_factory)

    return factory


def load_eval_set(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data.get("queries"), list) or not data["queries"]:
        raise SystemExit(f"评测集为空或格式非法：{path}")
    return data


def check_relevant_ids(queries, known: set[str]) -> list[str]:
    missing = []
    for query in queries:
        for entry_id in query.get("relevant") or []:
            if entry_id not in known:
                missing.append(entry_id)
    return sorted(set(missing))


def warmup(index: MemoryIndex) -> None:
    """预热嵌入 + reranker（惰性加载），把模型加载成本排除在逐题计时之外。"""
    try:
        index.search("warmup", k=1)
    except Exception as exc:  # 预热失败不阻断评测
        print(f"warmup 跳过：{exc}", file=sys.stderr)


def run(index: MemoryIndex, queries, top_k: int, trace_path: str | None = None):
    """逐条检索；给了 trace_path 就每条追加一行 JSONL（可中断 / 可 --resume）。"""
    done: dict[str, dict] = {}
    if trace_path and os.path.isfile(trace_path):
        with open(trace_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                done[record["id"]] = record
        print(f"resume: 已有 {len(done)} 条 trace", file=sys.stderr)

    trace_handle = open(trace_path, "a", encoding="utf-8") if trace_path else None
    try:
        for i, query in enumerate(queries, start=1):
            if query["id"] in done:
                # trace 只复用排名；标签始终以当前评测集为准（便于改标注后重算）
                done[query["id"]]["relevant"] = list(query.get("relevant") or [])
                continue
            started = time.time()
            hits = index.search(query["query"], k=top_k)
            elapsed = time.time() - started
            record = {
                "id": query["id"],
                "query": query["query"],
                "relevant": list(query.get("relevant") or []),
                "ranked": [hit["id"] for hit in hits],
                "ranked_scores": [hit["score"] for hit in hits],
                "elapsed_s": round(elapsed, 3),
            }
            done[record["id"]] = record
            if trace_handle:
                trace_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                trace_handle.flush()
            print(f"[{i}/{len(queries)}] {query['id']} "
                  f"{elapsed:.1f}s", file=sys.stderr)
    finally:
        if trace_handle:
            trace_handle.close()
    return [done[q["id"]] for q in queries if q["id"] in done]


def run_hash(report: dict) -> str:
    canonical = json.dumps(
        {"aggregate": report["aggregate"], "per_query": report["per_query"],
         "no_answer": report["no_answer"]},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    idx = min(int(len(ordered) * pct), len(ordered) - 1)
    return ordered[idx]


def latency_stats(records: list[dict]) -> dict | None:
    """逐题检索耗时的 mean / p50 / p95 / max（秒）。

    口径与 `experiments/rerank-latency/results.md` 一致（p95 = 排序后 95% 位置）。
    不计入指标、不影响 `run_hash`——只作延迟证据。
    """
    times = [r["elapsed_s"] for r in records
             if isinstance(r.get("elapsed_s"), (int, float))]
    if not times:
        return None
    return {
        "count": len(times),
        "mean": round(sum(times) / len(times), 3),
        "p50": round(_percentile(times, 0.50), 3),
        "p95": round(_percentile(times, 0.95), 3),
        "max": round(max(times), 3),
    }


def _fmt(value) -> str:
    return f"{value:.4f}" if isinstance(value, float) else str(value)


def print_summary(report: dict, meta: dict) -> None:
    agg = report["aggregate"]
    print(f"mode={meta['mode']} gen={meta['index_gen']} "
          f"queries={agg['queries_total']} "
          f"(answerable={agg['queries_answerable']}, no_answer={agg['queries_no_answer']})")
    print("recall " + "  ".join(f"@{k}={_fmt(v)}" for k, v in agg["recall"].items()))
    print(f"nDCG@10={_fmt(agg['ndcg@10'])}  MRR={_fmt(agg['mrr'])}  "
          f"misses={len(agg['misses'])}/{agg['queries_answerable']}")
    if "no_answer_top1_score" in agg:
        print(f"no-answer top1 score: {agg['no_answer_top1_score']}")
    if meta.get("latency_s"):
        lat = meta["latency_s"]
        print(f"latency(s): mean={lat['mean']}  p50={lat['p50']}  "
              f"p95={lat['p95']}  max={lat['max']}")
    print(f"run_hash={meta['run_hash']}  elapsed={meta['elapsed_s']}s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="memory retrieval deterministic eval")
    parser.add_argument("--eval-set", default=DEFAULT_EVAL_SET)
    parser.add_argument("--mode", choices=MODES, default="hybrid-rerank")
    parser.add_argument("--k", type=int, nargs="+", default=[1, 3, 5, 10])
    parser.add_argument("--ndcg-k", type=int, default=10)
    parser.add_argument("--out", default=None, help="把完整报告写到此 JSON")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条（冒烟用）")
    parser.add_argument("--trace", default=None,
                        help="逐条追加 JSONL；中断后原样重拉可续跑")
    args = parser.parse_args(argv)

    eval_set = load_eval_set(args.eval_set)
    queries = eval_set["queries"]
    if args.limit is not None:
        queries = queries[:args.limit]
    top_k = max(max(args.k), args.ndcg_k)

    index = MemoryIndex(retriever_factory=build_retriever_factory(args.mode))
    known = set(index.known_ids())
    missing = check_relevant_ids(queries, known)
    if missing:
        raise SystemExit(
            f"评测集里有 {len(missing)} 个相关条目不在当前索引（gen={index.gen}）里，"
            f"请先重建索引或修正评测集：{missing[:10]}"
        )

    warmup(index)
    started = time.time()
    records = run(index, queries, top_k=top_k, trace_path=args.trace)
    report = evaluate(records, ks=tuple(args.k), ndcg_k=args.ndcg_k)
    meta = {
        "mode": args.mode,
        "eval_set": os.path.relpath(os.path.abspath(args.eval_set), REPO_ROOT),
        "index_gen": index.gen,
        "top_k": top_k,
        "indexed_entries": len(known),
        "elapsed_s": round(time.time() - started, 2),
        "latency_s": latency_stats(records),
    }
    meta["run_hash"] = run_hash(report)
    full = {"meta": {**meta, "eval_set_name": eval_set.get("name"),
                     "eval_set_version": eval_set.get("version")},
            "aggregate": report["aggregate"], "per_query": report["per_query"],
            "no_answer": report["no_answer"]}

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(full, handle, ensure_ascii=False, indent=2)
    print_summary(report, meta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
