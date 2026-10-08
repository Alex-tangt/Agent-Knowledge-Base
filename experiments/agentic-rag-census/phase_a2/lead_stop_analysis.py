"""Lead 分析：A″ 的「停止判据是不是瓶颈」——按停止触发器分层 + 导出**单题完整案例**。

只读已入库的产物（`control_rankings.json` + 本地 trace + `data/corpus.json`），不调 LLM。

    # 分层统计
    python lead_stop_analysis.py
    # 导出某一题的完整案例（markdown；含数据集原文 → 落 gitignored 目录）
    python lead_stop_analysis.py --case mhr2475 --out <path>.md
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


def recall_at(ranked, relevant, k):
    if not relevant:
        return None
    gold = set(nid(x) for x in relevant)
    return len(gold & set(nid(x) for x in ranked[:k])) / len(gold)


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


class Data:
    """一次装载：控制臂排名 + 轨迹 + 语料标题 + 评测集。"""

    def __init__(self, phase):
        self.phase = phase
        self.census = os.path.dirname(phase)
        self.rows = {row["id"]: row for row in load_json(
            os.path.join(phase, "artifacts", "control_rankings.json"))["rows"]}
        self.records = {}
        with open(os.path.join(phase, "artifacts", "trace", "a1_traces.jsonl"),
                  "r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    record = json.loads(line)
                    self.records[record["id"]] = record
        self.ids = list(self.rows)
        self.completed = [q for q in self.ids
                          if q in self.records and not self.records[q].get("error")]
        self.answerable = [q for q in self.completed if self.rows[q]["relevant"]]
        corpus_path = os.path.join(self.census, "data", "corpus.json")
        self.titles = {}
        if os.path.isfile(corpus_path):
            for index, article in enumerate(load_json(corpus_path)):
                self.titles[index] = (article.get("title") or "").strip()
        self.per = {qid: self._one(qid) for qid in self.answerable}

    def _one(self, qid):
        trace = self.records[qid]["trace"]
        shown = [nid(x) for x in (trace.get("final") or {}).get("evidence_ids") or []]
        gold = set(nid(x) for x in self.rows[qid]["relevant"])
        stop = trace.get("stop")
        return {
            "trigger": stop.get("trigger") if isinstance(stop, dict) else stop,
            "rounds": len(trace.get("rounds") or []),
            "shown": shown,
            "n_shown": len(shown),
            "gold": gold,
            "missing": sorted(gold - set(shown)),
            "a1": len(gold & set(shown)) / len(gold) if gold else None,
            "c15": recall_at(self.rows[qid]["ranked"], self.rows[qid]["relevant"], 15),
            "first_ranking": [nid(x) for x in self.rows[qid]["ranked"]],
            "type": self.rows[qid].get("question_type"),
        }

    def title(self, index):
        return self.titles.get(index) or "_(标题缺失)_"

    def rank_of(self, qid, index):
        return {entry: pos + 1
                for pos, entry in enumerate(self.per[qid]["first_ranking"])}.get(index)


def markdown_case(data: Data, qid: str) -> str:
    row = data.per[qid]
    trace = data.records[qid]["trace"]
    ranks = {entry: pos + 1
             for pos, entry in enumerate(row["first_ranking"])}
    gold = sorted(row["gold"])
    lines = [
        f"# A″ 单题案例：`{qid}`（{row['type']}，gold = {len(gold)} 篇）",
        "",
        "> **含数据集原文**（MultiHop-RAG，ODC-BY，`yixuantt/MultiHopRAG`）→",
        "> 本文件落在 **gitignored** 的 `artifacts/trace/` 下，**不入库**。",
        "> 生成：`python lead_stop_analysis.py --case " + qid + " --out <path>`（只读、不调 LLM）",
        "",
        "## 1. 问题（数据集原文）",
        "",
        f"> {trace.get('query')}",
        "",
        "## 2. 该题的标准答案 = 必须被找到的 " + str(len(gold)) + " 篇文章",
        "",
        "| # | 条目 | 标题 | 首轮排名 |",
        "|---|---|---|---|",
    ]
    for position, index in enumerate(gold, start=1):
        lines.append(f"| {position} | `multihop:{index:04d}` | {data.title(index)} | "
                     f"第 {ranks.get(index)} 名 |")

    lines += [
        "",
        f"## 3. 实际发生了什么（停止原因 = `{row['trigger']}`，共 {row['rounds']} 轮）",
        "",
    ]
    for rnd in trace.get("rounds") or []:
        lines.append(f"### 第 {rnd.get('round')} 轮")
        lines.append("")
        lines.append(f"- 这一轮使用的检索 query：`{rnd.get('query')}`")
        for call in rnd.get("tool_calls") or []:
            lines.append(f"- 工具调用：`{call.get('tool')}` 参数 `{call.get('args')}`")
        added = [nid(x) for call in (rnd.get("tool_calls") or [])
                 for x in (call.get("added_ids") or [])]
        lines.append(f"- 本轮新展示的条目（{len(added)} 条）：")
        for index in added:
            flag = "**gold**" if index in row["gold"] else "非 gold"
            lines.append(f"  - `multihop:{index:04d}`（首轮第 {ranks.get(index)} 名，{flag}）"
                         f" — {data.title(index)}")
        output = (rnd.get("model_output") or "").strip()
        lines += ["", "模型这一轮的输出：", "", "```", output, "```", ""]

    lines += [
        "## 4. 结果",
        "",
        f"- 证据召回 = **{row['a1']:.4f}**（{len(row['gold']) - len(row['missing'])}"
        f"/{len(row['gold'])} 篇 gold 被展示）",
        f"- 最终答案：`{(trace.get('final') or {}).get('answer')}`",
        "",
        "**没被展示的 gold 文章**（= 召回缺口）：",
        "",
        "| 条目 | 标题 | 首轮排名 | 距「再多看一眼」多远 |",
        "|---|---|---|---|",
    ]
    for index in row["missing"]:
        rank = ranks.get(index)
        note = ("在首轮 6–15 名内（多跑一步 / 把 k 调到 15 就能拿到）"
                if rank and 6 <= rank <= 15 else
                f"首轮第 {rank} 名（超出 15，需改写 query）" if rank and rank > 15 else
                "不在首轮前 50（属覆盖问题）")
        lines.append(f"| `multihop:{index:04d}` | {data.title(index)} | 第 {rank} 名 | {note} |")

    lines += [
        "",
        "## 5. 反事实：如果一次性取前 15 条",
        "",
        "| 首轮排名 | 条目 | gold? | 标题 |",
        "|---|---|---|---|",
    ]
    for position, index in enumerate(row["first_ranking"][:15], start=1):
        lines.append(f"| {position} | `multihop:{index:04d}` | "
                     f"{'✅' if index in row['gold'] else ''} | {data.title(index)} |")
    lines += [
        "",
        f"→ 一次性 top-15 的召回 = **{row['c15']:.4f}**；agent 实际 = **{row['a1']:.4f}**。",
        "",
        "## 6. 成因",
        "",
        "运行时的充分性判据是「**模型给出了终结回复**」（`ANSWER:` / `INSUFFICIENT`）——",
        "它回答的是「**我能不能答**」；而本题的评分要求是「**这 " + str(len(gold)) + " 篇证据齐不齐**」。",
        "",
        "这道题上两者正好分岔：第 1 轮命中的条目已经足够让模型**答出**标准答案实体，",
        "于是它在只拿到 "
        f"{len(row['gold']) - len(row['missing'])}/{len(row['gold'])} 证据时就宣布完成，"
        f"循环随之结束（停止原因 `{row['trigger']}`）。",
        "",
        "**不是模型答错了，是判据与指标不对齐**：它因为「答案有了」而停，不是因为「证据够了」而停。",
        "",
        "## 7. 原始轨迹（可审计）",
        "",
        "```json",
        json.dumps(trace, ensure_ascii=False, indent=1)[:12000],
        "```",
        "",
    ]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="A″ early-stop analysis")
    parser.add_argument("--phase-a2", default=os.path.dirname(os.path.abspath(__file__)))
    parser.add_argument("--case", default=None, help="导出该题 id 的完整案例 markdown")
    parser.add_argument("--out", default=None, help="案例输出路径")
    args = parser.parse_args(argv)

    data = Data(args.phase_a2)
    answerable = data.answerable

    if args.case:
        if args.case not in data.per:
            raise SystemExit(f"未知 / 不可答的题 id：{args.case}")
        text = markdown_case(data, args.case)
        out = args.out or os.path.join(
            data.phase, "artifacts", "trace", f"case_{args.case}.md")
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"[out] {out}  ({len(text)} chars)")
        return 0

    per = data.per
    print("=== 1. 按 stop 触发器分层（n=%d 可答）===" % len(answerable))
    print(f"{'trigger':<14}{'n':>4}{'A1':>9}{'C15':>9}{'gap':>9}{'shown':>7}{'1轮':>5}")
    for name in ("answer", "no_new_ids", "budget", "fallback", "insufficient"):
        group = [q for q in answerable if per[q]["trigger"] == name]
        if not group:
            continue
        a1 = mean([per[q]["a1"] for q in group])
        c15 = mean([per[q]["c15"] for q in group])
        print(f"{name:<14}{len(group):>4}{a1:>9.4f}{c15:>9.4f}{c15 - a1:>9.4f}"
              f"{mean([per[q]['n_shown'] for q in group]):>7.2f}"
              f"{sum(1 for q in group if per[q]['rounds'] == 1):>5}")

    print("\n=== 2. 早停的直接代价 ===")
    for name in ("answer", "no_new_ids"):
        group = [q for q in answerable if per[q]["trigger"] == name]
        short = [q for q in group if per[q]["missing"]]
        print(f"  {name}: {len(group)} 题，gold 没齐 {len(short)} 题"
              f"（{len(short) / len(group):.1%}），共缺 "
              f"{sum(len(per[q]['missing']) for q in short)} 个槽位")
    early = [q for q in answerable if per[q]["trigger"] in ("answer", "no_new_ids")]
    late = [q for q in answerable if per[q]["trigger"] == "budget"]
    for label, group in (("早停层", early), ("用满预算层", late)):
        a1 = mean([per[q]["a1"] for q in group])
        c15 = mean([per[q]["c15"] for q in group])
        print(f"  {label} n={len(group)}：A1 {a1:.4f} / 天花板 {c15:.4f} → 差 {c15 - a1:.4f}")

    cases = []
    for qid in answerable:
        row = per[qid]
        if row["trigger"] != "answer" or row["rounds"] != 1 or not row["missing"]:
            continue
        ranks = {entry: pos + 1 for pos, entry in enumerate(row["first_ranking"])}
        just_outside = [e for e in row["missing"] if 6 <= ranks.get(e, 999) <= 15]
        if just_outside:
            cases.append((row["c15"] - row["a1"], qid, just_outside))
    cases.sort(reverse=True)
    print(f"\n=== 3. 「只跑 1 轮就作答 + 缺的 gold 落在首轮第 6–15 名」共 {len(cases)} 题 ===")
    for gap, qid, _ in cases[:10]:
        print(f"  {qid}  缺口 {gap:.3f}  A1 {per[qid]['a1']:.2f}")
    if cases:
        print(f"\n导出首例：python lead_stop_analysis.py --case {cases[0][1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
