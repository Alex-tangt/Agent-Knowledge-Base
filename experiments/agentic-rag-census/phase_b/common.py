"""Phase B 共享装载器（issue #48）：数据集 / 抽样 / Phase A trace / 独立 store。

不加载模型；检索由 `run_phase_b.py` 经 `run_census.open_searcher` 打开。

- 评测集：`../data/MultiHopRAG.json` + `../data/corpus.json` —— **纯函数**构造
  （不调用 `multihop.ensure_prepared`，避免重写已删的 `data/articles/`）。
- B1 输入：`../artifacts/trace/base_hybrid.jsonl`（Phase A 全量排名，pool=50）。
- 抽样：`sample.json`（冻结 id 列表 + seed）。
- store：`../store/base/qdrant` + `manifest.json`（私有副本；`store.fetch` 取回 payload 正文）。
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CENSUS_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(os.path.dirname(CENSUS_DIR))

for path in (REPO_ROOT, CENSUS_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

import multihop as mh  # noqa: E402

DATA_DIR = mh.DATA_DIR
ARTIFACTS_DIR = mh.ARTIFACTS_DIR
TRACE_B1 = os.path.join(ARTIFACTS_DIR, "trace", "base_hybrid.jsonl")
STORE_DB = os.path.join(mh.STORE_DIR, "base", "qdrant")
STORE_MANIFEST = os.path.join(mh.STORE_DIR, "base", "manifest.json")

PHASE_B_DIR = HERE
PHASE_B_ARTIFACTS = os.path.join(HERE, "artifacts")
PHASE_B_TRACE = os.path.join(PHASE_B_ARTIFACTS, "trace")
SAMPLE_JSON = os.path.join(HERE, "sample.json")

COLLECTION = mh.COLLECTION
POOL = 50          # 检索池（与 Phase A 一致；每轮仍只取 top-5 入累计证据）
ROUND_TOP_K = 5    # 每轮进累计证据的条数（产品 memory_search 默认）
R_MAX = 4          # 检索轮次上限（1 初始 + ≤3 追问；架构层补定）
EVIDENCE_CAP = 20  # 累计证据上限（R_max × k）
# judge / answer 送 LLM 的证据正文总预算（按证据顺序截断）。
# 实测 gold fact 的末尾位置：p50=2316 / p90=5003 / max=5990 字 → 条目级证据必须给整篇
# （≤6000），不能按 3k 截断（会丢 ~40% fact）。20 篇 × 6000 ≈ 120k 字 ≈ 30k token。
CONTEXT_MAX_CHARS = 120000


def load_eval() -> dict[str, dict]:
    """条目级评测集 + gold answer：`mhr0000` -> row。"""
    corpus, queries = mh.load_dataset()
    base = mh.build_eval_set(corpus, queries)
    out: dict[str, dict] = {}
    for row, item in zip(base["queries"], queries):
        row = dict(row)
        row["answer"] = item.get("answer")
        out[row["id"]] = row
    return out


def load_trace(path: str = TRACE_B1) -> dict[str, dict]:
    """Phase A 逐题排名 trace：`id` -> {ranked, ranked_scores, relevant, ...}。"""
    out: dict[str, dict] = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                record = json.loads(line)
                out[record["id"]] = record
    return out


def load_sample(path: str = SAMPLE_JSON) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def source_vocabulary() -> list[str]:
    """语料 source 名的规范化集合（用于「来源样板词 vs 内容约束」切分）。"""
    corpus, _ = mh.load_dataset()
    seen: dict[str, str] = {}
    for article in corpus:
        name = (article.get("source") or "").strip()
        if name:
            seen.setdefault(_norm_text(name), name)
    return list(seen.values())


def _norm_text(text: str) -> str:
    return " ".join((text or "").lower().split())


def sample_questions(sample: dict, eval_set: dict[str, dict]) -> list[dict]:
    """按冻结顺序返回本题行（含 dev/holdout 标记）。"""
    split_of: dict[str, str] = {}
    for name, ids in sample.get("split", {}).items():
        for entry_id in ids:
            split_of[entry_id] = name
    rows = []
    for qid in sample["all"]:
        row = dict(eval_set[qid])
        row["split"] = split_of.get(qid, "dev")
        rows.append(row)
    return rows
