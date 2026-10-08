r"""A‴ 范围 B（ticket #76）：给**已存**答案判分（LLM 裁判，可断点续跑）。

不重跑循环：预测 = `phase_a2/artifacts/trace/a1_traces.jsonl` 的 `trace.final.answer`
（A″ 运行时**只在 `stop.trigger == "answer"` 时**写答案；其余停止触发器 `answer=null`，
→ 见报告「覆盖率」一节，这是本票必须如实写明的口径事实）。

判分口径**逐字照** `phase_b/run_phase_b.py`（不重造）：
① `det_match`（归一化 + 精确 / 包含 / 词元子集双向）命中 → 对；
② `is_insufficient` → 错；
③ 其余请**异模型**裁判（`qwen3.7-max`，防自评；不可用则回 `qwen3.7-flash` 并如实记录）。
裁判 prompt = `phase_b/prompts.py::build_answer_judge_prompt(question, gold, prediction)`
——**只给问题 + gold + 预测**，不透露 gold 齐不齐（避免暗示裁判）。

gold answer 来源：`data/MultiHopRAG.json` 第 i 条的 `answer`（`mhr<i:04d>` ↔ 第 i 条）。
`n_gold` / `n_hit` / `gold_complete` 来自 `phase_a2/artifacts/control_rankings.json` 的
`relevant` 与 A1 轨迹的 `trace.final.evidence_ids`。

复跑（**范围 B 已判完就不必再跑**；未判完会自动跳过已完成 id）：

    $py = "D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe"
    $env:DASHSCOPE_ENV_FILE = "D:\Study\SFT\kg-triplet-sft\.env"   # key 不入库 / 不回显
    & $py experiments/agentic-rag-census/phase_a2/a3_grade_answers.py --census-dir <主树>/experiments/agentic-rag-census --report

产物：
- `artifacts/trace/a3_grades.jsonl`（**gitignored**：逐题判分 + 裁判 reason，含数据集原文）；
- `a3_results.json`（**入库**：只存 id / 计数 / 布尔 / match_source，**不含数据集原文**）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
CENSUS_WT = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(os.path.dirname(CENSUS_WT))
PHASE_B_DIR = os.path.join(CENSUS_WT, "phase_b")

for _path in (PHASE_B_DIR, CENSUS_WT, REPO_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import multihop as mh  # noqa: E402
from llm import DEFAULT_JUDGE_MODEL, DEFAULT_MODEL, DashScope, LLMError  # noqa: E402
from prompts import build_answer_judge_prompt  # noqa: E402
from run_phase_b import _norm, clean_answer, det_match, is_insufficient  # noqa: E402

import a3_constraint_audit as audit_mod  # noqa: E402

A3_TRACE_DIR = os.path.join(HERE, "artifacts", "trace")
DEFAULT_JSONL = os.path.join(A3_TRACE_DIR, "a3_grades.jsonl")
DEFAULT_CONSTRAINT = os.path.join(A3_TRACE_DIR, "a3_constraint.json")
DEFAULT_RESULTS = os.path.join(HERE, "a3_results.json")
PHASE_A2_ARTIFACTS_REL = "phase_a2/artifacts"

PREDICTION_SOURCE = ("A1 轨迹 trace.final.answer（运行时仅在模型给出 ANSWER 时写入；"
                     "budget / no_new_ids / fallback / insufficient 停止时 answer=null）")


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def sanitize_proxy_env() -> dict:
    """去掉 `NO_PROXY`/`no_proxy` 里的 IPv6 条目（宿主 env 缺陷；抄 run_a2）。"""
    changed: dict[str, str] = {}
    for key in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(key)
        if not raw:
            continue
        parts = [item.strip() for item in raw.split(",") if item.strip()]
        kept = [item for item in parts if not any(ch in item for ch in ":[]")]
        if kept != parts:
            os.environ[key] = ",".join(kept)
            changed[key] = os.environ[key]
    return changed


def load_json(path: str):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=1)


def read_jsonl_last_wins(path: str) -> dict[str, dict]:
    return audit_mod.read_jsonl_last_wins(path)


def main_worktree() -> str:
    return audit_mod.main_worktree()


# ------------------------------------------------------------------ 装载

class Inputs:
    def __init__(self, census_dir: str, traces_path: str | None = None):
        self.census_dir = census_dir
        corpus, queries = mh.load_dataset(os.path.join(census_dir, "data"))
        eval_set: dict[str, dict] = {}
        for row, item in zip(mh.build_eval_set(corpus, queries)["queries"], queries):
            entry = dict(row)
            entry["gold_answer"] = item.get("answer")
            eval_set[row["id"]] = entry
        self.eval_set = eval_set
        self.sample = load_json(os.path.join(census_dir, "phase_b", "sample.json"))
        self.ids = list(self.sample["all"])
        self.traces_path = traces_path or os.path.join(
            census_dir, PHASE_A2_ARTIFACTS_REL, "trace", "a1_traces.jsonl")
        self.traces = read_jsonl_last_wins(self.traces_path)
        control = load_json(os.path.join(census_dir, PHASE_A2_ARTIFACTS_REL,
                                         "control_rankings.json"))
        self.control_rows = {row["id"]: row for row in control["rows"]}
        self.answerable = [qid for qid in self.ids if self.eval_set[qid]["relevant"]]

    def answer_of(self, qid: str) -> str | None:
        final = ((self.traces.get(qid) or {}).get("trace") or {}).get("final") or {}
        return final.get("answer")

    def stop_of(self, qid: str) -> str | None:
        trace = (self.traces.get(qid) or {}).get("trace") or {}
        return (trace.get("stop") or {}).get("trigger")

    def evidence_of(self, qid: str) -> list[str]:
        trace = (self.traces.get(qid) or {}).get("trace") or {}
        return list((trace.get("final") or {}).get("evidence_ids") or [])

    def recall_of(self, qid: str) -> dict:
        gold = set(self.eval_set[qid]["relevant"])
        hit = gold & set(self.evidence_of(qid))
        return {
            "n_gold": len(gold),
            "n_hit": len(hit),
            "gold_complete": (True if len(hit) == len(gold) else False) if gold else None,
        }


# ------------------------------------------------------------------ 判分

def det_match_kind(prediction: str, gold: str) -> str | None:
    """`det_match` 的**展开分类**（同一判定，只多记录命中的是哪种）：

    `exact`（归一化后完全相等）/ `substring`（字符包含，任一方向）/ `token_subset`
    （词元集合包含，任一方向）/ None（不命中）。用于「严格 / 词级 / 先例」三档敏感性，
    主表判对错仍与 phase_b 的 `det_match` **逐题一致**（grading 内自带自证计数）。
    """
    p, g = _norm(prediction), _norm(gold)
    if not p or not g:
        return None
    if p == g:
        return "exact"
    if g in p or p in g:
        return "substring"
    pt, gt = set(p.split()), set(g.split())
    if bool(gt) and (gt <= pt or pt <= gt):
        return "token_subset"
    return None

class Grader:
    def __init__(self, *, judge_model: str, fallback_model: str, thinking: bool,
                 concurrency: int):
        self.primary = judge_model
        self.fallback = fallback_model
        self.judge_used = judge_model
        self._switched = False
        self.concurrency = concurrency
        self.thinking = thinking
        self.judge = DashScope(judge_model, thinking=thinking)
        self._fallback_client: DashScope | None = None
        self._lock = threading.Lock()
        self.stats = {"det": 0, "insufficient": 0, "judge": 0, "no_gold": 0, "error": 0,
                      "judge_calls": 0}

    def _fallback_client_or_none(self) -> DashScope | None:
        with self._lock:
            if self._fallback_client is None:
                try:
                    self._fallback_client = DashScope(self.fallback, thinking=self.thinking)
                except Exception as exc:  # noqa: BLE001
                    log(f"[grade] fallback 客户端构造失败：{type(exc).__name__}: {exc}")
                    return None
            return self._fallback_client

    def _ask_judge(self, question: str, gold: str, prediction: str) -> tuple[dict, str, str]:
        """先主裁判模型；构造 / 调用失败则回退模型。返回 (parsed, model_used, text)。"""
        client = self.judge
        model = self.primary
        if self._switched:
            client = self._fallback_client_or_none()
            model = self.fallback
            if client is None:
                raise LLMError(f"裁判模型不可用：{self.primary} 与 {self.fallback} 都失败")
        prompt = build_answer_judge_prompt(question, gold, prediction)
        try:
            parsed, raw = client.chat_json(prompt, max_tokens=512)
        except LLMError as exc:
            if model == self.fallback:
                raise
            log(f"[grade] 主裁判 {self.primary} 失败，回退 {self.fallback}：{exc}")
            with self._lock:
                self._switched = True
            client = self._fallback_client_or_none()
            if client is None:
                raise
            model = self.fallback
            parsed, raw = client.chat_json(prompt, max_tokens=512)
        text = str(parsed.get("reason") or "")
        return parsed, model, text

    def grade(self, qid: str, question: str, gold: str | None,
              prediction: str | None) -> dict:
        rec = {"id": qid, "match_source": None, "correct": None, "judge_model": None,
               "judge_reason": None, "prediction_chars": len(prediction or ""),
               "latency_s": None, "error": None}
        if not gold:
            rec.update(match_source="no_gold", correct=None)
            self.stats["no_gold"] += 1
            return rec
        if prediction is None:
            rec.update(match_source="no_prediction", correct=None)
            self.stats["no_gold"] += 1
            return rec
        cleaned = clean_answer(prediction)
        # 敏感性：严格口径 = 归一化后**完全相等**（phase_b 的 `_norm`）。
        # 主口径仍是 phase_b 的 `det_match`（含包含 / 词元子集，对 yes/no 类 gold 很宽松）。
        strict = bool(_norm(cleaned)) and _norm(cleaned) == _norm(gold)
        rec["strict_match"] = strict
        kind = det_match_kind(cleaned, gold)
        rec["det_match_kind"] = kind
        with self._lock:
            # 自证：本函数与 phase_b 的 `det_match` 判定必须一致
            self.stats["det_selfcheck_calls"] = self.stats.get("det_selfcheck_calls", 0) + 1
            if (kind is not None) != det_match(cleaned, gold):
                self.stats["det_selfcheck_mismatch"] = \
                    self.stats.get("det_selfcheck_mismatch", 0) + 1
        started = time.time()
        if kind is not None:
            rec.update(match_source="det", correct=True)
            self.stats["det"] += 1
            return rec
        if is_insufficient(cleaned):
            rec.update(match_source="insufficient", correct=False, strict_match=strict)
            self.stats["insufficient"] += 1
            return rec
        parsed, model, reason = self._ask_judge(question, gold, cleaned)
        matched = parsed.get("match")
        matched = matched if isinstance(matched, bool) else \
            str(matched).strip().lower() in {"true", "yes", "1"}
        rec.update(match_source="judge", correct=bool(matched), judge_model=model,
                   judge_reason=reason, latency_s=round(time.time() - started, 3),
                   strict_match=strict)
        with self._lock:
            self.stats["judge"] += 1
            self.stats["judge_calls"] += 1
        return rec


def append_jsonl(path: str, record: dict, lock: threading.Lock) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with lock:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()


def run_grading(args) -> int:
    inputs = Inputs(args.census_dir, args.traces)
    out_jsonl = args.out_jsonl
    done = {qid: rec for qid, rec in read_jsonl_last_wins(out_jsonl).items()
            if rec.get("correct") is not None and not rec.get("error")}
    pending = []
    for qid in inputs.answerable:
        if qid in done:
            continue
        prediction = inputs.answer_of(qid)
        if prediction is None and not args.include_missing:
            continue                 # 运行时没有产出答案 → 不判、不写（覆盖率里如实报）
        pending.append(qid)
    if args.limit is not None:
        pending = pending[:args.limit]
    n_stored_answerable = sum(1 for qid in inputs.answerable
                              if inputs.answer_of(qid) is not None)
    log(f"[grade] answerable={len(inputs.answerable)} "
        f"answerable_with_stored_answer={n_stored_answerable} "
        f"done={len(done)} pending={len(pending)} jsonl={out_jsonl}")

    changed = sanitize_proxy_env()
    log(f"[grade] proxy_env_sanitized={json.dumps(changed)} "
        f"env_file={os.environ.get('DASHSCOPE_ENV_FILE') or '(process env)'} "
        f"cwd={os.getcwd()}")
    if not pending:
        # 全部已完成 → 不该因为"没有 key"而失败（断点续跑的可复跑性）
        log("[grade] nothing pending (all answerable questions with stored answers graded)")
        return 0
    grader = Grader(judge_model=args.judge_model, fallback_model=args.fallback_model,
                    thinking=not args.no_thinking, concurrency=args.concurrency)
    lock = threading.Lock()
    started = time.time()
    completed = 0

    def work(qid: str) -> dict:
        record = grader.grade(qid, inputs.eval_set[qid]["query"],
                              inputs.eval_set[qid].get("gold_answer"),
                              inputs.answer_of(qid))
        record.update(inputs.recall_of(qid))
        record["stop"] = inputs.stop_of(qid)
        record["gold_answer_chars"] = len(inputs.eval_set[qid].get("gold_answer") or "")
        return record

    if pending:
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            futures = {pool.submit(work, qid): qid for qid in pending}
            for future in as_completed(futures):
                qid = futures[future]
                try:
                    record = future.result()
                except Exception as exc:  # noqa: BLE001 - 逐题失败不拖垮整轮
                    record = {"id": qid, "correct": None, "match_source": "error",
                              "error": f"{type(exc).__name__}: {exc}"}
                    grader.stats["error"] += 1
                append_jsonl(out_jsonl, record, lock)
                completed += 1
                if completed % 10 == 0 or completed == len(pending):
                    log(f"[grade] {completed}/{len(pending)} "
                        f"({completed / max(time.time() - started, 1e-6):.2f}/s) "
                        f"stats={json.dumps(grader.stats)}")
    log(f"[grade] done in {time.time() - started:.1f}s stats={json.dumps(grader.stats)} "
        f"judge_model_used={grader.judge_used if not grader._switched else grader.fallback}")
    return 0


# ------------------------------------------------------------------ 汇总（入库）

def _table(rows: list[dict]) -> dict:
    """`gold_complete` × `correct` 的 2×2（只用有答案的题）。"""
    table = {"gold_complete": {"correct": 0, "wrong": 0},
             "gold_incomplete": {"correct": 0, "wrong": 0}}
    for row in rows:
        if row.get("gold_complete") is None or row.get("correct") is None:
            continue
        bucket = "gold_complete" if row["gold_complete"] else "gold_incomplete"
        table[bucket]["correct" if row["correct"] else "wrong"] += 1
    for bucket in table.values():
        total = bucket["correct"] + bucket["wrong"]
        bucket["n"] = total
        bucket["accuracy"] = round(bucket["correct"] / total, 4) if total else None
    return table


def _accuracy(rows: list[dict]) -> dict:
    graded = [row for row in rows if row.get("correct") is not None]
    return {
        "n": len(rows),
        "n_graded": len(graded),
        "n_correct": sum(1 for row in graded if row["correct"]),
        "accuracy": (round(sum(1 for row in graded if row["correct"]) / len(graded), 4)
                     if graded else None),
        "n_no_stored_answer": len(rows) - len(graded),
    }


def build_results(args) -> dict:
    inputs = Inputs(args.census_dir, args.traces)
    grades = read_jsonl_last_wins(args.out_jsonl)
    answerable = inputs.answerable

    per_question = []
    for qid in answerable:
        recall = inputs.recall_of(qid)
        record = grades.get(qid) or {}
        per_question.append({
            "id": qid,
            "n_gold": recall["n_gold"],
            "n_hit": recall["n_hit"],
            "gold_complete": recall["gold_complete"],
            "correct": record.get("correct"),
            "match_source": record.get("match_source"),
        })

    rows_with_answer = [row for row in per_question if row["correct"] is not None]
    table = _table(rows_with_answer)

    # 敏感性：把「确定性匹配」改成**严格相等**（det 命中但非严格相等的算错；裁判结果不变）
    strict_rows = []
    for row in rows_with_answer:
        grade = grades.get(row["id"]) or {}
        strict_ok = bool(grade.get("strict_match")) if row["match_source"] == "det" \
            else bool(row["correct"])
        strict_rows.append(dict(row, correct=strict_ok))
    strict_table = _table(strict_rows)

    # 中档敏感性：把确定性匹配的「字符包含」通道去掉（`substring` 会把 gold='no' 与
    # 预测里的 'notably' / 'inconsistent' 之类误判），只留 `exact` + `token_subset`。
    word_rows = []
    for row in rows_with_answer:
        grade = grades.get(row["id"]) or {}
        word_ok = (grade.get("det_match_kind") in ("exact", "token_subset")) \
            if row["match_source"] == "det" else bool(row["correct"])
        word_rows.append(dict(row, correct=word_ok))
    word_table = _table(word_rows)
    word_ok = {row["id"]: row["correct"] for row in word_rows}

    kind_counts: dict[str, int] = {}
    for row in rows_with_answer:
        if row["match_source"] != "det":
            continue
        kind = str((grades.get(row["id"]) or {}).get("det_match_kind"))
        kind_counts[kind] = kind_counts.get(kind, 0) + 1

    det_rows = [row for row in rows_with_answer if row["match_source"] == "det"]
    det_diagnostics = {
        "n_det_matches": len(det_rows),
        "det_match_kind_counts": dict(sorted(kind_counts.items())),
        "n_det_matches_also_strict_equal": sum(
            1 for row in det_rows if (grades.get(row["id"]) or {}).get("strict_match")),
        "n_det_matches_loose_only": sum(
            1 for row in det_rows if not (grades.get(row["id"]) or {}).get("strict_match")),
        "n_graded_with_gold_answer_len_le_4": sum(
            1 for row in rows_with_answer
            if (grades.get(row["id"]) or {}).get("gold_answer_chars", 99) <= 4),
        "note": ("phase_b 的 `det_match` 含「字符包含 / 词元子集双向」，对 yes/no 这类 2–4 字符 "
                 "gold 很宽松（gold='no' 与预测里的 'notably' 也会命中字符包含）。"
                 "主表照先例用该口径；另给两档敏感性：`strict_variant`（归一化完全相等）与 "
                 "`wordlevel_variant`（去掉字符包含通道，只留 exact + 词元子集）。"),
    }

    by_stop: dict[str, list[dict]] = {}
    for row in per_question:
        by_stop.setdefault(inputs.stop_of(row["id"]) or "none", []).append(row)

    stop_answer = by_stop.get("answer", [])
    stop_answer_incomplete = [row for row in stop_answer if row["gold_complete"] is False]
    stop_answer_complete = [row for row in stop_answer if row["gold_complete"] is True]

    # 全 176 可答题的「三结果 × gold 齐否」（含"没有产出答案"这一结果）
    outcome_table = {
        "gold_complete": {"correct": 0, "wrong": 0, "no_answer": 0},
        "gold_incomplete": {"correct": 0, "wrong": 0, "no_answer": 0},
    }
    for row in per_question:
        if row["gold_complete"] is None:
            continue
        bucket = "gold_complete" if row["gold_complete"] else "gold_incomplete"
        if row["correct"] is None:
            outcome_table[bucket]["no_answer"] += 1
        else:
            outcome_table[bucket]["correct" if row["correct"] else "wrong"] += 1
    for bucket in outcome_table.values():
        bucket["n"] = bucket["correct"] + bucket["wrong"] + bucket["no_answer"]

    match_sources: dict[str, int] = {}
    for row in rows_with_answer:
        key = row["match_source"] or "none"
        match_sources[key] = match_sources.get(key, 0) + 1

    judge_models: dict[str, int] = {}
    for qid in [row["id"] for row in rows_with_answer]:
        model = (grades.get(qid) or {}).get("judge_model") or "n/a"
        judge_models[model] = judge_models.get(model, 0) + 1

    n_stored = sum(1 for qid in inputs.ids if inputs.answer_of(qid) is not None)
    n_stored_answerable = sum(1 for qid in answerable
                              if inputs.answer_of(qid) is not None)
    n_answerable_no_answer = len(answerable) - n_stored_answerable

    range_b = {
        "scope": "range_b_answer_grading",
        "prediction_source": PREDICTION_SOURCE,
        "grading_contract": {
            "order": ["det_match（归一化 + 精确/包含/词元子集双向）", "is_insufficient → 错",
                      "异模型裁判"],
            "functions_copied_from": "phase_b/run_phase_b.py（clean_answer / det_match / "
                                     "is_insufficient，import 复用不重写）",
            "judge_prompt": "phase_b/prompts.py::build_answer_judge_prompt"
                            "（只给问题 + gold + 预测；不透露 gold 齐不齐）",
            "primary_judge_model": args.judge_model,
            "fallback_judge_model": args.fallback_model,
            "judge_models_actually_used": judge_models,
            "temperature": 0.0, "seed": 42, "thinking": not args.no_thinking,
            "max_tokens": 512,
        },
        "coverage": {
            "n_sample_questions": len(inputs.ids),
            "n_answerable": len(answerable),
            "n_null_query": len(inputs.ids) - len(answerable),
            "n_stored_answers_all_questions": n_stored,
            "n_stored_answers_answerable": n_stored_answerable,
            "n_answerable_without_stored_answer": n_answerable_no_answer,
            "n_graded": len(rows_with_answer),
            "match_source_counts": dict(sorted(match_sources.items())),
            "note": ("A″ 运行时只在 stop.trigger=='answer' 时写 final.answer；"
                     "budget / no_new_ids / fallback / insufficient 停止的题**没有答案可判**。"
                     "本题集上可答题里 66 题有答案、110 题没有 → 2×2 表只能覆盖有答案的 "
                     f"{len(rows_with_answer)} 题。"),
        },
        "table_2x2_gold_x_correct": table,
        "table_2x2_strict_variant": strict_table,
        "table_2x2_wordlevel_variant": word_table,
        "deterministic_diagnostics": det_diagnostics,
        "outcome_table_all_answerable_3x2": outcome_table,
        "stop_distribution_answerable": {key: len(value) for key, value in
                                         sorted(by_stop.items())},
        "key_subsets": {
            "stop_answer": {
                "n": len(stop_answer),
                "gold_complete_n": len(stop_answer_complete),
                "gold_incomplete_n": len(stop_answer_incomplete),
                "gold_incomplete_error_rate": (
                    round(sum(1 for row in stop_answer_incomplete
                              if row["correct"] is False) / len(stop_answer_incomplete), 4)
                    if stop_answer_incomplete else None),
                "gold_incomplete_n_wrong": sum(1 for row in stop_answer_incomplete
                                               if row["correct"] is False),
                "gold_incomplete_error_rate_wordlevel": (
                    round(sum(1 for row in stop_answer_incomplete
                              if word_ok.get(row["id"]) is False)
                          / len(stop_answer_incomplete), 4)
                    if stop_answer_incomplete else None),
                "gold_complete_accuracy": (
                    round(sum(1 for row in stop_answer_complete if row["correct"] is True)
                          / len(stop_answer_complete), 4) if stop_answer_complete else None),
                "gold_complete_n_correct": sum(1 for row in stop_answer_complete
                                               if row["correct"] is True),
                "note": "预注册 #74 的事后口径：66 题 stop=answer 中 44 题 gold 没齐。",
            },
            "fallback": dict(_accuracy(by_stop.get("fallback", [])),
                             stop_trigger="fallback"),
            "budget": dict(_accuracy(by_stop.get("budget", [])), stop_trigger="budget"),
            "no_new_ids": dict(_accuracy(by_stop.get("no_new_ids", [])),
                               stop_trigger="no_new_ids"),
            "insufficient": dict(_accuracy(by_stop.get("insufficient", [])),
                                 stop_trigger="insufficient"),
        },
        "per_question": per_question,
        "not_stored": {
            "judge_reasons_and_raw_grades": "artifacts/trace/a3_grades.jsonl（gitignored）",
            "rule": "入库只存 id / 计数 / 布尔 / match_source；不含问题 / gold / 预测原文。",
        },
    }

    constraint_path = args.constraint or DEFAULT_CONSTRAINT
    if os.path.isfile(constraint_path):
        range_a = load_json(constraint_path)
        # 交叉统计用**最新**判分重算（约束审计里的 grades 路径可能已过期）
        range_a["cross_with_answers"] = audit_mod.cross_with_answers(
            range_a["per_question"], read_jsonl_last_wins(args.out_jsonl),
            inputs.eval_set, inputs.sample)
    else:
        range_a = audit_mod.audit(args.census_dir, traces_path=args.traces,
                                  grades_path=args.out_jsonl)

    return {
        "ticket": "#76 A‴（gold 缺失会不会导致错误信息？来源约束离线核验 + 已存答案判分）",
        "status": "post_hoc_exploratory_not_preregistered",
        "warning": ("本产物为**事后 / 探索性**分析，不在任何预注册内；**不得**据此回改 "
                    "A″(#74) 已冻结的结论。外部语料只作机制证据，不声称本库增益"
                    "（ADR-0026 D5/D6、ADR-0030 D7.5）。"),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "inputs": {
            "census_dir_rel": "experiments/agentic-rag-census",
            "traces": f"{PHASE_A2_ARTIFACTS_REL}/trace/a1_traces.jsonl",
            "control_rankings": f"{PHASE_A2_ARTIFACTS_REL}/control_rankings.json",
            "grades_jsonl": "phase_a2/artifacts/trace/a3_grades.jsonl (gitignored)",
        },
        "range_a": range_a,
        "range_b": range_b,
    }


# ------------------------------------------------------------------ CLI

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="A‴ range B: grade stored A1 answers")
    parser.add_argument("--census-dir", default=None)
    parser.add_argument("--traces", default=None)
    parser.add_argument("--out-jsonl", default=DEFAULT_JSONL)
    parser.add_argument("--constraint", default=DEFAULT_CONSTRAINT)
    parser.add_argument("--results", default=DEFAULT_RESULTS)
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--fallback-model", default=DEFAULT_MODEL)
    parser.add_argument("--no-thinking", action="store_true")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--limit", type=int, default=None, help="只判前 N 题（冒烟用）")
    parser.add_argument("--include-missing", action="store_true",
                        help="也写没有已存答案的题（match_source=no_prediction）")
    parser.add_argument("--report", action="store_true", help="判分后合并 a3_results.json")
    parser.add_argument("--report-only", action="store_true", help="只合并，不判分")
    args = parser.parse_args(argv)

    if not args.census_dir:
        args.census_dir = os.path.join(main_worktree(), "experiments", "agentic-rag-census")
    if not os.path.isfile(os.path.join(args.census_dir, "data", "corpus.json")):
        raise SystemExit(f"census-dir 里没有 data/corpus.json：{args.census_dir}")

    status = 0
    if not args.report_only:
        status = run_grading(args)
    if args.report or args.report_only:
        results = build_results(args)
        write_json(args.results, results)
        coverage = results["range_b"]["coverage"]
        table = results["range_b"]["table_2x2_gold_x_correct"]
        log(f"[report] graded={coverage['n_graded']} match_sources="
            f"{json.dumps(coverage['match_source_counts'])} "
            f"table={json.dumps(table)} [out] {args.results}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
