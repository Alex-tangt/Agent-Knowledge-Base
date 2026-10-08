"""Lead 侧**独立复核**（#74 A″ 验收）——不复用 `run_a2.py` 的任何计算。

只读原始产物，自己重算一遍头条数字，并与 `report.json` 对账：

1. Step 0 闸门：`control_rankings.json` 的排名 vs #47 `per_query_ids.json`（逐位）；
2. 三臂召回（A1 / C_k）+ 主次配对 Δ 与我自己的百分位 bootstrap CI；
3. **探索性**补充（明确标注：事后，非预注册）：A1 的「实际展示深度」落在哪条 C_k 曲线上
   —— 因为 A1 实测 mean 展示 6.445 条、上界 15，而对照按**允许**额度给 15/20，
   两者额度口径不同，需要看「同**实际**深度」下 A1 是否仍更差；
4. `fallback` 率 + Wilson CI（判定带 0.10）；
5. 描述性：停止分布 / 工具调用 / 展示条数分布。

    python experiments/agentic-rag-census/phase_a2/lead_verify_74.py --census-dir <dir>
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def norm_id(value):
    text = str(value)
    return int(text.split(":")[1]) if ":" in text else int(text)


def mean(values):
    return sum(values) / len(values) if values else 0.0


def recall_at(ranked, relevant, k):
    if not relevant:
        return None
    gold = set(norm_id(x) for x in relevant)
    top = set(norm_id(x) for x in ranked[:k])
    return len(gold & top) / len(gold)


def boot_ci(diffs, iters=10000, seed=20261008):
    """我自己的百分位 bootstrap（不用 repo 的 stats，避免复核复用被测代码）。"""
    size = len(diffs)
    if size == 0:
        return 0.0, 0.0, 0.0
    rng = random.Random(seed)
    observed = mean(diffs)
    samples = sorted(mean([diffs[rng.randrange(size)] for _ in range(size)])
                     for _ in range(iters))
    return (observed, samples[int(0.025 * iters)], samples[int(0.975 * iters)])


def wilson(success, total, z=1.959963984540054):
    if total == 0:
        return 0.0, 0.0
    p = success / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * ((p * (1 - p) / total + z * z / (4 * total * total)) ** 0.5) / denom
    return centre - half, centre + half


def main_worktree():
    proc = subprocess.run(["git", "-C", os.path.dirname(os.path.abspath(__file__)),
                           "worktree", "list", "--porcelain"],
                          capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            return line.split(" ", 1)[1].strip()
    return os.getcwd()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lead independent verification (#74)")
    parser.add_argument("--census-dir", default=os.path.join(
        main_worktree(), "experiments", "agentic-rag-census"))
    parser.add_argument("--report", default=None)
    args = parser.parse_args(argv)

    census = args.census_dir
    phase = os.path.join(census, "phase_a2")
    control = load_json(os.path.join(phase, "artifacts", "control_rankings.json"))
    rows = {row["id"]: row for row in control["rows"]}
    ids = [row["id"] for row in control["rows"]]
    report = load_json(args.report or os.path.join(phase, "report.json"))

    out = {"n_rows": len(ids), "checks": {}}

    # ---------------------------------------------------------- 1. Step 0 闸门
    reference = {row["id"]: row for row in load_json(
        os.path.join(census, "artifacts", "per_query_ids.json"))["base:hybrid"]}
    same_ranked, first_div, same_rel, missing_ref = 0, None, 0, []
    for qid in ids:
        ref = reference.get(qid)
        if ref is None:
            missing_ref.append(qid)
            continue
        if [norm_id(x) for x in rows[qid]["ranked"]] == [norm_id(x) for x in ref["ranked"]]:
            same_ranked += 1
        elif first_div is None:
            first_div = qid
        if set(norm_id(x) for x in rows[qid]["relevant"]) == \
                set(norm_id(x) for x in ref["relevant"]):
            same_rel += 1
    out["checks"]["step0_ranked_bit_identical"] = same_ranked
    out["checks"]["step0_first_divergence"] = first_div
    out["checks"]["step0_relevant_identical"] = same_rel
    out["checks"]["step0_missing_in_reference"] = missing_ref

    # ---------------------------------------------------------- 2. 轨迹
    records = {}
    with open(os.path.join(phase, "artifacts", "trace", "a1_traces.jsonl"),
              "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                record = json.loads(line)
                records[record["id"]] = record      # last-wins（断点续跑口径）
    completed = [q for q in ids if q in records and not records[q].get("error")]
    errors = [q for q in ids if q in records and records[q].get("error")]
    answerable = [q for q in completed if rows[q]["relevant"]]
    out["checks"]["completed"] = len(completed)
    out["checks"]["errors"] = len(errors)
    out["checks"]["answerable"] = len(answerable)

    def evidence_of(qid):
        trace = records[qid]["trace"]
        return [norm_id(x) for x in (trace.get("final") or {}).get("evidence_ids") or []]

    def trigger_of(qid):
        stop = records[qid]["trace"].get("stop")
        return stop.get("trigger") if isinstance(stop, dict) else stop

    a1 = {q: recall_at(evidence_of(q), rows[q]["relevant"], 10 ** 6) for q in answerable}
    curves = {k: {q: recall_at(rows[q]["ranked"], rows[q]["relevant"], k)
                  for q in answerable} for k in (1, 5, 10, 15, 20, 50)}
    out["recall"] = {"A1": round(mean(list(a1.values())), 6)}
    out["recall"].update({f"C{k}": round(mean(list(curves[k].values())), 6)
                          for k in curves})

    # 主/次配对
    out["paired"] = {}
    for name, k in (("A1-C20", 20), ("A1-C5", 5), ("A1-C15", 15), ("A1-C50", 50)):
        diffs = [a1[q] - curves[k][q] for q in answerable]
        observed, lo, hi = boot_ci(diffs)
        out["paired"][name] = {"delta": round(observed, 6),
                               "ci": [round(lo, 6), round(hi, 6)],
                               "significant": (lo > 0) or (hi < 0)}

    # ---------------------------------- 3. 探索性：A1 落在哪条 C_k 曲线上（事后）
    full_curve = {}
    for k in range(1, 21):
        full_curve[k] = mean([recall_at(rows[q]["ranked"], rows[q]["relevant"], k)
                              for q in answerable])
    depth = [len(evidence_of(q)) for q in answerable]
    nearest = min(full_curve, key=lambda k: abs(full_curve[k] - mean(list(a1.values()))))
    out["exploratory_realized_depth"] = {
        "caveat": "事后补充，非预注册；A1 的『实际展示深度』与对照的『允许额度』不是一个口径",
        "a1_display_mean": round(mean(depth), 4),
        "a1_display_p50": sorted(depth)[len(depth) // 2],
        "a1_display_max": max(depth) if depth else None,
        "curve_C1_to_C20": {k: round(full_curve[k], 6) for k in sorted(full_curve)},
        "nearest_Ck_to_A1": nearest,
        "nearest_Ck_value": round(full_curve[nearest], 6),
        "a1_value": round(mean(list(a1.values())), 6),
    }

    # 深度匹配对照（事后 / 探索性）：每题按**该题 A1 自己的展示条数**截断一次性排名，再逐题配对。
    # 为什么必要：A1 实测 mean 6.0 条、上界 15，而主对照按**允许**额度给 20——两者口径不同，
    # 直接相减混了「选择更差」与「额度没花」。这个对照把额度钉在同一题同一深度上。
    depth_matched = {q: recall_at(rows[q]["ranked"], rows[q]["relevant"],
                                  max(1, len(evidence_of(q)))) for q in answerable}
    diffs_dm = [a1[q] - depth_matched[q] for q in answerable]
    observed_dm, lo_dm, hi_dm = boot_ci(diffs_dm)
    out["exploratory_depth_matched"] = {
        "caveat": "事后、探索性，非预注册：自称『深度匹配』的对照，务必与主对照分开引用",
        "Cad_mean": round(mean(list(depth_matched.values())), 6),
        "A1_mean": round(mean(list(a1.values())), 6),
        "delta": round(observed_dm, 6),
        "ci": [round(lo_dm, 6), round(hi_dm, 6)],
        "significant": (lo_dm > 0) or (hi_dm < 0),
        "depth_min": min(depth) if depth else None,
    }

    # ---------------------------------------------------------- 4. fallback
    triggers = {}
    for qid in completed:
        trigger = trigger_of(qid) or "none"
        triggers[trigger] = triggers.get(trigger, 0) + 1
    falls = triggers.get("fallback", 0)
    lo_w, hi_w = wilson(falls, len(completed))
    out["fallback"] = {
        "count": falls, "completed": len(completed),
        "rate": round(falls / len(completed), 6) if completed else None,
        "wilson_ci": [round(lo_w, 6), round(hi_w, 6)],
        "verdict": ("confirmed" if lo_w > 0.10 else
                    "noise" if hi_w < 0.10 else "undecided"),
    }
    out["stop_triggers"] = triggers

    # ---------------------------------------------------------- 5. 工具/轮数
    tools, searches_per_q, rounds_per_q = {}, {}, {}
    for qid in completed:
        calls = [(call.get("tool")) for rnd in (records[qid]["trace"].get("rounds") or [])
                 for call in (rnd.get("tool_calls") or [])]
        for tool in calls:
            tools[tool] = tools.get(tool, 0) + 1
        searches_per_q[qid] = calls.count("memory_search")
        rounds_per_q[qid] = len(records[qid]["trace"].get("rounds") or [])
    out["tools"] = dict(sorted(tools.items(), key=lambda item: -item[1]))
    out["nav_tool_use_rate"] = round(
        sum(1 for q in completed
            if any(t not in ("memory_search", "memory_get")
                   for t in [(call.get("tool"))
                             for rnd in (records[q]["trace"].get("rounds") or [])
                             for call in (rnd.get("tool_calls") or [])])) / len(completed), 6)
    out["dist"] = {"searches_per_question": {str(k): list(searches_per_q.values()).count(k)
                                            for k in sorted(set(searches_per_q.values()))},
                   "rounds_per_question": {str(k): list(rounds_per_q.values()).count(k)
                                           for k in sorted(set(rounds_per_q.values()))}}

    # ---------------------------------------------------------- 6. 与 report.json 对账
    theirs = {name: (report.get("metrics", {}).get(name) or {}).get("mean_evidence_recall")
              for name in ("A1", "C20", "C5", "C15")}
    their_pairs = report.get("paired") or {}
    out["reconcile"] = {
        "mine_recall": out["recall"],
        "theirs_recall": {k: v for k, v in theirs.items() if v is not None},
        "recall_diff": {k: round(out["recall"][k] - theirs[k], 9)
                        for k in theirs if theirs[k] is not None},
        "theirs_paired_keys": sorted(their_pairs)[:8],
        "their_A1_minus_C20": (their_pairs.get("A1-C20") or their_pairs.get("primary")
                              or {}).get("delta") if their_pairs else None,
        "my_A1_minus_C20": out["paired"]["A1-C20"]["delta"],
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
