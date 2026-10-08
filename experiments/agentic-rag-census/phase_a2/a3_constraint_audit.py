r"""A‴ 范围 A（ticket #76）：**来源约束**离线核验 —— 零 LLM、纯读文件。

问的是什么（owner 的原话）：问题**显式写了来源约束**（如 "as reported by The Verge and
TechCrunch"），当**缺的那篇 gold 正是该来源的稿件**时，agent 有没有去核这个约束？
本脚本把"约束是否被检索满足"变成可复算的数字。

口径（全部写进报告）：

- **来源词表** = `data/corpus.json` 每篇的 `source` 字段（49 个不同值）；`multihop:<i:04d>`
  ↔ 第 i 篇（`multihop.build_eval_set` 的映射）。
- **别名**：对每个来源名取三个前缀变体（`" | "` / `" - "` / `": "` 之前的部分），
  例如 `"Cnbc | World Business News Leader"` → `Cnbc`、`"FOX News - Technology"` → `FOX News`。
  长度 < 3 的变体丢弃。一个别名可映射到多个来源（`BBC News` → 两个 feed）。
- **匹配规则**：问题文本 lower() 后按 **ASCII 词边界**做大小写不敏感的全词匹配
  （`(?<![a-z0-9])alias(?![a-z0-9])`）；同一来源在一题里只计一次。
  这是**机械规则**，不做语义判断（既不识别 "CBS Sports" ↔ `CBSSports.com` 这类改写，
  也不排除同形异义词）——边界写进报告。
- **分母**：D1 = 176 道可答题；D2 = 其中含 ≥1 显式来源提及的题；D3 = (题 × 来源) 提及对。
- **致盲风险面（blind risk）**：某个被提及来源在 **gold** 里有文章，但该来源的文章在
  **证据集**里一篇都没有 → 约束"被说了却没被核"。证据集取两臂：
  `A1` = `trace.final.evidence_ids`；`C15` = `control_rankings.json` 的 `ranked[:15]`
  （一次性 top-15 = A1 的实际展示额度；#74 report.md §2）。
- 另报**文章级**：被提及来源的 gold 文章里，有多少篇不在证据集里。

复跑（零 LLM，秒级）：

    $py = "D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe"
    & $py experiments/agentic-rag-census/phase_a2/a3_constraint_audit.py `
        --census-dir "D:\python_work\work2026-4\Agent-Knowledge-Base\experiments\agentic-rag-census"

- **只读**主树：`data/corpus.json` / `data/MultiHopRAG.json` / `phase_b/sample.json` /
  `phase_a2/artifacts/{control_rankings.json,trace/a1_traces.jsonl}`；
  **绝不调用** `multihop.ensure_prepared()`（会往 worktree 重写 `data/articles/`）。
- 输出落**本脚本所在目录**的 `artifacts/trace/a3_constraint.json`（gitignored 中间产物），
  由 `a3_grade_answers.py --report` 合并进 `a3_results.json`。
- 入库产物**不含数据集原文**（问题 / 答案 / 正文一律不落）：只存 id、来源名（站点名，非原文）、
  布尔与计数。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CENSUS_WT = os.path.dirname(HERE)                  # worktree 内的 census 目录（代码）
REPO_ROOT = os.path.dirname(os.path.dirname(CENSUS_WT))

for _path in (CENSUS_WT, REPO_ROOT):               # 顺序：REPO_ROOT 最后插入 → 排最前
    if _path not in sys.path:
        sys.path.insert(0, _path)

import multihop as mh  # noqa: E402

A3_TRACE_DIR = os.path.join(HERE, "artifacts", "trace")
DEFAULT_OUT = os.path.join(A3_TRACE_DIR, "a3_constraint.json")
PHASE_A2_ARTIFACTS_REL = "phase_a2/artifacts"
ARMS = ("A1", "C15")
C15_K = 15

MIN_ALIAS_LEN = 3


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def load_json(path: str):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=1)


def read_jsonl_last_wins(path: str) -> dict[str, dict]:
    """逐行 JSONL → `{id: record}`（同一 id 多行时**后写者胜**，抄 run_a2）。"""
    out: dict[str, dict] = {}
    if not os.path.isfile(path):
        return out
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("id"):
                out[str(record["id"])] = record
    return out


def main_worktree() -> str:
    """主工作树路径（`git worktree list --porcelain` 第一项；抄 run_a2._main_worktree）。"""
    try:
        proc = subprocess.run(["git", "-C", REPO_ROOT, "worktree", "list", "--porcelain"],
                              capture_output=True, text=True)
    except OSError:
        return REPO_ROOT
    for line in (proc.stdout or "").splitlines():
        if line.startswith("worktree "):
            return line.split(" ", 1)[1].strip()
    return REPO_ROOT


# ------------------------------------------------------------------ 来源词表

def aliases_of(name: str) -> list[str]:
    """来源名 → 别名变体（全名 + 分隔符前缀），长度 ≥ 3，去重排序。"""
    variants = {name.strip()}
    for separator in (" | ", " - ", ": "):
        if separator in name:
            variants.add(name.split(separator, 1)[0].strip())
    return sorted(v for v in variants if len(v) >= MIN_ALIAS_LEN)


def build_source_index(corpus: list[dict]) -> dict:
    """来源词表 + 别名表 + 每来源的条目 id（`multihop:<i:04d>`）。"""
    names = sorted({(article.get("source") or "").strip() for article in corpus})
    names = [name for name in names if name]
    alias_to_names: dict[str, list[str]] = {}
    for name in names:
        for alias in aliases_of(name):
            alias_to_names.setdefault(alias, [])
            if name not in alias_to_names[alias]:
                alias_to_names[alias].append(name)
    ids_by_source: dict[str, list[str]] = {name: [] for name in names}
    for index, article in enumerate(corpus):
        name = (article.get("source") or "").strip()
        if name:
            ids_by_source[name].append(mh.article_id(index))
    patterns = {alias: re.compile(r"(?<![a-z0-9])" + re.escape(alias.lower()) + r"(?![a-z0-9])")
                for alias in alias_to_names}
    return {
        "names": names,
        "aliases": {alias: sorted(set(targets)) for alias, targets in sorted(alias_to_names.items())},
        "ids_by_source": ids_by_source,
        "patterns": patterns,
    }


def mention_sources(question: str, index: dict) -> list[str]:
    """问题文本里显式提到的来源（canonical 名，去重排序）。"""
    lowered = (question or "").lower()
    hit: set[str] = set()
    for alias, pattern in index["patterns"].items():
        if pattern.search(lowered):
            hit.update(index["aliases"][alias])
    return sorted(hit)


# ------------------------------------------------------------------ 核验

def _pair_summary(mentions: list[str], gold: set[str], evidence: set[str],
                  ids_by_source: dict[str, list[str]]) -> dict:
    """一道题在一个证据臂上的提及级 / 文章级计数。"""
    with_gold = 0
    blind = 0
    satisfied = 0
    gold_articles = 0
    gold_articles_missing = 0
    for source in mentions:
        articles = ids_by_source.get(source, [])
        gold_here = [entry for entry in articles if entry in gold]
        if not gold_here:
            continue
        with_gold += 1
        gold_articles += len(gold_here)
        missing_here = [entry for entry in gold_here if entry not in evidence]
        gold_articles_missing += len(missing_here)
        if len(missing_here) == len(gold_here):
            blind += 1                      # 该来源在证据集里**一篇都没有**
        if any(entry in evidence for entry in gold_here):
            satisfied += 1
    return {
        "n_mentions": len(mentions),
        "n_mentions_with_gold": with_gold,
        "n_mentions_gold_missing_in_evidence": blind,
        "n_mentions_gold_satisfied_in_evidence": satisfied,
        "n_gold_articles_from_mentions": gold_articles,
        "n_gold_articles_from_mentions_missing_in_evidence": gold_articles_missing,
        # 题级布尔：只要有一个「gold 里有、证据里没有」的提及来源 → 约束未被核
        "blind_risk_any": blind > 0,
        # 更严：**所有**在 gold 里出现的提及来源，在证据里都一篇没有
        "blind_risk_all": with_gold > 0 and blind == with_gold,
        "constraint_satisfied_any": satisfied > 0,
        "mentions_absent_from_gold": with_gold == 0 and len(mentions) > 0,
    }


def audit(census_dir: str, traces_path: str | None = None,
          grades_path: str | None = None) -> dict:
    data_dir = os.path.join(census_dir, "data")
    corpus, queries = mh.load_dataset(data_dir)
    eval_set = {row["id"]: row for row in mh.build_eval_set(corpus, queries)["queries"]}
    sample = load_json(os.path.join(census_dir, "phase_b", "sample.json"))
    ids = list(sample["all"])

    traces_path = traces_path or os.path.join(
        census_dir, PHASE_A2_ARTIFACTS_REL, "trace", "a1_traces.jsonl")
    traces = read_jsonl_last_wins(traces_path)
    control = load_json(os.path.join(census_dir, PHASE_A2_ARTIFACTS_REL,
                                     "control_rankings.json"))
    control_rows = {row["id"]: row for row in control["rows"]}

    index = build_source_index(corpus)
    ids_by_source = index["ids_by_source"]

    answerable = [qid for qid in ids if eval_set[qid]["relevant"]]
    missing_traces = [qid for qid in answerable
                      if not ((traces.get(qid) or {}).get("trace") or {}).get("final")]
    if missing_traces:
        raise SystemExit(f"trace 缺失/无 final 的可答题：{missing_traces[:5]}")

    per_question: list[dict] = []
    totals = {arm: {"n_questions_with_mention": 0,
                    "n_questions_mention_source_in_gold": 0,
                    "n_mentions": 0,
                    "n_mentions_with_gold": 0,
                    "n_mentions_gold_missing_in_evidence": 0,
                    "n_mentions_gold_satisfied_in_evidence": 0,
                    "n_gold_articles_from_mentions": 0,
                    "n_gold_articles_from_mentions_missing_in_evidence": 0,
                    "n_blind_risk_questions_any": 0,
                    "n_blind_risk_questions_all": 0,
                    "n_constraint_satisfied_questions": 0,
                    "n_questions_all_mentions_absent_from_gold": 0}
              for arm in ARMS}

    for qid in answerable:
        row = eval_set[qid]
        control_row = control_rows[qid]
        gold = set(row["relevant"])
        mentions = mention_sources(row["query"], index)
        trace = (traces[qid].get("trace") or {})
        evidence_by_arm = {
            "A1": set((trace.get("final") or {}).get("evidence_ids") or []),
            "C15": set(control_row["ranked"][:C15_K]),
        }
        record = {"id": qid, "mentions": mentions, "n_gold": len(gold)}
        for arm in ARMS:
            summary = _pair_summary(mentions, gold, evidence_by_arm[arm], ids_by_source)
            record[arm] = summary
            bucket = totals[arm]
            if mentions:
                bucket["n_questions_with_mention"] += 1
            if summary["n_mentions_with_gold"] > 0:
                bucket["n_questions_mention_source_in_gold"] += 1
            if summary["mentions_absent_from_gold"]:
                bucket["n_questions_all_mentions_absent_from_gold"] += 1
            bucket["n_mentions"] += summary["n_mentions"]
            bucket["n_mentions_with_gold"] += summary["n_mentions_with_gold"]
            bucket["n_mentions_gold_missing_in_evidence"] += \
                summary["n_mentions_gold_missing_in_evidence"]
            bucket["n_mentions_gold_satisfied_in_evidence"] += \
                summary["n_mentions_gold_satisfied_in_evidence"]
            bucket["n_gold_articles_from_mentions"] += summary["n_gold_articles_from_mentions"]
            bucket["n_gold_articles_from_mentions_missing_in_evidence"] += \
                summary["n_gold_articles_from_mentions_missing_in_evidence"]
            bucket["n_blind_risk_questions_any"] += int(summary["blind_risk_any"])
            bucket["n_blind_risk_questions_all"] += int(summary["blind_risk_all"])
            bucket["n_constraint_satisfied_questions"] += \
                int(summary["constraint_satisfied_any"])
        per_question.append(record)

    result = {
        "scope": "range_a_source_constraint_audit",
        "post_hoc": True,
        "zero_llm": True,
        "census_dir_rel": "experiments/agentic-rag-census",
        "inputs": {
            "corpus": "data/corpus.json",
            "queries": "data/MultiHopRAG.json",
            "sample": "phase_b/sample.json",
            "traces": f"{PHASE_A2_ARTIFACTS_REL}/trace/a1_traces.jsonl",
            "control_rankings": f"{PHASE_A2_ARTIFACTS_REL}/control_rankings.json",
        },
        "method": {
            "match_rule": ("问题文本 lower() 后对来源别名做 ASCII 词边界、大小写不敏感的"
                           "全词匹配：(?<![a-z0-9])alias(?![a-z0-9])；同一来源一题只计一次。"),
            "alias_rule": ("来源名本体 + 三个前缀变体（' | ' / ' - ' / ': ' 之前），"
                           f"长度 < {MIN_ALIAS_LEN} 丢弃；一个别名可映射多个来源。"),
            "min_alias_len": MIN_ALIAS_LEN,
            "source_vocabulary_size": len(index["names"]),
            "source_names": index["names"],
            "aliases": index["aliases"],
            "denominators": {
                "D1_answerable_questions": len(answerable),
                "D2_questions_with_mention": totals["A1"]["n_questions_with_mention"],
                "D3_question_source_pairs": totals["A1"]["n_mentions"],
                "D4_pairs_whose_source_in_gold": totals["A1"]["n_mentions_with_gold"],
            },
            "blind_risk_definition": (
                "某个被提及来源在 gold 里有文章、但在该臂证据集里**一篇都没有** → 约束被"
                "『说了』却没被『核』。A1 证据 = trace.final.evidence_ids；"
                f"C15 证据 = control_rankings.rows[].ranked[:{C15_K}]。"),
            "known_limits": [
                "机械别名匹配：不识别 'CBS Sports' ↔ 'CBSSports.com' 这类改写（假阴性）。",
                "同形异义（如来源名与普通词重合）只能靠别名长度过滤，未做语义消歧（假阳性）。",
                "只量『来源约束』这一条显式面；题里其它约束（数字 / 时间 / 实体）不在本脚本。",
            ],
        },
        "per_arm": totals,
        "per_question": per_question,
    }
    if grades_path and os.path.isfile(grades_path):
        result["cross_with_answers"] = cross_with_answers(
            per_question, read_grades(grades_path), eval_set, sample)
    return result


def read_grades(path: str) -> dict[str, dict]:
    return read_jsonl_last_wins(path)


def cross_with_answers(per_question: list[dict], grades: dict[str, dict],
                       eval_set: dict[str, dict], sample: dict) -> dict:
    """范围 A × 范围 B：来源约束状态 × 答案对错（只在**有已存答案**的题上）。"""
    by_id = {row["id"]: row for row in per_question}
    answerable = [qid for qid in sample["all"] if eval_set[qid]["relevant"]]
    graded = [qid for qid in answerable if (grades.get(qid) or {}).get("correct") is not None]
    out: dict = {
        "note": ("只在『可答 且 轨迹里有已存答案』的题上交叉（见 range_b.coverage）；"
                 "无已存答案的题无法判对错。"),
        "n_graded_answerable": len(graded),
        "arms": {},
    }
    for arm in ARMS:
        buckets = {
            "blind_risk_any": {"n": 0, "correct": 0},
            "constraint_satisfied_no_blind": {"n": 0, "correct": 0},
            "no_mention": {"n": 0, "correct": 0},
            "mention_but_source_not_in_gold": {"n": 0, "correct": 0},
        }
        for qid in graded:
            row = by_id[qid]
            summary = row[arm]
            if not row["mentions"]:
                key = "no_mention"
            elif summary["blind_risk_any"]:
                key = "blind_risk_any"
            elif summary["constraint_satisfied_any"]:
                key = "constraint_satisfied_no_blind"
            else:
                key = "mention_but_source_not_in_gold"
            buckets[key]["n"] += 1
            buckets[key]["correct"] += int(bool(grades[qid]["correct"]))
        for key, bucket in buckets.items():
            bucket["accuracy"] = (round(bucket["correct"] / bucket["n"], 4)
                                  if bucket["n"] else None)
        out["arms"][arm] = buckets
    return out


# ------------------------------------------------------------------ CLI

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="A‴ range A: source-constraint audit (no LLM)")
    parser.add_argument("--census-dir", default=None,
                        help="主树 experiments/agentic-rag-census（数据 / 索引只在主树）")
    parser.add_argument("--traces", default=None, help="A1 轨迹 JSONL（默认 <census>/phase_a2/artifacts/trace/a1_traces.jsonl）")
    parser.add_argument("--grades", default=os.path.join(A3_TRACE_DIR, "a3_grades.jsonl"),
                        help="可选：范围 B 逐题判分 JSONL（用于交叉统计）")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    census_dir = args.census_dir or os.path.join(main_worktree(),
                                                 "experiments", "agentic-rag-census")
    if not os.path.isfile(os.path.join(census_dir, "data", "corpus.json")):
        raise SystemExit(f"census-dir 里没有 data/corpus.json：{census_dir}"
                         "（数据集只在主树；worktree 里没有）")
    result = audit(census_dir, traces_path=args.traces, grades_path=args.grades)
    write_json(args.out, result)
    if not args.quiet:
        for arm in ARMS:
            bucket = result["per_arm"][arm]
            log(f"[rangeA:{arm}] mention_questions={bucket['n_questions_with_mention']}"
                f"/{result['method']['denominators']['D1_answerable_questions']} "
                f"pairs={bucket['n_mentions']} pairs_with_gold={bucket['n_mentions_with_gold']} "
                f"blind_pairs={bucket['n_mentions_gold_missing_in_evidence']} "
                f"blind_questions_any={bucket['n_blind_risk_questions_any']}")
        log(f"[out] {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
