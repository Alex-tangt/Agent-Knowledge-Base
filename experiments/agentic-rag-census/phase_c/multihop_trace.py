"""MultiHop-RAG → H harness（trace 契约）适配器（#71 / ADR-0030 D7）。

**本模块不重跑检索**（纪律「已定数值不重跑」）：数据源是 #47 Phase A 的**不可变证据**
`../artifacts/per_query_ids.json`（逐题：`relevant` 金标文章序号 + `ranked` 全量排名）。
它把那份 one-shot 结果**表达成 H 的契约格式**（`memory_agent/trace.py` 的 `Trace` JSONL），
于是 `memory_agent.eval.harness` 的 scorer / stats 能直接消费——为 #63（A 导航工具）提供
"同一 harness 上的 MultiHop-RAG 一次性基线"。

口径与边界（沿 ADR-0026 D5/D6 / ADR-0030 D7.5）：

- 外部语料**只作机制证据**，**不声称本 KB 增益**；
- 单位 = **条目级**（金标 = evidence `url` 对齐的文章 `multihop:<i:04d>`），与 #47 同粒度；
- 数据集 **无正文、无 query 文本**落盘（`per_query_ids.json` 只有序号与排名）；
  故 trace 的 `query` 字段是**占位符**，只承担 id 对齐与契约形状，**不含数据集文本**（ODC-BY 归属见报告 meta）。

    $py = "D:\\...\\venv\\Scripts\\python.exe"
    $env:PYTHONPATH = "D:\\...\\wk-71-eval"
    & $py experiments/agentic-rag-census/phase_c/multihop_trace.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CENSUS_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(os.path.dirname(CENSUS_DIR))
for path in (REPO_ROOT, CENSUS_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

import multihop as mh  # noqa: E402  （只取常量：DATASET / LICENSE / SOURCE_REPO）

from memory_agent.eval.harness.scorer import evaluate  # noqa: E402
from memory_agent.eval.harness.stats import bootstrap_ci  # noqa: E402
from memory_agent.trace import (  # noqa: E402
    STOP_BUDGET, Round, Stop, ToolCall, Trace, compute_run_hash,
)

PER_QUERY_JSON = os.path.join(mh.ARTIFACTS_DIR, "per_query_ids.json")
ARTIFACTS = os.path.join(HERE, "artifacts")
KS = (1, 5, 10, 14, 20, 50)
# 展示给模型的条数（= 产品 memory_search 默认 top-5）；召回池深度留在 tool_call.args 里。
DISPLAY_K = 5
DISPLAY_K_KEYS = ("k5", "k14")
QUERY_PLACEHOLDER = "<redacted: MultiHop-RAG query, ODC-BY; 排名见 artifacts/per_query_ids.json>"
# #47 Phase A 公布的同期数字（`../README.md` 结论表）——用于**一致性核对**，不是新测。
# 容差 1e-3：README 里的数字是四舍五入（hybrid 4 位、vector 3 位）。
PUBLISHED_TOL = 1e-3
PUBLISHED = {
    "base:hybrid": {"1": 0.2526, "5": 0.6504, "10": 0.7833, "14": 0.8470,
                    "20": 0.8998, "50": 0.9645},
    "base:vector": {"5": 0.6320, "50": 0.9645},
}


def entry_id(index: int) -> str:
    return f"{mh.SOURCE_LABEL}:{index:04d}"


def load_evidence(path: str = PER_QUERY_JSON) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def rows_for(evidence: dict, run: str) -> list[dict]:
    for key in (run, run.replace(":", "_")):
        if key in evidence:
            return evidence[key]
    raise SystemExit(f"per_query_ids.json 里没有 run：{run}（有 {sorted(evidence)}）")


def build_traces(rows: list[dict], *, run: str, display_k: int) -> list[Trace]:
    """one-shot 检索 → H 契约 trace（一轮、一个 memory_search 工具调用、stop=budget）。"""
    traces = []
    for row in rows:
        ranked = [entry_id(i) for i in row["ranked"]]
        relevant = [entry_id(i) for i in row["relevant"]]
        shown = ranked[:display_k]
        traces.append(Trace(
            id=row["id"],
            query=QUERY_PLACEHOLDER,
            rounds=[Round(
                round=0,
                query=QUERY_PLACEHOLDER,
                tool_calls=[ToolCall(
                    tool="memory_search",
                    args={"run": run, "pool": len(ranked), "display_k": display_k,
                          "mode": "one-shot"},
                    added_ids=ranked,
                    result_count=len(ranked),
                )],
                model_output="",
            )],
            # one-shot = 单轮即耗尽预算；用 stop=budget 作分组标签（不是"answer"）。
            stop=Stop(trigger=STOP_BUDGET, reason="one-shot baseline: max_hops=1 用尽"),
            final={"answer": None, "evidence_ids": shown,
                   "relevant_ids": relevant},
            meta={"arm": "one-shot", "run": run, "display_k": display_k,
                  "unit": "entry-level (article)",
                  "dataset": mh.DATASET, "license": mh.LICENSE,
                  "provenance": "reuse of #47 Phase A artifacts/per_query_ids.json",
                  "kinds": {"relevant": "gold evidence articles (unique)",
                            "ranked": "full one-shot ranking, pool=50"}},
        ))
    return traces


def write_jsonl(traces: list[Trace], path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for trace in traces:
            handle.write(json.dumps(trace.to_dict(), ensure_ascii=False) + "\n")


def recall_at(rows: list[dict], k: int) -> float:
    answerable = [r for r in rows if r["relevant"]]
    if not answerable:
        return 0.0
    total = 0.0
    for row in answerable:
        gold = set(row["relevant"])
        top = set(row["ranked"][:k])
        total += len(gold & top) / len(gold)
    return round(total / len(answerable), 6)


def per_query_recall(rows: list[dict], k: int) -> list[float]:
    out = []
    for row in rows:
        gold = set(row["relevant"])
        if not gold:
            continue
        out.append(len(gold & set(row["ranked"][:k])) / len(gold))
    return out


def run(run_name: str, rows: list[dict]) -> dict:
    published = PUBLISHED.get(run_name, {})
    recalls = {str(k): recall_at(rows, k) for k in KS}
    ci = {str(k): bootstrap_ci(per_query_recall(rows, k)) for k in KS}
    checks = {k: {"computed": recalls[k], "published": published.get(k),
                  "match": (published.get(k) is not None
                            and abs(recalls[k] - published[k]) <= PUBLISHED_TOL)}
              for k in (str(x) for x in KS) if k in published}

    eval_set = {row["id"]: {
        "query": QUERY_PLACEHOLDER,
        "kind": "answerable" if row["relevant"] else "no_answer",
        "relevant": [entry_id(i) for i in row["relevant"]],
        "gold_answer": None,      # 外部集没有可判定的答案文本口径 → answer_correct 不适用
    } for row in rows}

    arms = {}
    for key in DISPLAY_K_KEYS:
        display_k = int(key[1:])
        traces = build_traces(rows, run=run_name, display_k=display_k)
        scored = evaluate(traces, eval_set)
        hash_ = hashlib.sha256("".join(
            compute_run_hash(t) for t in traces).encode()).hexdigest()[:16]
        arms[key] = {
            "display_k": display_k,
            "traces": len(traces),
            "run_hash": hash_,
            "harness": {
                "overall": scored["overall"],
                "by_stop": scored["by_stop"],
            },
            # scorer 的 answer_correct 对本适配**不适用**（one-shot 无 answer），
            # 其 `mean_evidence_recall` / `gold_unreached_rate` 才是有意义的通路自证。
            "harness_note": "answer_correct 不适用（one-shot 不产答案，gold_answer=None）",
        }
    return {
        "run": run_name,
        "rows": len(rows),
        "answerable": sum(1 for r in rows if r["relevant"]),
        "null_query": sum(1 for r in rows if not r["relevant"]),
        "recall": recalls,
        "recall_ci": ci,
        "consistency_with_phase_a": checks,
        "consistency_all_match": all(c["match"] for c in checks.values()),
        "arms": arms,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="MultiHop-RAG → H trace contract (#71)")
    parser.add_argument("--evidence", default=PER_QUERY_JSON)
    parser.add_argument("--runs", default="base:hybrid,base:vector")
    parser.add_argument("--trace-dir", default=None,
                        help="全量契约 JSONL 目录（默认 %%TEMP%%/eval71-phase-c/trace；"
                             "20MB 级，不入库——这里只放**可重生成**的全量轨迹）")
    parser.add_argument("--sample", type=int, default=25,
                        help="入库的契约样例条数（artifacts/sample_traces.jsonl，0 = 不写）")
    args = parser.parse_args(argv)

    trace_dir = args.trace_dir or os.path.join(
        tempfile.gettempdir(), "eval71-phase-c", "trace")
    evidence = load_evidence(args.evidence)
    results = {}
    for run_name in [r.strip() for r in args.runs.split(",") if r.strip()]:
        rows = rows_for(evidence, run_name)
        results[run_name] = run(run_name, rows)
        for key in DISPLAY_K_KEYS:
            traces = build_traces(rows, run=run_name, display_k=int(key[1:]))
            write_jsonl(traces, os.path.join(
                trace_dir, f"{run_name.replace(':', '_')}_{key}.jsonl"))
    if args.sample:
        sample = build_traces(rows_for(evidence, "base:hybrid"), run="base:hybrid",
                             display_k=DISPLAY_K)[:args.sample]
        write_jsonl(sample, os.path.join(ARTIFACTS, "sample_traces.jsonl"))

    report = {
        "meta": {
            "dataset": mh.DATASET,
            "license": mh.LICENSE,
            "source_repo": mh.SOURCE_REPO,
            "evidence_file": os.path.relpath(args.evidence, HERE).replace("\\", "/"),
            "provenance": "复用 #47 Phase A 不可变证据（per_query_ids.json）；"
                          "**未重跑**检索（纪律：已定数值不重跑）",
            "unit": "entry-level（一篇文章 = 一个条目 multihop:<i:04d>）",
            "ks": list(KS),
            "display_k": list(DISPLAY_K_KEYS),
            "contract": "memory_agent/trace.py（Trace JSONL）",
            "harness": "memory_agent.eval.harness.scorer.evaluate + stats.bootstrap_ci",
            "trace_dir": trace_dir,
            "sample_traces": (os.path.relpath(os.path.join(ARTIFACTS, "sample_traces.jsonl"),
                                              HERE).replace("\\", "/") if args.sample else None),
            "published_tolerance": PUBLISHED_TOL,
            "boundary": "外部语料只作机制证据，不声称本库增益（ADR-0026 D5/D6）",
        },
        "runs": results,
    }
    os.makedirs(ARTIFACTS, exist_ok=True)
    with open(os.path.join(ARTIFACTS, "report.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)

    lines = ["# phase_c — MultiHop-RAG → H harness trace 契约（#71）", "",
             f"- 证据来源：`{report['meta']['evidence_file']}`（#47 Phase A，**复用未重跑**）",
             f"- 契约：`{report['meta']['contract']}`；消费：`memory_agent.eval.harness`",
             f"- 单位：{report['meta']['unit']}；k = {list(KS)}",
             "", "## 一次性检索基线（条目级 recall@k）+ bootstrap CI", "",
             "| run | " + " | ".join(f"r@{k}" for k in KS) + " |",
             "|" + "---|" * (len(KS) + 1)]
    for name, res in results.items():
        lines.append("| " + name + " | " + " | ".join(
            f"{res['recall'][str(k)]:.4f}" for k in KS) + " |")
    lines += ["", "| run | k | 95% CI | 与 #47 公布数字一致 |", "|---|---|---|---|"]
    for name, res in results.items():
        for k in KS:
            ci = res["recall_ci"][str(k)]
            match = res["consistency_with_phase_a"].get(str(k), {}).get("match")
            lines.append(f"| {name} | {k} | [{ci['lo']}, {ci['hi']}] | "
                         f"{'✅' if match else ('—' if match is None else '❌')} |")
    lines += ["", "## harness 通路自证（按 stop 分组；answer_correct 不适用）", "",
              "| run | arm(display_k) | traces | run_hash | mean_evidence_recall | "
              "gold_unreached_rate |", "|" + "---|" * 6]
    for name, res in results.items():
        for key, arm in res["arms"].items():
            overall = arm["harness"]["overall"]
            lines.append(f"| {name} | {key}({arm['display_k']}) | {arm['traces']} | "
                         f"{arm['run_hash']} | {overall['mean_evidence_recall']} | "
                         f"{overall['gold_unreached_rate']} |")
    lines += ["", "## 边界", "",
              "1. **外部语料只作机制证据**，不声称本 KB 增益（ADR-0026 D5/D6 / ADR-0030 D7.5）。",
              "2. 本报告**复用 #47 的不可变证据**，不是重新测量；一致性核对证明适配器消费的是同一份数据。",
              "3. **全量 trace 不入库**（20MB 级，可一行命令重生成）：落 `%TEMP%/eval71-phase-c/trace/`；",
              "   入库的只有契约**样例** `artifacts/sample_traces.jsonl`（前 25 条）。",
              "4. trace 的 `query` 是**占位符**：数据集 query 文本未落盘（`per_query_ids.json` 只有序号与排名），",
              "   故本适配只证明**契约形状与打分通路**，不重放查询文本。",
              "5. `answer_correct` 不适用：one-shot 基线不产答案，`gold_answer=None` → scorer 回 `None`。",
              f"6. 许可/版本：`{report['meta']['dataset']}`（{report['meta']['license']}），"
              "参考实现 commit `c1c1287aa60a94acf9c4d20c891c9cd611a0f6e8`（见 census README）。",
              f"7. 一致性核对容差 {report['meta']['published_tolerance']}"
              "（#47 README 的数字是四舍五入）。",
              ""]
    with open(os.path.join(HERE, "report.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))

    for name, res in results.items():
        print(f"{name}: " + " ".join(
            f"r@{k}={res['recall'][str(k)]}" for k in KS)
            + f" consistency={res['consistency_all_match']}")
    print(f"[out] {os.path.join(ARTIFACTS, 'report.json')}")
    print(f"[out] {os.path.join(HERE, 'report.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
