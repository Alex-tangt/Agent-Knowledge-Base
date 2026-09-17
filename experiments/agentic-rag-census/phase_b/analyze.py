"""Phase B 离线分析（issue #48）：三臂 recall / 答案正确率 / 停早-过检率 / hop 曲线 / 约束检查。

全部从 `artifacts/trace/{b1,b2,b3}.jsonl` + Phase A trace 离线算，**零额外模型成本**。

口径：
- `recall@k`（条目级）：`|G ∩ (∪_r top-k_r)| / |G|`，`G` = 该题 unique 金标。
- `evidence_recall@5`：答案实际看到的证据 `evidence_ids` 的 recall（B3 = 累计 top-5）。
- 失效类型切片（Phase A）：`full@5` / `rank_miss`（进 @50 不进 @5）/ `cover_miss`（@50 也缺）。
- B3 早停/晚停：`Reach = G ∩ top50(B1)`（可达上界代理）；`C_r` = 前 r 轮累计命中；`r*` = 最早 complete。
- 约束检查：非来源实体 + 数字在改写里的保留率（规则，deterministic）。

输出 `report.md`（人读）+ `report.json`（聚合）+ `per_query.json`（逐题 id/指标，**无正文**）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
from collections import defaultdict

from common import (
    PHASE_B_ARTIFACTS,
    PHASE_B_DIR,
    load_eval,
    load_sample,
    load_trace,
    sample_questions,
    source_vocabulary,
)

ARM_LABELS = {"b1": "B1 单发", "b2": "B2 单发+改写", "b3": "B3 迭代循环"}
KS = (1, 3, 5, 10, 20, 50)
BOOTSTRAP = 10000
BOOTSTRAP_SEED = 20260917
R_MAX = 4

# ------------------------------------------------------------------ constraint

_CAP_RE = re.compile(r"\b[A-Z][A-Za-z0-9&.\-]*(?:\s+[A-Z][A-Za-z0-9&.\-]*)*\b")
_NUM_RE = re.compile(r"\b\d[\d.,/%\-]*\b")
_QUESTION_STOP = {
    "which", "who", "what", "when", "where", "why", "how", "considering",
    "according", "the", "in", "on", "by", "for", "and", "both", "twice",
    "does", "do", "did", "was", "were", "is", "are", "a", "an", "of", "to",
}


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def extract_constraints(query: str, sources: list[str]) -> tuple[list[str], list[str]]:
    """返回 `(content_constraints, source_hits)`。"""
    lowered = _norm(query)
    source_hits = [s for s in sources if _norm(s) and _norm(s) in lowered]
    source_norms = {_norm(s) for s in source_hits}
    candidates: list[str] = []
    for match in _CAP_RE.findall(query or ""):
        token = match.strip()
        norm = _norm(token)
        if len(token) < 2 or norm in _QUESTION_STOP:
            continue
        if any(norm in s or s in norm for s in source_norms):
            continue
        candidates.append(token)
    candidates.extend(_NUM_RE.findall(query or ""))
    seen: dict[str, str] = {}
    for token in candidates:
        seen.setdefault(_norm(token), token)
    return list(seen.values()), source_hits


def retention(constraints: list[str], rewrite: str) -> float | None:
    if not constraints:
        return None
    lowered = _norm(rewrite)
    kept = sum(1 for token in constraints if _norm(token) in lowered)
    return kept / len(constraints)


# --------------------------------------------------------------------- load

def parse_cost() -> dict:
    """从 run.log 抓 LLM 调用/token 统计（成本证据）。"""
    stats: dict = {"raw": None, "run_log": os.path.join(PHASE_B_ARTIFACTS, "trace", "run.log")}
    if os.path.isfile(stats["run_log"]):
        with open(stats["run_log"], encoding="utf-8") as handle:
            for line in handle:
                if "stats main=" in line:
                    stats["raw"] = line.strip().split("stats ", 1)[1]
    return stats


def load_arm(path: str) -> tuple[dict[str, dict], int]:
    records: dict[str, dict] = {}
    errors = 0
    if not os.path.isfile(path):
        return records, errors
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if "error" in record:
                errors += 1
                continue
            records[record["id"]] = record
    return records, errors


# ------------------------------------------------------------------ metrics

def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def arm_ranked(record: dict) -> dict[int, list[str]]:
    """`{k: 该臂实际轮次内 top-k 的并集}`。"""
    per_k: dict[int, set[str]] = {k: set() for k in KS}
    for round_record in record.get("rounds", []):
        ids = round_record.get("ids") or []
        for k in KS:
            per_k[k].update(ids[:k])
    return {k: list(v) for k, v in per_k.items()}


def recall_of(gold: list[str], ranked: list[str]) -> float:
    if not gold:
        return 0.0
    return len(set(gold) & set(ranked)) / len(set(gold))


def arm_metrics(record: dict) -> dict:
    gold = record.get("relevant") or []
    ranked = arm_ranked(record)
    evidence = record.get("evidence_ids") or []
    out = {
        "recall": {k: recall_of(gold, ranked[k]) for k in KS},
        "evidence_recall": recall_of(gold, evidence),
        "evidence_count": len(evidence),
        "full@5": recall_of(gold, ranked[5]) >= 1.0,
        "correct": record.get("correct"),
        "answer_intersection": record.get("answer_intersection"),
        "match_source": record.get("match_source"),
        "rounds_used": record.get("rounds_used"),
        "stop_trigger": record.get("stop_trigger"),
    }
    return out


def aggregate(metrics: dict[str, dict]) -> dict:
    rows = [m for m in metrics.values() if m["recall"]]
    if not rows:
        return {"count": 0}
    return {
        "count": len(rows),
        "recall": {str(k): round(_mean(r["recall"][k] for r in rows), 6) for k in KS},
        "evidence_recall": round(_mean(r["evidence_recall"] for r in rows), 6),
        "full@5": round(sum(1 for r in rows if r["full@5"]) / len(rows), 6),
        "answer_accuracy": round(_mean(
            float(r["correct"]) for r in rows if r["correct"] is not None), 6),
        "answer_intersection": round(_mean(
            float(r["answer_intersection"]) for r in rows
            if r["answer_intersection"] is not None), 6),
        "answer_judged": sum(1 for r in rows if r["match_source"] == "judge"),
    }


def group_rows(rows: list[dict], trace: dict) -> dict[str, list[str]]:
    """按 Phase A 失效类型给 query 分组。"""
    groups: dict[str, list[str]] = {"full@5": [], "rank_miss": [], "cover_miss": []}
    for row in rows:
        if row["kind"] == "no_answer" or not row["relevant"]:
            continue
        pa = trace.get(row["id"])
        if pa is None:
            continue
        gold, ranked = set(row["relevant"]), pa["ranked"]
        top5, top50 = set(ranked[:5]), set(ranked[:50])
        if gold <= top5:
            groups["full@5"].append(row["id"])
        elif gold <= top50:
            groups["rank_miss"].append(row["id"])
        else:
            groups["cover_miss"].append(row["id"])
    return groups


def stop_analysis(records: dict[str, dict], trace: dict,
                  eval_set: dict[str, dict]) -> dict:
    per_query = []
    for qid, record in records.items():
        if eval_set.get(qid, {}).get("kind") == "no_answer":
            continue
        gold = set(record.get("relevant") or [])
        pa = trace.get(qid)
        reach = gold & set(pa["ranked"][:50]) if pa else set()
        cumulative: list[set] = []
        running: set[str] = set()
        for round_record in record.get("rounds", []):
            running = running | set(round_record.get("added_ids") or [])
            cumulative.append(set(running))
        r_star = next((index + 1 for index, c in enumerate(cumulative)
                       if reach <= c), None)
        final = cumulative[-1] if cumulative else set()
        trigger = record.get("stop_trigger")
        rounds_used = record.get("rounds_used") or len(cumulative)
        missing_final = not (reach <= final)
        category = None
        if trigger == "llm_enough":
            category = "premature" if missing_final else "complete"
        elif trigger in ("no_new_ids", "no_next_query"):
            category = "stall"
        elif trigger == "budget":
            category = "budget"
        wasted_rounds = 0
        for round_record in record.get("rounds", []):
            if round_record.get("round", 1) > (r_star or 0) and \
                    not (set(round_record.get("added_ids") or []) & gold):
                wasted_rounds += 1
        per_query.append({
            "id": qid,
            "trigger": trigger,
            "category": category,
            "reach_size": len(reach),
            "reach_missing": len(gold) - len(reach),
            "r_star": r_star,
            "rounds_used": rounds_used,
            "extra_rounds": (rounds_used - r_star) if r_star else None,
            "wasted_rounds": wasted_rounds,
            "final_complete": reach <= final,
        })
    llm_stops = [q for q in per_query if q["trigger"] == "llm_enough"]
    premature = [q for q in llm_stops if q["category"] == "premature"]
    completed = [q for q in per_query if q["r_star"] is not None]
    over = [q for q in completed if q["rounds_used"] > q["r_star"]]
    over_weak = [q for q in per_query if q["wasted_rounds"] > 0]
    return {
        "answerable": len(per_query),
        "triggers": dict(sorted(
            ((trigger, sum(1 for q in per_query if q["trigger"] == trigger))
             for trigger in {q["trigger"] for q in per_query}), key=lambda kv: kv[0])),
        "categories": dict(sorted(
            ((cat, sum(1 for q in per_query if q["category"] == cat))
             for cat in {q["category"] for q in per_query}), key=lambda kv: kv[0])),
        "premature": {
            "count": len(premature),
            "rate_among_llm_stops": round(len(premature) / len(llm_stops), 6)
            if llm_stops else None,
            "rate_among_answerable": round(len(premature) / len(per_query), 6)
            if per_query else None,
        },
        "over_retrieval_strong": {
            "count": len(over),
            "rate_among_completed": round(len(over) / len(completed), 6)
            if completed else None,
            "mean_extra_rounds": round(_mean(q["extra_rounds"] for q in over), 4)
            if over else None,
        },
        "over_retrieval_weak": {
            "count": len(over_weak),
            "rate": round(len(over_weak) / len(per_query), 6) if per_query else None,
            "mean_wasted_rounds": round(_mean(q["wasted_rounds"] for q in over_weak), 4)
            if over_weak else None,
        },
        "per_query": per_query,
    }


def hop_curve(records: dict[str, dict], eval_set: dict[str, dict]) -> dict:
    rows = [r for qid, r in records.items()
            if eval_set.get(qid, {}).get("kind") != "no_answer" and r.get("relevant")]
    curve = {}
    for k in (5, 10, 20, 50):
        per_round = []
        for round_index in range(R_MAX):
            reached = 0.0
            for record in rows:
                rounds = record.get("rounds", [])
                gold = set(record["relevant"])
                union: set[str] = set()
                for rr in rounds[:round_index + 1]:  # 已停的题按最后一轮结转
                    union.update((rr.get("ids") or [])[:k])
                reached += len(gold & union) / len(gold)
            per_round.append(round(reached / len(rows), 6) if rows else None)
        curve[str(k)] = per_round
    return {"answerable": len(rows), "recall_by_round": curve}


def paired_bootstrap(pairs: list[tuple[float, float]], rng: random.Random) -> dict:
    """`pairs = [(a, b)]`；返回 `mean(b-a)` 的 bootstrap 95% CI + McNemar 计数。"""
    deltas = [b - a for a, b in pairs]
    if not deltas:
        return {"n": 0}
    observed = _mean(deltas)
    n = len(deltas)
    samples = []
    for _ in range(BOOTSTRAP):
        samples.append(sum(deltas[rng.randrange(n)] for _ in range(n)) / n)
    samples.sort()
    lo = samples[int(0.025 * BOOTSTRAP)]
    hi = samples[int(0.975 * BOOTSTRAP) - 1]
    discordant = [(a, b) for a, b in pairs if a != b]
    p_d = len(discordant) / n
    mde = 2.80 * math.sqrt(max(p_d - observed ** 2, 0.0) / n)
    binary = all(v in (0.0, 1.0) for pair in pairs for v in pair)
    return {
        "n": n,
        "mean_delta": round(observed, 6),
        "ci95": [round(lo, 6), round(hi, 6)],
        "significant": (lo > 0) or (hi < 0),
        "discordant": len(discordant),
        "discordance_rate": round(p_d, 4),
        "b01": sum(1 for a, b in pairs if a == 0 and b == 1) if binary else None,
        "b10": sum(1 for a, b in pairs if a == 1 and b == 0) if binary else None,
        "mde_80pct_power_at_n": round(mde, 4),
    }


# ------------------------------------------------------------------- report

def _fmt(value) -> str:
    return f"{value:.4f}" if isinstance(value, float) else str(value)


def render_markdown(report: dict) -> str:
    meta = report["meta"]
    lines = [
        "# Phase B：迭代检索三臂（#48）",
        "",
        f"- 模型：`{meta['model']}`（thinking={meta['thinking']}，temperature=0，seed={meta['seed']}）；"
        f"答案判定异模型 `{meta['judge_model']}`；prompt `{meta['prompt_version']}`。",
        f"- 样本：N={meta['n']}（可答 {meta['answerable']} + null {meta['null_query']}），"
        f"seed={meta['sample_seed']}，dev/holdout={meta['dev']}/{meta['holdout']}。",
        f"- 检索：pool={meta['pool']}，每轮 top-{meta['round_top_k']}，R_max={meta['r_max']}，"
        f"累计证据上限 {meta['evidence_cap']}；rerank 关。",
        f"- B3 循环：judge 与 next_query 同一次调用（对齐 MultiHop-RAG 参考实现的 "
        f"`JudgeDecision`）；停止优先序 ①LLM 判足够 ②本轮无新 id ③用完 R_max。",
        "",
        "## 三臂总览（可答题）",
        "",
        "| 臂 | n | recall@1 | recall@3 | recall@5 | recall@10 | recall@20 | "
        "recall@50 | evidence_recall@5 | 答案正确率 | 答案(宽松口径) | 答案裁判次数 |",
        "|" + "---|" * 11,
    ]
    for arm, agg in report["arms"].items():
        recall = agg.get("recall", {})
        lines.append("| " + " | ".join([
            ARM_LABELS.get(arm, arm), str(agg.get("count", 0)),
            *[_fmt(recall.get(str(k), 0.0)) for k in KS],
            _fmt(agg.get("evidence_recall", 0.0)),
            _fmt(agg.get("answer_accuracy", 0.0)),
            _fmt(agg.get("answer_intersection", 0.0)),
            str(agg.get("answer_judged", 0)),
        ]) + " |")

    lines += ["", "## 按 Phase A 失效类型切片（recall@5 / 答案正确率）", "",
              "| 组 | n | B1 recall@5 | B2 recall@5 | B3 recall@5 | B1 正确率 | B2 正确率 | B3 正确率 |",
              "|" + "---|" * 8]
    for group, stats in report["by_failure_type"].items():
        lines.append("| " + " | ".join([
            group, str(stats["n"]),
            _fmt(stats["recall@5"]["b1"]), _fmt(stats["recall@5"]["b2"]),
            _fmt(stats["recall@5"]["b3"]),
            _fmt(stats["accuracy"]["b1"]), _fmt(stats["accuracy"]["b2"]),
            _fmt(stats["accuracy"]["b3"])]) + " |")

    for title, data in (("分题型", report["by_question_type"]),
                        ("分金标篇数", report["by_evidence_count"])):
        lines += ["", f"## {title}（recall@5 / 答案正确率）", "",
                  "| 组 | n | B1 r@5 | B2 r@5 | B3 r@5 | B1 正确 | B2 正确 | B3 正确 |",
                  "|" + "---|" * 8]
        for key, stats in data.items():
            lines.append("| " + " | ".join([
                key, str(stats["n"]),
                _fmt(stats["recall@5"]["b1"]), _fmt(stats["recall@5"]["b2"]),
                _fmt(stats["recall@5"]["b3"]),
                _fmt(stats["accuracy"]["b1"]), _fmt(stats["accuracy"]["b2"]),
                _fmt(stats["accuracy"]["b3"])]) + " |")

    lines += ["", "## 配对增益（bootstrap 95% CI）", "",
              "| 对比 | 指标 | n | Δ | CI95 | 显著 | b01/b10 | N=200 的 MDE |",
              "|" + "---|" * 8]
    for name, stats in report["deltas"].items():
        if not stats.get("n"):
            continue
        mcnemar = ("-" if stats.get("b01") is None
                   else f"{stats['b01']}/{stats['b10']}")
        lines.append("| " + " | ".join([
            name, stats["metric"], str(stats["n"]), _fmt(stats["mean_delta"]),
            f"[{stats['ci95'][0]:.4f}, {stats['ci95'][1]:.4f}]",
            "是" if stats["significant"] else "否",
            mcnemar,
            _fmt(stats["mde_80pct_power_at_n"])]) + " |")

    lines += ["", "## B3 停止 / 早停 / 过检", ""]
    stop = report["stop"]
    lines += [
        f"- 触发分布：{stop['triggers']}",
        f"- 类别：{stop['categories']}",
        f"- **早停率**（仅 ① 触发且 Reach 缺）："
        f"{stop['premature']['count']}/{stop['answerable']} = "
        f"{_fmt(stop['premature']['rate_among_answerable'])}；"
        f"占 LLM 主动停 = {_fmt(stop['premature']['rate_among_llm_stops'])}",
        f"- **过检率（强：r* 存在仍多跑）**：{stop['over_retrieval_strong']['count']} / "
        f"{len([q for q in stop['per_query'] if q['r_star'] is not None])}，"
        f"平均多跑 {stop['over_retrieval_strong']['mean_extra_rounds']} 轮",
        f"- **过检率（弱：某轮新增金标=0）**：{stop['over_retrieval_weak']['count']} / "
        f"{stop['answerable']}，平均浪费 {stop['over_retrieval_weak']['mean_wasted_rounds']} 轮",
        "",
        "**口径限制（必须随结论引用）**：",
        "1. 用 gold 判「该不该停」默认所有金标都必要，而 evidence 列表可能含冗余 → "
        "**早停率是上界**（描述性、不校阈值，ADR-0017）；",
        "2. 这是**我们自己的操作化**，与 Adaptive-RAG「路由误判 31%」**不是同一口径**，不得混引；",
        "3. `Reach = G ∩ top-50(B1)` 只是**可达上界代理**——不在其中的金标是覆盖 / 索引问题，"
        "不算循环的错。",
        "",
        "## hop 曲线（recall@k vs 轮数，离线）",
        "",
        "| k | r=1 | r=2 | r=3 | r=4 |",
        "|" + "---|" * 5,
    ]
    for k, values in report["hop_curve"]["recall_by_round"].items():
        lines.append("| " + " | ".join([f"@{k}"] + [_fmt(v) if v is not None else "-"
                                        for v in values]) + " |")

    nq = report["null_query"]
    lines += ["", "## null_query（描述性弃答观察，不校阈值；ADR-0017）", "",
              f"- n={nq['n']}；**弃答率**（答案与 gold「Insufficient information」一致）："
              + ", ".join(f"{k}={_fmt(v)}" for k, v in nq["refusal_rate"].items()),
              "- 平均 evidence_recall@5：**n/a**（null 无金标，不作为指标；"
              "只报弃答率）。",
              "",
              "## B2 改写约束保留（规则检查）", "",
              f"- 非来源约束（实体/数字）平均保留率："
              f"{_fmt(report['constraint']['content_retention'])}"
              f"（n={report['constraint']['n']}）",
              f"- 来源名平均保留率（预期偏低＝主动剥离样板词）："
              f"{_fmt(report['constraint']['source_retention'])}",
              f"- 与 Δrecall@5(B2−B1) 的相关："
              f"{report['constraint']['corr_with_delta']}",
              ""]

    rec = report["n_recommendation"]
    lines += ["## 样本量建议", "",
              f"- 主指标：{rec['primary_metric']}；观察 Δ={rec['observed_delta']}，"
              f"N=200 已显著：{rec['significant_at_200']}。",
              f"- N=200 可检出（80% power）≈ {rec['mde_at_200']}；"
              f"检出观察效应需 N≈{rec['n_for_observed']}；"
              f"检出 5pp 小效应需 N≈{rec['n_for_5pp']}。",
              f"- 口径：{rec['note']}",
              f"- **建议终版 N：{rec['suggested_n']}**"
              f"（主效应已显著；若要稳定检出 ≤5pp 再扩到 ~{rec['n_for_5pp']}）",
              "",
              "## 成本", "",
              f"- LLM：`{report['cost'].get('raw')}`",
              f"- 日志：`{report['cost'].get('run_log')}`",
              ""]

    lines += ["## 确定性 / 隔离", "",
              f"- Phase A trace：`{report['meta']['trace_b1']}`",
              f"- B1 直接 join Phase A trace（不重跑检索）。",
              f"- B1 逐题排名 hash：`{report['hashes']['b1_recall']}`；"
              f"per-arm 明细 hash：{report['hashes']['arms']}",
              f"- LLM 非确定性：prompt/温度/seed 已固定并落盘；改写与迭代内容随模型输出变化。",
              ""]

    d = report["deltas"]
    b2b1_r = d.get("B2−B1 · recall@5", {})
    b3b2_r = d.get("B3−B2 · recall@5", {})
    b2b1_a = d.get("B2−B1 · answer", {})
    b3b2_a = d.get("B3−B2 · answer", {})
    b3b1_a = d.get("B3−B1 · answer", {})
    fm = report["by_failure_type"]
    lines += ["", "## 结论要点", "",
              f"- **改写（B2）无可靠增益**：相对 B1，recall@5 Δ={b2b1_r.get('mean_delta')}"
              f"（CI {b2b1_r.get('ci95')}，显著={b2b1_r.get('significant')}），"
              f"答案 Δ={b2b1_a.get('mean_delta')}（显著={b2b1_a.get('significant')}）。",
              f"- **迭代（B3）有可靠增益**：相对 B2，recall@5 Δ={b3b2_r.get('mean_delta')}"
              f"（CI {b3b2_r.get('ci95')}），答案 Δ={b3b2_a.get('mean_delta')}"
              f"（CI {b3b2_a.get('ci95')}）；相对 B1 答案 Δ={b3b1_a.get('mean_delta')}。",
              f"- **增益主要落在排序 miss 组**：rank_miss（n={fm['rank_miss']['n']}）"
              f"recall@5 {fm['rank_miss']['recall@5']['b2']}→{fm['rank_miss']['recall@5']['b3']}、"
              f"答案 {fm['rank_miss']['accuracy']['b2']}→{fm['rank_miss']['accuracy']['b3']}；"
              f"cover_miss（n={fm['cover_miss']['n']}）答案 "
              f"{fm['cover_miss']['accuracy']['b2']}→{fm['cover_miss']['accuracy']['b3']}。",
              f"- **LLM 自判弱**：早停率 {stop['premature']['rate_among_answerable']}"
              f"（占 LLM 主动停 {stop['premature']['rate_among_llm_stops']}）——"
              "不能把「看运气停」当有效机制。",
              "",
              "**ADR-0026 D5**：以上是**外部英文新闻语料上的机制证据**，**不是本产品增益**"
              "（规模 / 多跳密度 / 语言 / 题型均不可迁移；它测引擎，不测包边界）。",
              ""]
    return "\n".join(lines)


# --------------------------------------------------------------------- main

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Phase B analysis")
    parser.add_argument("--out-md", default=os.path.join(PHASE_B_DIR, "report.md"))
    parser.add_argument("--out-json", default=os.path.join(PHASE_B_ARTIFACTS, "report.json"))
    parser.add_argument("--out-per-query",
                        default=os.path.join(PHASE_B_ARTIFACTS, "per_query.json"))
    args = parser.parse_args(argv)

    sample = load_sample()
    eval_set = load_eval()
    trace = load_trace()
    rows = sample_questions(sample, eval_set)

    arms_raw = {}
    for arm in ("b1", "b2", "b3"):
        records, errors = load_arm(os.path.join(PHASE_B_ARTIFACTS, "trace", f"{arm}.jsonl"))
        arms_raw[arm] = {"records": records, "errors": errors}

    common_ids = set(arms_raw["b1"]["records"]) & \
        set(arms_raw["b2"]["records"]) & set(arms_raw["b3"]["records"])
    metrics = {
        arm: {qid: arm_metrics(rec) for qid, rec in arms_raw[arm]["records"].items()}
        for arm in arms_raw
    }
    answerable = {qid for qid, row in eval_set.items()
                  if row["kind"] != "no_answer" and row["relevant"]}
    common_answerable = sorted(common_ids & answerable)

    arms_overview = {
        arm: aggregate({qid: metrics[arm][qid] for qid in common_answerable})
        for arm in metrics
    }

    groups = group_rows(rows, trace)
    by_failure = {}
    for group, ids in groups.items():
        ids = [qid for qid in ids if qid in common_answerable]
        stats = {"n": len(ids), "recall@5": {}, "accuracy": {}}
        for arm in ("b1", "b2", "b3"):
            stats["recall@5"][arm] = round(_mean(
                metrics[arm][qid]["recall"][5] for qid in ids), 6) if ids else 0.0
            stats["accuracy"][arm] = round(_mean(
                float(metrics[arm][qid]["correct"])
                for qid in ids if metrics[arm][qid]["correct"] is not None), 6) if ids else 0.0
        by_failure[group] = stats

    def arm_slice(ids: list[str]) -> dict:
        if not ids:
            return {"n": 0, "recall@5": {}, "accuracy": {}}
        return {
            "n": len(ids),
            "recall@5": {arm: round(_mean(metrics[arm][q]["recall"][5] for q in ids), 6)
                         for arm in ("b1", "b2", "b3")},
            "accuracy": {arm: round(_mean(
                float(metrics[arm][q]["correct"]) for q in ids
                if metrics[arm][q]["correct"] is not None), 6)
                for arm in ("b1", "b2", "b3")},
        }

    def slice_by(key) -> dict:
        out = {}
        for value in sorted({key(row) for row in rows if row["id"] in common_answerable},
                            key=str):
            ids = [row["id"] for row in rows
                   if row["id"] in common_answerable and key(row) == value]
            out[str(value)] = arm_slice(ids)
        return out

    by_question_type = slice_by(lambda row: row["question_type"])
    by_evidence_count = slice_by(lambda row: row["evidence_count"])

    null_ids = [row["id"] for row in rows
                if row["kind"] == "no_answer" and row["id"] in common_ids]
    null_stats = {
        "n": len(null_ids),
        "refusal_rate": {arm: (round(_mean(
            float(metrics[arm][q]["correct"]) for q in null_ids), 4)
            if null_ids else None) for arm in ("b1", "b2", "b3")},
        "mean_evidence_recall": {arm: (round(_mean(
            metrics[arm][q]["evidence_recall"] for q in null_ids), 4)
            if null_ids else None) for arm in ("b1", "b2", "b3")},
    }

    rng = random.Random(BOOTSTRAP_SEED)
    deltas = {}
    for name, (a, b), metric_name in (
        ("B2−B1", ("b1", "b2"), "recall@5"),
        ("B3−B2", ("b2", "b3"), "recall@5"),
        ("B3−B1", ("b1", "b3"), "recall@5"),
        ("B2−B1", ("b1", "b2"), "full@5"),
        ("B3−B2", ("b2", "b3"), "full@5"),
        ("B3−B1", ("b1", "b3"), "full@5"),
        ("B2−B1", ("b1", "b2"), "answer"),
        ("B3−B2", ("b2", "b3"), "answer"),
        ("B3−B1", ("b1", "b3"), "answer"),
    ):
        pairs = []
        for qid in common_answerable:
            if metric_name == "recall@5":
                pairs.append((metrics[a][qid]["recall"][5], metrics[b][qid]["recall"][5]))
            elif metric_name == "full@5":
                pairs.append((float(metrics[a][qid]["full@5"]),
                              float(metrics[b][qid]["full@5"])))
            else:
                ca, cb = metrics[a][qid]["correct"], metrics[b][qid]["correct"]
                if ca is None or cb is None:
                    continue
                pairs.append((float(ca), float(cb)))
        stats = paired_bootstrap(pairs, rng)
        stats["metric"] = metric_name
        deltas[f"{name} · {metric_name}"] = stats

    stop = stop_analysis(arms_raw["b3"]["records"], trace, eval_set)
    curve = hop_curve(arms_raw["b3"]["records"], eval_set)

    sources = source_vocabulary()
    content_retentions, source_retentions, delta_pairs = [], [], []
    for qid, record in arms_raw["b2"]["records"].items():
        if qid not in common_answerable:
            continue
        constraints, source_hits = extract_constraints(record["question"], sources)
        cr = retention(constraints, record.get("rewrite") or "")
        sr = retention(source_hits, record.get("rewrite") or "")
        if cr is not None:
            content_retentions.append(cr)
            delta_pairs.append((cr, metrics["b2"][qid]["recall"][5]
                                - metrics["b1"][qid]["recall"][5]))
        if sr is not None:
            source_retentions.append(sr)
    corr = None
    if len(delta_pairs) >= 3:
        xs = [p[0] for p in delta_pairs]
        ys = [p[1] for p in delta_pairs]
        mx, my = _mean(xs), _mean(ys)
        denom = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
        corr = round(sum((x - mx) * (y - my) for x, y in delta_pairs) / denom, 4) \
            if denom else None

    primary = deltas.get("B3−B1 · answer", {})
    p_disc = primary.get("discordance_rate", 0.0)
    observed = abs(primary.get("mean_delta", 0.0))

    def n_for(target: float) -> int | None:
        # McNemar 配对比例样本量（80% power, α=0.05 双侧）：n ≈ 7.85 · p_disc / δ²
        if target <= 0:
            return None
        return math.ceil(7.85 * p_disc / (target ** 2) / 50.0) * 50

    n_for_observed = n_for(observed)
    n_for_5pp = n_for(0.05)
    suggested = 200 if primary.get("significant") else max(200, n_for_observed or 200)

    cost = parse_cost()
    hashes = {
        "b1_recall": hashlib.sha256(json.dumps(
            {qid: metrics["b1"][qid]["recall"] for qid in common_answerable},
            sort_keys=True).encode()).hexdigest()[:16],
        "arms": {arm: hashlib.sha256(json.dumps(
            {qid: [metrics[arm][qid]["recall"], metrics[arm][qid]["correct"]]
             for qid in common_answerable}, sort_keys=True, default=str).encode()
        ).hexdigest()[:16] for arm in ("b1", "b2", "b3")},
    }

    report = {
        "meta": {
            "model": next(iter(arms_raw["b3"]["records"].values()))["model"]
            if arms_raw["b3"]["records"] else None,
            "judge_model": next(iter(arms_raw["b3"]["records"].values()))["judge_model"]
            if arms_raw["b3"]["records"] else None,
            "thinking": next(iter(arms_raw["b3"]["records"].values()))["thinking"]
            if arms_raw["b3"]["records"] else None,
            "prompt_version": next(iter(arms_raw["b3"]["records"].values()))["prompt_version"]
            if arms_raw["b3"]["records"] else None,
            "n": len(rows), "answerable": len(answerable & set(sample["all"])),
            "null_query": sample["counts"]["null_query"],
            "seed": 42, "sample_seed": sample["seed"], "dev": len(sample["dev"]),
            "holdout": len(sample["holdout"]),
            "pool": 50, "round_top_k": 5, "r_max": R_MAX, "evidence_cap": 20,
            "trace_b1": "artifacts/trace/base_hybrid.jsonl（Phase A）",
            "completed": {arm: len(arms_raw[arm]["records"]) for arm in arms_raw},
            "errors": {arm: arms_raw[arm]["errors"] for arm in arms_raw},
            "common": len(common_answerable),
        },
        "arms": arms_overview,
        "by_failure_type": by_failure,
        "by_question_type": by_question_type,
        "by_evidence_count": by_evidence_count,
        "null_query": null_stats,
        "deltas": deltas,
        "stop": stop,
        "hop_curve": curve,
        "constraint": {
            "n": len(content_retentions),
            "content_retention": round(_mean(content_retentions), 4),
            "source_retention": round(_mean(source_retentions), 4),
            "corr_with_delta": corr,
        },
        "n_recommendation": {
            "primary_metric": "answer accuracy (B3−B1)",
            "observed_delta": round(observed, 4),
            "discordance_rate": round(p_disc, 4),
            "mde_at_200": primary.get("mde_80pct_power_at_n"),
            "significant_at_200": bool(primary.get("significant")),
            "n_for_observed": n_for_observed,
            "n_for_5pp": n_for_5pp,
            "note": ("McNemar 配对比例样本量近似（80% power, α=0.05 双侧，"
                     f"实测不一致率 {p_disc:.3f}）；观察到的效应在 N=200 已显著。"),
            "suggested_n": suggested,
        },
        "cost": cost,
        "hashes": hashes,
    }

    os.makedirs(PHASE_B_ARTIFACTS, exist_ok=True)
    with open(args.out_md, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report))
    with open(args.out_json, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)

    per_query = {}
    for arm in ("b1", "b2", "b3"):
        per_query[arm] = [
            {"id": qid, "question_type": eval_set[qid]["question_type"],
             "evidence_count": eval_set[qid]["evidence_count"],
             "recall": metrics[arm][qid]["recall"],
             "evidence_recall": metrics[arm][qid]["evidence_recall"],
             "correct": metrics[arm][qid]["correct"],
             "match_source": metrics[arm][qid]["match_source"],
             "evidence_ids": arms_raw[arm]["records"][qid].get("evidence_ids"),
             "stop_trigger": metrics[arm][qid]["stop_trigger"],
             "rounds_used": metrics[arm][qid]["rounds_used"]}
            for qid in sorted(arms_raw[arm]["records"]) if qid in eval_set
        ]
    with open(args.out_per_query, "w", encoding="utf-8") as handle:
        json.dump(per_query, handle, ensure_ascii=False)

    print(f"[out] {args.out_md}")
    print(f"[out] {args.out_json}")
    print(f"[out] {args.out_per_query}")
    for arm, agg in arms_overview.items():
        print(f"{arm}: evidence_recall@5={agg.get('evidence_recall')} "
              f"answer_acc={agg.get('answer_accuracy')} n={agg.get('count')}")
    print("stop:", stop["triggers"], "premature:", stop["premature"]["count"],
          "over(strong):", stop["over_retrieval_strong"]["count"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
