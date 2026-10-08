"""Lead 分析二：迭代的**边际收益**——第 2/3 轮到底带来了多少新证据。

只读已入库产物，不调 LLM。回答的问题是：
「早停」是**原因**，还是**结果**（即：即使不早停，后续轮次本来就拿不到新东西）？

    python lead_round_yield.py
"""
from __future__ import annotations

import argparse
import json
import os


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def nid(value):
    text = str(value)
    return int(text.split(":")[1]) if ":" in text else int(text)


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else 0.0


def main(argv=None):
    parser = argparse.ArgumentParser(description="A″ per-round marginal yield")
    parser.add_argument("--phase-a2", default=os.path.dirname(os.path.abspath(__file__)))
    args = parser.parse_args(argv)
    phase = args.phase_a2

    rows = {r["id"]: r for r in load_json(
        os.path.join(phase, "artifacts", "control_rankings.json"))["rows"]}
    records = {}
    with open(os.path.join(phase, "artifacts", "trace", "a1_traces.jsonl"),
              "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rec = json.loads(line)
                records[rec["id"]] = rec

    answerable = [q for q in rows
                  if q in records and not records[q].get("error") and rows[q]["relevant"]]

    # 逐题逐轮
    per_round = {1: [], 2: [], 3: []}
    saved_by_later = 0          # 首轮 0 gold、后续轮拿到 gold 的题
    first_round_zero = 0
    later_gold_total = 0
    gold_total = 0
    dup_query = 0
    zero_rounds = 0
    for qid in answerable:
        trace = records[qid]["trace"]
        gold = set(nid(x) for x in rows[qid]["relevant"])
        gold_total += len(gold)
        seen = set()
        queries = []
        per_q_later = 0
        for rnd in trace.get("rounds") or []:
            index = int(rnd.get("round") or 0)
            for call in rnd.get("tool_calls") or []:
                call_args = call.get("args") or {}
                query = str(call_args.get("query") or "")
                if query:
                    if query in queries:
                        dup_query += 1
                    queries.append(query)
                added = [nid(x) for x in (call.get("added_ids") or [])]
                if index in per_round:
                    new_gold = [x for x in added if x in gold and x not in seen]
                    per_round[index].append({
                        "qid": qid, "added": len(added), "new_gold": len(new_gold),
                        "trigger": (trace.get("stop") or {}).get("trigger")
                        if isinstance(trace.get("stop"), dict) else trace.get("stop"),
                    })
                    if index > 1:
                        per_q_later += len(new_gold)
                    if not added and call.get("tool") == "memory_search":
                        zero_rounds += 1
                seen.update(added)
        first_round_gold = len([x for x in seen if x in gold])  # 仅诊断用
        del first_round_gold
        g1 = sum(1 for r in per_round[1] if r["qid"] == qid for _ in range(r["new_gold"]))
        if g1 == 0:
            first_round_zero += 1
            if per_q_later > 0:
                saved_by_later += 1
        later_gold_total += per_q_later

    print(f"n = {len(answerable)} 可答；gold 槽位合计 {gold_total}")
    print(f"首轮一条 gold 都没拿到的题：{first_round_zero}；"
          f"其中被后续轮**救回**的：{saved_by_later}")
    print(f"后续轮（2/3）贡献的新 gold 槽位：{later_gold_total} / {gold_total} "
          f"= {later_gold_total / gold_total:.1%}")
    print(f"`memory_search` 返回 0 新条目的轮次：{zero_rounds}；"
          f"同一题内 query 重复出现的次数：{dup_query}")
    print()
    print(f"{'轮':<4}{'到达该轮的题':>10}{'平均新增条目':>12}{'平均新增gold':>12}"
          f"{'新增 gold 的题占比':>16}")
    for index in (1, 2, 3):
        group = per_round[index]
        if not group:
            continue
        qids = {r["qid"] for r in group}
        print(f"{index:<4}{len(qids):>10}{mean([r['added'] for r in group]):>12.2f}"
              f"{mean([r['new_gold'] for r in group]):>12.2f}"
              f"{sum(1 for r in group if r['new_gold']) / len(group):>15.1%}")
    print()
    print("按停止原因看「后续轮贡献」：")
    for name in ("answer", "no_new_ids", "budget", "fallback"):
        qids = [q for q in answerable
                if ((records[q]["trace"].get("stop") or {}).get("trigger")
                    if isinstance(records[q]["trace"].get("stop"), dict)
                    else records[q]["trace"].get("stop")) == name]
        if not qids:
            continue
        later = 0
        total = 0
        rounds_seen = 0
        for qid in qids:
            gold = set(nid(x) for x in rows[qid]["relevant"])
            total += len(gold)
            trace = records[qid]["trace"]
            seen = set()
            rounds_seen += len(trace.get("rounds") or [])
            for rnd in trace.get("rounds") or []:
                index = int(rnd.get("round") or 0)
                for call in rnd.get("tool_calls") or []:
                    added = [nid(x) for x in (call.get("added_ids") or [])]
                    if index > 1:
                        later += len([x for x in added if x in gold and x not in seen])
                    seen.update(added)
        print(f"  {name:<12} n={len(qids):>3}  轮数合计 {rounds_seen:>3}  "
              f"后续轮贡献 gold {later}/{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
