"""Phase B 三臂 harness（issue #48）：B1 单发 / B2 单发+改写 / B3 迭代循环。

- 检索链与 Phase A 完全一致：`MemoryRetriever` + `DefaultRetrievalStrategy`（向量 + 关键词），
  每轮 **pool=50**、取 **top-5** 入累计证据（B1 直接从 Phase A trace 的 top-50 排名 join）。
- B3：`R_max=4`（1 初始 + ≤3 追问）；停止 = ①LLM 判足够 ②本轮无新 id ③用完预算（按此优先序），
  每条停都落盘触发条件；每轮记录 query / top-50 ids+scores / 新增 id / judge 输出 / 上下文长度。
- 答案：三臂同一答案 prompt（MultiHop-RAG `qa_llama.py` 口径）；判定先确定性匹配，
  匹配不了才用**异模型**裁判（防自评）。
- LLM：`qwen3.7-flash`，`temperature=0`，`enable_thinking=true`，固定 seed。

逐题结果写 `artifacts/trace/<arm>.jsonl`（含数据集 query 文本 → **gitignored**）；
进度写 `--log`（脚本内落盘，不依赖 shell 重定向）。

    python experiments/agentic-rag-census/phase_b/run_phase_b.py --arms b1,b2,b3
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from common import (
    CONTEXT_MAX_CHARS,
    EVIDENCE_CAP,
    PHASE_B_TRACE,
    POOL,
    R_MAX,
    ROUND_TOP_K,
    STORE_DB,
    COLLECTION,
    load_eval,
    load_sample,
    load_trace,
    sample_questions,
)
from evidence import format_context, make_doc, merge_evidence
from llm import DEFAULT_JUDGE_MODEL, DEFAULT_MODEL, DashScope
from prompts import (
    PROMPT_VERSION,
    build_answer_judge_prompt,
    build_answer_prompt,
    build_judge_prompt,
    build_rewrite_prompt,
)

from memory_agent.memory.entries import point_id_for
from memory_agent.memory.retrieval import MemoryRetriever
from memory_agent.memory.store import open_store
from ragcore.strategies.default import DefaultRetrievalStrategy

ARM_FILES = {"b1": "b1.jsonl", "b2": "b2.jsonl", "b3": "b3.jsonl"}


# --------------------------------------------------------------------- utils

class Logger:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._handle = open(path, "a", encoding="utf-8")

    def __call__(self, message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}"
        with self._lock:
            self._handle.write(line + "\n")
            self._handle.flush()
        print(line, file=sys.stderr, flush=True)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (text or "").lower()).split())


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value)


def clean_answer(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    for line in text.splitlines():
        if line.strip():
            text = line.strip()
            break
    text = text.strip().strip('"').strip("'")
    text = re.sub(r"^(the answer is|answer)\s*[:\-]?\s*", "", text, flags=re.IGNORECASE)
    text = text.strip().strip('"').strip("'").rstrip(".").strip()
    return text[:200]


def is_insufficient(text: str) -> bool:
    low = (text or "").lower()
    return "insufficient" in low


def det_match(prediction: str, gold: str) -> bool:
    """确定性匹配：精确 / 包含 / 词元子集（双向）。"""
    p, g = _norm(prediction), _norm(gold)
    if not p or not g:
        return False
    if p == g or g in p or p in g:
        return True
    pt, gt = set(p.split()), set(g.split())
    return bool(gt) and (gt <= pt or pt <= gt)


def has_intersection(prediction: str, gold: str) -> bool:
    """参考 `qa_evaluate.py` 的宽松口径（次级指标，仅供可比性）。"""
    return bool(set(_norm(prediction).split()) & set(_norm(gold).split()))


# ------------------------------------------------------------------- runner

class PhaseBRunner:
    def __init__(self, *, concurrency: int, logger: Logger,
                 model: str, judge_model: str, thinking: bool):
        self.concurrency = concurrency
        self.log = logger
        self.client = DashScope(model, thinking=thinking)
        self.answer_judge = DashScope(judge_model, thinking=thinking)
        self.model = model
        self.judge_model = judge_model
        self.thinking = thinking
        self.store = open_store(db_path=STORE_DB, collection_name=COLLECTION,
                                hybrid=False)
        self.store.warmup()
        self.retriever = MemoryRetriever(
            self.store,
            strategy=DefaultRetrievalStrategy(enable_keyword=True),
            pool_size=POOL,
        )
        self._payload_cache: dict[str, dict] = {}
        self._cache_lock = threading.Lock()

    # ------------------------------------------------------------- retrieval

    def search(self, query: str, k: int) -> list[dict]:
        hits = []
        for score, text, meta in self.retriever.retrieve(query, k=k):
            hits.append({
                "entry_id": meta.get("entry_id"),
                "title": meta.get("title") or "",
                "source": meta.get("source") or "",
                "text": text or "",
                "score": float(score),
            })
        return hits

    def payloads(self, entry_ids: list[str]) -> dict[str, dict]:
        with self._cache_lock:
            missing = [eid for eid in entry_ids if eid not in self._payload_cache]
            for start in range(0, len(missing), 256):
                chunk = missing[start:start + 256]
                for payload in self.store.fetch([point_id_for(eid) for eid in chunk]):
                    entry_id = payload.get("entry_id")
                    if entry_id:
                        self._payload_cache[entry_id] = payload
            return {eid: self._payload_cache.get(eid, {}) for eid in entry_ids}

    # ---------------------------------------------------------------- LLM

    def call_judge(self, question: str, evidence: list[dict],
                   history: list[str]) -> dict:
        context, meta = format_context(evidence, CONTEXT_MAX_CHARS)
        parsed, raw = self.client.chat_json(
            build_judge_prompt(question, context, history), max_tokens=2048)
        parsed["_context"] = meta
        parsed["_latency_s"] = raw["latency_s"]
        return parsed

    def answer(self, question: str, evidence: list[dict]) -> dict:
        context, meta = format_context(evidence, CONTEXT_MAX_CHARS)
        raw = self.client.chat(build_answer_prompt(question, context), max_tokens=1024)
        cleaned = clean_answer(raw["content"])
        return {
            "answer_raw": raw["content"],
            "answer": cleaned,
            "context": meta,
            "latency_s": raw["latency_s"],
        }

    def grade(self, question: str, gold: str, prediction: str) -> dict:
        """先确定性；失败才请异模型裁判。"""
        if not gold:
            return {"correct": None, "match_source": "none", "judge": None}
        if det_match(prediction, gold):
            return {"correct": True, "match_source": "det", "judge": None}
        if is_insufficient(prediction):
            return {"correct": False, "match_source": "insufficient", "judge": None}
        parsed, raw = self.answer_judge.chat_json(
            build_answer_judge_prompt(question, gold, prediction), max_tokens=512)
        matched = _as_bool(parsed.get("match"))
        return {
            "correct": matched,
            "match_source": "judge",
            "judge": {"match": matched,
                      "reason": parsed.get("reason", ""),
                      "latency_s": raw["latency_s"]},
        }

    # ------------------------------------------------------------- workers

    def run_b1(self, row: dict, trace: dict) -> dict:
        ranked = list(trace["ranked"][:POOL])
        scores = list(trace["ranked_scores"][:POOL])
        top = ranked[:ROUND_TOP_K]
        payloads = self.payloads(top)
        evidence = []
        for position, entry_id in enumerate(top):
            payload = payloads.get(entry_id, {})
            evidence.append(make_doc({
                "entry_id": entry_id,
                "title": payload.get("title"),
                "source": payload.get("source"),
                "text": payload.get("text"),
            }, round_index=1, score=scores[position] if position < len(scores) else None))
        answer = self.answer(row["query"], evidence)
        grade = self.grade(row["query"], row.get("answer"), answer["answer"])
        return self._record(row, arm="B1", queries=[row["query"]],
                            rounds=[{"round": 1, "query": row["query"],
                                     "ids": ranked, "scores": scores,
                                     "added_ids": top, "judge": None,
                                     "context_chars": answer["context"]["context_chars"]}],
                            evidence=evidence, stop_trigger=None, rounds_used=1,
                            answer=answer, grade=grade, rewrite=None)

    def run_b2(self, row: dict) -> dict:
        rewrite_raw = self.client.chat(build_rewrite_prompt(row["query"]),
                                       max_tokens=1024)["content"]
        rewrite = clean_answer(rewrite_raw)
        hits = self.search(rewrite, POOL)
        evidence = hits[:ROUND_TOP_K]
        answer = self.answer(row["query"], evidence)
        grade = self.grade(row["query"], row.get("answer"), answer["answer"])
        return self._record(row, arm="B2", queries=[rewrite],
                            rounds=[{"round": 1, "query": rewrite,
                                     "ids": [h["entry_id"] for h in hits],
                                     "scores": [h["score"] for h in hits],
                                     "added_ids": [h["entry_id"] for h in evidence],
                                     "judge": None,
                                     "context_chars": answer["context"]["context_chars"]}],
                            evidence=evidence, stop_trigger=None, rounds_used=1,
                            answer=answer, grade=grade, rewrite=rewrite_raw)

    def run_b3(self, row: dict) -> dict:
        question = row["query"]
        history = [question]
        accumulated: list[dict] = []
        rounds: list[dict] = []
        stop_trigger = None
        query = question
        for round_index in range(1, R_MAX + 1):
            hits = self.search(query, POOL)
            incoming = hits[:ROUND_TOP_K]
            accumulated, added = merge_evidence(accumulated, incoming, EVIDENCE_CAP)
            history_for_judge = list(history)
            judge = self.call_judge(question, accumulated, history_for_judge)
            rounds.append({
                "round": round_index,
                "query": query,
                "ids": [h["entry_id"] for h in hits],
                "scores": [h["score"] for h in hits],
                "added_ids": added,
                "judge": {key: value for key, value in judge.items()
                          if not key.startswith("_")},
                "judge_latency_s": judge.get("_latency_s"),
                "context_chars": judge.get("_context", {}).get("context_chars"),
                "context_truncated": judge.get("_context", {}).get("truncated"),
            })
            if _as_bool(judge.get("enough")):
                stop_trigger = "llm_enough"
                break
            if not added:
                stop_trigger = "no_new_ids"
                break
            if round_index == R_MAX:
                stop_trigger = "budget"
                break
            next_query = str(judge.get("next_query") or "").strip()
            if not next_query or _norm(next_query) in {_norm(h) for h in history}:
                stop_trigger = "no_next_query"
                break
            query = next_query
            history.append(next_query)

        answer = self.answer(question, accumulated)
        grade = self.grade(question, row.get("answer"), answer["answer"])
        return self._record(row, arm="B3", queries=list(history),
                            rounds=rounds, evidence=accumulated,
                            stop_trigger=stop_trigger, rounds_used=len(rounds),
                            answer=answer, grade=grade, rewrite=None)

    # ------------------------------------------------------------- record

    def _record(self, row: dict, *, arm: str, queries: list[str],
                rounds: list[dict], evidence: list[dict], stop_trigger,
                rounds_used: int, answer: dict, grade: dict,
                rewrite: str | None) -> dict:
        return {
            "id": row["id"],
            "arm": arm,
            "split": row.get("split"),
            "question_type": row["question_type"],
            "kind": row["kind"],
            "evidence_count": row["evidence_count"],
            "question": row["query"],
            "gold_answer": row.get("answer"),
            "relevant": list(row["relevant"]),
            "queries": queries,
            "rounds": rounds,
            "evidence_ids": [doc["entry_id"] for doc in evidence],
            "rewrite": rewrite,
            "stop_trigger": stop_trigger,
            "rounds_used": rounds_used,
            "answer": answer["answer"],
            "answer_raw": answer["answer_raw"],
            "answer_context": answer["context"],
            "correct": grade["correct"],
            "match_source": grade["match_source"],
            "answer_judge": grade["judge"],
            "answer_intersection": has_intersection(
                answer["answer"], row.get("answer") or ""),
            "latency_s": answer["latency_s"],
            "model": self.model,
            "judge_model": self.judge_model,
            "thinking": self.thinking,
            "prompt_version": PROMPT_VERSION,
        }


# --------------------------------------------------------------------- main

def read_done(path: str) -> set[str]:
    done: set[str] = set()
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    try:
                        done.add(json.loads(line)["id"])
                    except (json.JSONDecodeError, KeyError):
                        continue
    return done


def append_record(path: str, record: dict) -> None:
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Phase B three-arm harness")
    parser.add_argument("--arms", default="b1,b2,b3")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--ids", default=None, help="只跑该 JSON 列表里的 id（冒烟用）")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--no-thinking", action="store_true")
    parser.add_argument("--log", default=os.path.join(PHASE_B_TRACE, "run.log"))
    args = parser.parse_args(argv)

    print(f"[debug] model={args.model} judge={args.judge_model} "
          f"thinking={not args.no_thinking} concurrency={args.concurrency}", flush=True)
    logger = Logger(args.log)
    logger(f"start arms={args.arms} limit={args.limit} model={args.model} "
           f"prompt_version={PROMPT_VERSION}")

    sample = load_sample()
    eval_set = load_eval()
    rows = sample_questions(sample, eval_set)
    trace = load_trace()
    if args.ids:
        wanted = set(json.load(open(args.ids, encoding="utf-8")))
        rows = [row for row in rows if row["id"] in wanted]
    if args.limit is not None:
        rows = rows[:args.limit]
    logger(f"questions={len(rows)}")

    os.makedirs(PHASE_B_TRACE, exist_ok=True)
    runner = PhaseBRunner(concurrency=args.concurrency, logger=logger,
                          model=args.model, judge_model=args.judge_model,
                          thinking=not args.no_thinking)

    arms = [arm.strip().lower() for arm in args.arms.split(",") if arm.strip()]
    try:
        for arm in arms:
            path = os.path.join(PHASE_B_TRACE, ARM_FILES[arm])
            done = read_done(path)
            todo = [row for row in rows if row["id"] not in done]
            logger(f"[{arm}] resume done={len(done)} todo={len(todo)} -> {path}")
            if not todo:
                continue
            worker = {"b1": lambda row: runner.run_b1(row, trace[row["id"]]),
                      "b2": runner.run_b2,
                      "b3": runner.run_b3}[arm]
            started = time.time()
            completed = 0
            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                futures = {pool.submit(worker, row): row for row in todo}
                for future in as_completed(futures):
                    row = futures[future]
                    try:
                        record = future.result()
                    except Exception as exc:  # noqa: BLE001 - 逐题失败不拖垮整轮
                        record = {"id": row["id"], "arm": arm.upper(),
                                  "error": f"{type(exc).__name__}: {exc}"}
                    append_record(path, record)
                    completed += 1
                    if completed % 10 == 0 or completed == len(todo):
                        rate = completed / max(time.time() - started, 1e-6)
                        logger(f"[{arm}] {completed}/{len(todo)} "
                               f"({rate:.2f}/s remaining~{(len(todo)-completed)/max(rate,1e-6)/60:.1f}min)")
            logger(f"[{arm}] done in {time.time() - started:.1f}s")
        logger(f"stats main={runner.client.stats()} answer_judge={runner.answer_judge.stats()}")
    finally:
        runner.store.close()
    logger("finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
