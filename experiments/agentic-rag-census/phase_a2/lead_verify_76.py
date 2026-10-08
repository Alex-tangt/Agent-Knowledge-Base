"""Lead 独立复核（#76 / A‴）：不复用 `a3_*.py` 的任何计算，自己重算头条数字。

只读：主树的 `a1_traces.jsonl` + `data/corpus.json`；worktree 的 `a3_grades.jsonl`
+ `control_rankings.json`。零 LLM。

    python lead_verify_76.py --phase-a2 <worktree phase_a2> --main-census <main census dir>
"""
from __future__ import annotations

import argparse
import json
import os


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl(path):
    out = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rec = json.loads(line)
                out[rec.get("id")] = rec
    return out


def nid(value):
    text = str(value)
    return int(text.split(":")[1]) if ":" in text else int(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lead independent verification (#76)")
    parser.add_argument("--phase-a2", required=True)
    parser.add_argument("--main-census", required=True)
    args = parser.parse_args(argv)

    rows = {r["id"]: r for r in load_json(
        os.path.join(args.phase_a2, "artifacts", "control_rankings.json"))["rows"]}
    traces = read_jsonl(os.path.join(args.main_census,
                                     "phase_a2", "artifacts", "trace",
                                     "a1_traces.jsonl"))
    grades = read_jsonl(os.path.join(args.phase_a2, "artifacts", "trace",
                                     "a3_grades.jsonl"))
    corpus = load_json(os.path.join(args.main_census, "data", "corpus.json"))
    source_of = {i: (a.get("source") or "").strip() for i, a in enumerate(corpus)}

    answerable = [q for q in rows if rows[q]["relevant"]]
    out = {}

    # ---------------------------------------------------------- 1. 答案存在性
    by_trigger = {}
    for qid in answerable:
        trace = traces.get(qid, {}).get("trace") or {}
        stop = trace.get("stop")
        trigger = stop.get("trigger") if isinstance(stop, dict) else stop
        answer = (trace.get("final") or {}).get("answer")
        slot = by_trigger.setdefault(trigger, {"n": 0, "with_answer": 0})
        slot["n"] += 1
        if answer:
            slot["with_answer"] += 1
    total_with = sum(v["with_answer"] for v in by_trigger.values())
    out["answers_by_trigger"] = by_trigger
    out["answerable_with_answer"] = total_with
    out["answerable_no_answer"] = len(answerable) - total_with

    # ---------------------------------------------------------- 2. 2x2（我校自己的）
    def evidence_of(qid):
        trace = traces.get(qid, {}).get("trace") or {}
        return [nid(x) for x in (trace.get("final") or {}).get("evidence_ids") or []]

    table = {"complete": {"correct": 0, "wrong": 0},
             "incomplete": {"correct": 0, "wrong": 0}}
    three = {"complete": {"correct": 0, "wrong": 0, "no_answer": 0},
             "incomplete": {"correct": 0, "wrong": 0, "no_answer": 0}}
    match_sources = {}
    blinding_correct = {"n": 0, "correct": 0}
    for qid in answerable:
        gold = set(nid(x) for x in rows[qid]["relevant"])
        hit = gold & set(evidence_of(qid))
        key = "complete" if len(hit) == len(gold) else "incomplete"
        grade = grades.get(qid)
        has_answer = bool((traces.get(qid, {}).get("trace") or {}).get("final", {})
                          .get("answer"))
        if grade is None:
            three[key]["no_answer" if not has_answer else "wrong"] += 1
            continue
        source = grade.get("match_source")
        match_sources[source] = match_sources.get(source, 0) + 1
        if grade.get("correct"):
            table[key]["correct"] += 1
            three[key]["correct"] += 1
        else:
            table[key]["wrong"] += 1
            three[key]["wrong"] += 1
    out["two_by_two"] = table
    out["three_result_all176"] = three
    out["match_sources"] = match_sources

    # ---------------------------------------------------------- 3. 来源约束（两种口径都算）
    #
    # 定义 A（宽）："该来源被代表了吗" —— 证据里有没有**任何**一篇来自该来源的文章。
    # 定义 B（严）："该来源的 **gold 篇**进证据了吗" —— 该来源的 gold 文章一篇都没进 = 未核验。
    # 两定义对"pairs"计数相同（= 该来源在 gold 里有文章），差异只在 blinding 判定。
    vocabulary = sorted({name for name in source_of.values() if name},
                        key=len, reverse=True)
    stats = {arm: {kind: {"pairs": 0, "blinding": 0, "questions": 0, "blind_questions": 0}
                   for kind in ("A_any_article", "B_gold_article")}
             for arm in ("A1", "C15")}
    blind_ids = {"A1": {"A_any_article": set(), "B_gold_article": set()},
                 "C15": {"A_any_article": set(), "B_gold_article": set()}}
    for qid in answerable:
        question = ((traces.get(qid, {}).get("trace") or {}).get("query") or "").lower()
        named = [name for name in vocabulary if name.lower() in question]
        if not named:
            continue
        gold = set(nid(x) for x in rows[qid]["relevant"])
        considered = False
        for arm, shown in (("A1", set(evidence_of(qid))),
                           ("C15", set(nid(x) for x in rows[qid]["ranked"][:15]))):
            shown_sources = {source_of.get(i) for i in shown}
            blind_any = False
            blind_gold = False
            for name in named:
                gold_here = {i for i in gold if source_of.get(i) == name}
                if not gold_here:
                    continue
                considered = True
                stats[arm]["A_any_article"]["pairs"] += 1
                stats[arm]["B_gold_article"]["pairs"] += 1
                if name not in shown_sources:
                    stats[arm]["A_any_article"]["blinding"] += 1
                    blind_any = True
                if not (gold_here & shown):
                    stats[arm]["B_gold_article"]["blinding"] += 1
                    blind_gold = True
            stats[arm]["A_any_article"]["questions"] += 1
            stats[arm]["B_gold_article"]["questions"] += 1
            if blind_any:
                stats[arm]["A_any_article"]["blind_questions"] += 1
                blind_ids[arm]["A_any_article"].add(qid)
            if blind_gold:
                stats[arm]["B_gold_article"]["blind_questions"] += 1
                blind_ids[arm]["B_gold_article"].add(qid)
        del considered
    out["source_constraint"] = stats

    # ---------------------------------------------------------- 4. 致盲 x 答案（两种口径）
    cross = {}
    for kind in ("A_any_article", "B_gold_article"):
        n = correct = 0
        for qid in blind_ids["A1"][kind]:
            if qid in grades:
                n += 1
                if grades[qid].get("correct"):
                    correct += 1
        cross[kind] = {"n": n, "correct": correct}
    out["blinding_x_correct"] = cross

    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
