"""Lead 分析：A″ 的「停止判据是不是瓶颈」——按停止触发器分层，量早停的代价。

只读已入库的产物（`control_rankings.json` + 本地 trace），不调 LLM。

    python lead_stop_analysis.py --phase-a2 <dir>

输出：
1. 按 stop 触发器分层：n / A1 召回 / C15 召回（= 同额度的天花板）/ 差额 / 平均展示条数；
2. 「模型自己说够了、但 gold 没齐」的题数（= 早停直接造成损失的题数）；
3. 候选「经典案例」：trigger=answer 且只跑 1 轮、gold 缺失、缺失的 gold 就在首轮 top-6..15
   （即"再多看几条就拿到了"）。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def nid(value):
    text = str(value)
    return int(text.split(":")[1]) if ":" in text else int(text)


def recall_at(ranked, relevant, k):
    if not relevant:
        return None
    gold = set(nid(x) for x in relevant)
    return len(gold & set(nid(x) for x in ranked[:k])) / len(gold)


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def r(value):
    return None if value is None else round(value, 4)


def main(argv=None):
    parser = argparse.ArgumentParser(description="A″ early-stop analysis")
    parser.add_argument("--phase-a2", default=None)
    args = parser.parse_args(argv)
    phase = args.phase_a2 or os.path.join(
        os.path.dirname(os.path.abspath(__file__)))
    census = os.path.dirname(os.path.dirname(phase))

    rows = {row["id"]: row for row in
            load_json(os.path.join(phase, "artifacts", "control_rankings.json"))["rows"]}
    records = {}
    with open(os.path.join(phase, "artifacts", "trace", "a1_traces.jsonl"),
              "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                records[record["id"]] = record
    ids = list(rows)

    def trigger(qid):
        stop = records[qid]["trace"].get("stop")
        return stop.get("trigger") if isinstance(stop, dict) else stop

    completed = [q for q in ids if q in records and not records[q].get("error")]
    answerable = [q for q in completed if rows[q]["relevant"]]

    per = {}
    for qid in answerable:
        trace = records[qid]["trace"]
        shown = [nid(x) for x in (trace.get("final") or {}).get("evidence_ids") or []]
        gold = set(nid(x) for x in rows[qid]["relevant"])
        per[qid] = {
            "trigger": trigger(qid),
            "rounds": len(trace.get("rounds") or []),
            "shown": shown,
            "n_shown": len(shown),
            "gold": gold,
            "missing": sorted(gold - set(shown)),
            "a1": recall_at(rows[qid]["ranked"], rows[qid]["relevant"], 10 ** 6)
                  if False else len(gold & set(shown)) / len(gold),
            "c15": recall_at(rows[qid]["ranked"], rows[qid]["relevant"], 15),
            "c20": recall_at(rows[qid]["ranked"], rows[qid]["relevant"], 20),
            "first_ranking": [nid(x) for x in rows[qid]["ranked"]],
            "type": rows[qid].get("question_type"),
        }

    print("=== 1. 按 stop 触发器分层（n=176 可答）===")
    print(f"{'trigger':<14}{'n':>4}{'A1':>9}{'C15':>9}{'差(C15-A1)':>12}"
          f"{'A1展示条数':>11}{'只跑1轮':>9}")
    for name in ("answer", "no_new_ids", "budget", "fallback", "insufficient"):
        group = [q for q in answerable if per[q]["trigger"] == name]
        if not group:
            continue
        a1 = mean([per[q]["a1"] for q in group])
        c15 = mean([per[q]["c15"] for q in group])
        shown = mean([per[q]["n_shown"] for q in group])
        one = sum(1 for q in group if per[q]["rounds"] == 1)
        print(f"{name:<14}{len(group):>4}{a1:>9.4f}{c15:>9.4f}{c15 - a1:>12.4f}"
              f"{shown:>11.2f}{one:>9}")

    print("\n=== 2. 「模型自己说够了 / 判定没新东西」但 gold 没齐 = 早停的直接代价 ===")
    for name in ("answer", "no_new_ids"):
        group = [q for q in answerable if per[q]["trigger"] == name]
        short = [q for q in group if per[q]["missing"]]
        print(f"  {name}: {len(group)} 题，其中 gold 没齐 {len(short)} 题 "
              f"({len(short) / len(group):.1%})；"
              f"缺口 = gold 还差 {sum(len(per[q]['missing']) for q in short)} 个槽位")
    early = [q for q in answerable if per[q]["trigger"] in ("answer", "no_new_ids")]
    late = [q for q in answerable if per[q]["trigger"] == "budget"]
    print(f"  早停层 n={len(early)}：A1 {mean([per[q]['a1'] for q in early]):.4f} / "
          f"C15 天花板 {mean([per[q]['c15'] for q in early]):.4f} → 差额 "
          f"{mean([per[q]['c15'] for q in early]) - mean([per[q]['a1'] for q in early]):.4f}")
    print(f"  用满预算层 n={len(late)}：A1 {mean([per[q]['a1'] for q in late]):.4f} / "
          f"C15 天花板 {mean([per[q]['c15'] for q in late]):.4f} → 差额 "
          f"{mean([per[q]['c15'] for q in late]) - mean([per[q]['a1'] for q in late]):.4f}")

    print("\n=== 3. 经典案例候选（answer 且只跑 1 轮、gold 缺失、缺失项就在首轮 top-6..15）===")
    cases = []
    for qid in answerable:
        row = per[qid]
        if row["trigger"] != "answer" or row["rounds"] != 1:
            continue
        if not row["missing"] or not row["shown"]:
            continue
        ranks = {entry: index + 1 for index, entry in enumerate(row["first_ranking"])}
        just_outside = [entry for entry in row["missing"] if 6 <= ranks.get(entry, 999) <= 15]
        if just_outside:
            cases.append((row["c15"] - row["a1"], qid, just_outside, ranks))
    cases.sort(reverse=True)
    print(f"  符合「只看了一轮 + 明确作答 + 缺的 gold 就在首轮第 6–15 名」的题数：{len(cases)}")
    for gap, qid, just_outside, ranks in cases[:5]:
        row = per[qid]
        print(f"\n  --- {qid}（{row['type']}，gold {len(row['gold'])} 篇，"
              f"缺口 {gap:.3f}）---")
        print(f"    展示 {row['n_shown']} 条："
              f"{[f'{r}#{ranks.get(r)}' for r in row['shown']]}")
        print(f"    缺的 gold：{[(f'{r}#{ranks.get(r)}') for r in row['missing']]}")
        print(f"    首轮就落在 6–15 名的：{just_outside}")
    if cases:
        gap, qid, just_outside, ranks = cases[0]
        trace = records[qid]["trace"]
        print(f"\n=== 4. 上面第一条的完整轨迹（{qid}）===")
        print(f"  问题：{trace.get('query')}")
        for rnd in trace.get("rounds") or []:
            calls = [(c.get("tool"), c.get("args")) for c in (rnd.get("tool_calls") or [])]
            print(f"  第 {rnd.get('round')} 轮：query={rnd.get('query')!r}")
            print(f"     工具调用：{calls}")
            print(f"     展示的 id：{[(x, ranks.get(nid(x))) for x in rnd.get('added_ids') or []]}")
            out = (rnd.get("model_output") or "").strip().replace("\n", " | ")
            print(f"     模型输出：{out[:600]}")
        print(f"  最终答案：{(trace.get('final') or {}).get('answer')!r}")
        print(f"  缺的 gold 在首轮的排名："
              f"{[(x, ranks.get(x)) for x in per[qid]['missing']]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
