r"""A″（ticket #74）runner：Step 0 控制臂重算 + 冒烟 + A1 真 agent N=200 + 报告。

测量契约 = 同目录 `PREREGISTRATION.md`（`prereg_commit=fff00c2` + R1 `prereg_amend=55472c4`）。
**被测对象 = 交付运行时，一行都不改**；本脚本只做：装载（纯函数）/ 观测（透明包装）/ 统计。

    $py = <主树 venv>\Scripts\python.exe
    & $py experiments/agentic-rag-census/phase_a2/run_a2.py --step0
    & $py experiments/agentic-rag-census/phase_a2/run_a2.py --smoke
    & $py experiments/agentic-rag-census/phase_a2/run_a2.py --agent --concurrency 4
    & $py experiments/agentic-rag-census/phase_a2/run_a2.py --report

设计要点（其余见 README）：

- **语料 / 索引只在主树**（`data/`、`store/` gitignored）→ `--census-dir` 默认由
  `git worktree list --porcelain` 第一项解析（抄 `run_census._main_worktree`）。
- **绝不调用** `multihop.ensure_prepared()`（它会往 worktree 重写 `data/articles/`）；
  只用纯函数 `load_dataset(data_dir=…)` / `build_eval_set`。
- 索引链**逐字**照 `run_census.open_searcher("base:hybrid", 50)`；**只建一份** index/store。
- `exclude_retired=False`（= 运行时出厂默认，R1(a)）；另做零 LLM 实测披露 True/False 的
  top-5 机械差异。
- 观测仪表（**不改行为**，只记录）：`RecordingLLM` / `RecordingRegistry` 透明代理，
  记 prompt 是否含工具目录、逐工具调用耗时 / 结果形状、LLM 调用数。原始结果原样返回。
- 入库产物**不含数据集 query 文本、不含宿主绝对路径、不含密钥**；原始 trace 落
  `artifacts/trace/`（.gitignore:108，不入库）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
CENSUS_WT = os.path.dirname(HERE)                       # worktree 内的 census 目录（代码）
REPO_ROOT = os.path.dirname(os.path.dirname(CENSUS_WT))  # worktree 根（运行时优先于主树 editable 安装）

# 隔离兜底：显式 manifest/db 路径下 MEMORY_INDEX_DIR 不参与解析（index._explicit），
# 这里把它钉到临时目录，保证任何意外路径都不会碰到生产索引。
os.environ.setdefault("MEMORY_INDEX_DIR", os.path.join(tempfile.gettempdir(), "a2-74-isolated-index"))

for _path in (CENSUS_WT, REPO_ROOT):   # 顺序：REPO_ROOT 最后插入 → 排最前
    if _path not in sys.path:
        sys.path.insert(0, _path)

from memory_agent import _bootstrap  # noqa: E402

_bootstrap.configure_stderr_logging()
_bootstrap.configure_hf_offline()

import multihop as mh  # noqa: E402

from memory_agent.agent_loop import AgentLoop, Budget  # noqa: E402
from memory_agent.agent_loop.llm import (  # noqa: E402
    ProviderSpec,
    build_llm_client,
    resolve_provider,
)
from memory_agent.agent_loop.tools import (  # noqa: E402
    GREP_TOOL,
    HISTORY_TOOL,
    LINKS_TOOL,
    NAV_TOOLS,
    SEARCH_TOOL,
    MemoryNavToolRegistry,
    describe_tools,
)
from memory_agent.eval.harness.scorer import evaluate as score_traces  # noqa: E402
from memory_agent.eval.harness.stats import bootstrap_ci, paired_diffs  # noqa: E402
from memory_agent.eval.metrics import recall_at_k  # noqa: E402
from memory_agent.memory.index import MemoryIndex  # noqa: E402
from memory_agent.memory.retrieval import MemoryRetriever  # noqa: E402
from memory_agent.memory.store import open_store  # noqa: E402
from memory_agent.trace import Trace  # noqa: E402
from ragcore.strategies.default import DefaultRetrievalStrategy  # noqa: E402

# ------------------------------------------------------------------ 常量（预注册口径）

COLLECTION = "multihop_rag"
POOL = 50            # 检索池深度（与 #47 / #49 一致）
K_MAIN = 5           # 每轮 memory_search 的 k（产品默认）
MAX_ROUNDS = 3       # Budget 默认（总轮数）
MAX_EVIDENCE = 20    # Budget 默认（展示给模型的条目上限）
EXCLUDE_RETIRED = False  # R1(a)：交付运行时出厂默认
KS = (1, 5, 10, 15, 20, 50)
CONTROL_ARMS = {"C20": 20, "C5": 5, "C15": 15, "C50": 50}
FALLBACK_BAND = 0.10  # 预注册 §6 的 10% 判定带（判断，不是推导）
INSUFFICIENT_COMPLETED = 190

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.7-flash"

CENSUS_REL = "experiments/agentic-rag-census"
ARTIFACTS = os.path.join(HERE, "artifacts")
TRACE_DIR = os.path.join(ARTIFACTS, "trace")
SMOKE_DIR = os.path.join(HERE, "smoke")
CONTROL_JSON = os.path.join(ARTIFACTS, "control_rankings.json")
A1_TRACES = os.path.join(TRACE_DIR, "a1_traces.jsonl")
RUN_META = os.path.join(ARTIFACTS, "run_meta.json")
SMOKE_RAW = os.path.join(TRACE_DIR, "smoke_raw.jsonl")
SMOKE_JSON = os.path.join(SMOKE_DIR, "smoke_summary.json")
REPORT_JSON = os.path.join(HERE, "report.json")
REPORT_MD = os.path.join(HERE, "report.md")

NAV_TOOL_NAMES = tuple(sorted(NAV_TOOLS))
EXPECTED_CATALOG = tuple(sorted(
    (*NAV_TOOL_NAMES, SEARCH_TOOL, "memory_get")))

_boot = _bootstrap  # 保持引用（lint）


# ------------------------------------------------------------------ 小工具

def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def load_json(path: str):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=1)


def append_jsonl(path: str, record: dict, lock: threading.Lock | None = None) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    if lock is None:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
        return
    with lock:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()


def read_jsonl_last_wins(path: str) -> dict[str, dict]:
    """逐行 JSONL → `{id: record}`（同一 id 多行时**后写者胜**）。"""
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


def mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * pct), len(ordered) - 1)]


def main_worktree() -> str:
    """主工作树路径（`git worktree list --porcelain` 第一项；抄 run_census._main_worktree）。"""
    proc = subprocess.run(["git", "-C", REPO_ROOT, "worktree", "list", "--porcelain"],
                          capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            return line.split(" ", 1)[1].strip()
    return REPO_ROOT


_VOLATILE_SUFFIXES = (".log", ".pid", ".lock", ".tmp")


def dir_signature(path: str) -> str | None:
    """索引内容签名（名字+大小+mtime；排除运行时易变文件）——抄 run_census._dir_signature。

    只证明"未写"：本实验显式 `db_path` / `manifest_path`，从不引用索引指针。
    """
    if not os.path.isdir(path):
        return None
    parts = []
    for root, _dirs, files in os.walk(path):
        for name in sorted(files):
            if name.lower().endswith(_VOLATILE_SUFFIXES):
                continue
            full = os.path.join(root, name)
            try:
                stat = os.stat(full)
            except OSError:
                continue
            parts.append(f"{os.path.relpath(full, path)}:{stat.st_size}:{stat.st_mtime_ns}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def sanitize_proxy_env() -> dict:
    """进程内去掉 `no_proxy`/`NO_PROXY` 的 IPv6 条目（宿主 env 缺陷；照 nav_63_agentic）。"""
    changed: dict[str, str] = {}
    for key in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(key)
        if not raw:
            continue
        parts = [item.strip() for item in raw.split(",") if item.strip()]
        kept = [item for item in parts if not any(ch in item for ch in ":[]")]
        if kept != parts:
            os.environ[key] = ",".join(kept)
            changed[key] = ",".join(kept)
    return changed


# ------------------------------------------------------------------ 装载（纯函数）

def load_eval_set(census_dir: str) -> dict[str, dict]:
    """`mhr####` -> row（含 `query` / `kind` / `relevant` / `gold_answer`）。

    与 `phase_b/common.py::load_eval` 同口径：`load_dataset(data_dir=census_dir/data)` +
    `build_eval_set`（**纯函数**；不调 `ensure_prepared`）。
    """
    corpus, queries = mh.load_dataset(os.path.join(census_dir, "data"))
    base = mh.build_eval_set(corpus, queries)
    out: dict[str, dict] = {}
    for row, item in zip(base["queries"], queries):
        row = dict(row)
        row["gold_answer"] = item.get("answer")
        out[row["id"]] = row
    return out


def sample_ids(census_dir: str) -> list[str]:
    doc = load_json(os.path.join(census_dir, "phase_b", "sample.json"))
    return list(doc["all"])


def open_index(census_dir: str):
    """索引链逐字照 `run_census.open_searcher("base:hybrid", 50)`。返回 `(store, index)`。"""
    base = os.path.join(census_dir, "store", "base")
    store = open_store(db_path=os.path.join(base, "qdrant"),
                       collection_name=COLLECTION, hybrid=False)
    retriever = MemoryRetriever(
        store,
        strategy=DefaultRetrievalStrategy(enable_keyword=True),
        pool_size=POOL,
    )
    index = MemoryIndex(store=store, manifest_path=os.path.join(base, "manifest.json"),
                        retriever_factory=lambda _s: retriever)
    return store, index


def as_int_ids(entry_ids) -> list[int]:
    """`multihop:<i:04d>` → 整数序号（R1(b)：`per_query_ids.json` 存的是序号）。"""
    out = []
    for entry_id in entry_ids:
        text = str(entry_id)
        out.append(int(text.split(":", 1)[1]) if ":" in text else text)
    return out


# ------------------------------------------------------------------ LLM

def load_api_key() -> str | None:
    """key 只从**进程环境**读；缺失时经 `DASHSCOPE_ENV_FILE` 指向的 .env 惰性加载。

    **不落盘 / 不日志 / 不回显**；本文件不写死任何宿主绝对路径。
    """
    key = os.environ.get("DASHSCOPE_API_KEY")
    if key:
        return key
    env_file = os.environ.get("DASHSCOPE_ENV_FILE")
    if env_file and os.path.isfile(env_file):
        try:
            from dotenv import load_dotenv
        except ImportError:
            return None
        load_dotenv(env_file, override=False)
        return os.environ.get("DASHSCOPE_API_KEY")
    return None


def make_llm(base_url: str, model: str):
    """构造运行时 `openai-compat` 客户端（不改运行时；key 经进程环境）。"""
    key = load_api_key()
    if not key:
        raise SystemExit(
            "缺少 LLM 凭据：请设 DASHSCOPE_API_KEY，或设 DASHSCOPE_ENV_FILE 指向含该键的 .env")
    os.environ["MEMORY_AGENT_LLM_API_KEY"] = key
    spec = resolve_provider(ProviderSpec(
        provider="openai-compat", base_url=base_url, model=model,
        temperature=0.0, seed=42))
    client = build_llm_client(spec)
    public = {"provider": spec.provider, "model": spec.model,
              "temperature": spec.temperature, "seed": spec.seed}
    return client, public


class RecordingLLM:
    """透明代理：记录每次调用（耗时 / prompt 字符数），原样返回运行时结果。"""

    def __init__(self, inner):
        self._inner = inner
        self.calls: list[dict] = []
        self.last_messages: list[dict] | None = None

    def complete(self, messages, **kwargs) -> str:
        started = time.time()
        output = self._inner.complete(messages, **kwargs)
        self.calls.append({
            "elapsed_s": round(time.time() - started, 3),
            "prompt_chars": sum(len(m.get("content") or "") for m in messages),
            "system_chars": sum(len(m.get("content") or "") for m in messages
                                if m.get("role") == "system"),
        })
        self.last_messages = messages
        return output


class RecordingRegistry:
    """透明代理：记录每次工具调用（工具名 / 耗时 / 结果形状 / 错误），原样返回。"""

    def __init__(self, inner):
        self._inner = inner
        self.calls: list[dict] = []

    def list_tools(self):
        return self._inner.list_tools()

    def call(self, name, args):
        started = time.time()
        error = None
        result = None
        try:
            result = self._inner.call(name, args)
        except Exception as exc:  # noqa: BLE001 - 记录后**原样抛出**（不改变运行时语义）
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self.calls.append({
                "tool": name,
                "elapsed_s": round(time.time() - started, 4),
                "error": error,
                "n_items": len(result) if isinstance(result, list) else None,
                "result_keys": (sorted(str(key) for key in result)[:12]
                                if isinstance(result, dict) else None),
                "arg_keys": sorted(str(key) for key in (args or {})),
            })
        return result


# ------------------------------------------------------------------ 指标 / 统计

def recalls_for_ranking(ranked: list[str], relevant: list[str], k: int) -> float:
    return round(recall_at_k(ranked, relevant, k), 6)


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> dict:
    if total <= 0:
        return {"lo": None, "hi": None}
    p = successes / total
    denom = 1.0 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return {"lo": round(center - half, 6), "hi": round(center + half, 6)}


def n_needed_for_band(rate: float, band: float = FALLBACK_BAND, cap: int = 200000) -> dict:
    """要多少 N 才能让 95% Wilson CI 排除 `band`（预注册 §6「未决」时的算账）。

    `rate < band` → 需要 CI 上界 < band；`rate > band` → 需要 CI 下界 > band；
    `rate == band` → 加 N 永远排不掉（如实说明）。
    """
    if rate is None:
        return {"needed_n": None, "reason": "no_data"}
    if abs(rate - band) < 1e-12:
        return {"needed_n": None, "reason": "point_estimate_on_band_no_n_can_exclude"}
    n = 200
    while n <= cap:
        successes = int(round(rate * n))
        interval = wilson_interval(successes, n)
        if rate < band and interval["hi"] is not None and interval["hi"] < band:
            return {"needed_n": n, "at_rate": round(successes / n, 6),
                    "wilson": interval, "reason": "upper_bound_below_band"}
        if rate > band and interval["lo"] is not None and interval["lo"] > band:
            return {"needed_n": n, "at_rate": round(successes / n, 6),
                    "wilson": interval, "reason": "lower_bound_above_band"}
        n += 10
    return {"needed_n": None, "reason": f"not_reached_within_{cap}"}


def judge_fallback(bootstrap: dict) -> str:
    lo, hi = bootstrap.get("lo"), bootstrap.get("hi")
    if lo is None or hi is None:
        return "undecided_insufficient_data"
    if lo > FALLBACK_BAND:
        return "confirmed_defect"
    if hi < FALLBACK_BAND:
        return "small_sample_noise"
    return "undecided"


# ------------------------------------------------------------------ Step 0（零 LLM）

def run_step0(args) -> int:
    census_dir = args.census_dir
    store_dir = os.path.join(census_dir, "store", "base")
    ids = sample_ids(census_dir)
    eval_set = load_eval_set(census_dir)
    missing = [qid for qid in ids if qid not in eval_set]
    if missing:
        raise SystemExit(f"sample.json 里的 id 不在评测集：{missing[:5]}")

    before = dir_signature(store_dir)
    store, index = open_index(census_dir)
    rows: list[dict] = []
    noop_rows: list[dict] = []
    try:
        known = set(index.known_ids())
        bad_gold = sorted({gold for qid in ids for gold in eval_set[qid]["relevant"]
                           if gold not in known})
        log(f"[step0] entries={len(known)} gold_missing={len(bad_gold)}")
        index.search("warmup", 1)
        started = time.time()
        for position, qid in enumerate(ids, start=1):
            row = eval_set[qid]
            t0 = time.time()
            hits = index.search(row["query"], k=POOL)
            ranked = [hit["id"] for hit in hits]
            elapsed = round(time.time() - t0, 4)
            recall = {str(k): recalls_for_ranking(ranked, row["relevant"], k) for k in KS}
            rows.append({
                "id": qid, "question_type": row["question_type"], "kind": row["kind"],
                "evidence_count": row["evidence_count"],
                "relevant": list(row["relevant"]), "ranked": ranked,
                "recall": recall, "elapsed_s": elapsed,
            })
            # R1(a)：exclude_retired True/False 的机械差异（零 LLM）
            alt = [hit["id"] for hit in index.search(row["query"], k=K_MAIN,
                                                     exclude_retired=True)]
            main5 = [hit["id"] for hit in index.search(row["query"], k=K_MAIN,
                                                      exclude_retired=EXCLUDE_RETIRED)]
            noop_rows.append({"id": qid, "same_top5": alt == main5,
                              "alt_len": len(alt), "main_len": len(main5)})
            if position % 50 == 0:
                log(f"[step0] {position}/{len(ids)}")
    finally:
        store.close()
    after = dir_signature(store_dir)

    per_query = {}  # 与 #47 证据的逐位一致性
    reference_path = os.path.join(census_dir, "artifacts", "per_query_ids.json")
    reference = load_json(reference_path)["base:hybrid"]
    ref_by_id = {item["id"]: item for item in reference}
    identical_full = 0
    identical_top5 = 0
    identical_top20 = 0
    first_divergences: list[dict] = []
    pos_hist: dict[str, int] = {}
    relevant_mismatch: list[str] = []
    ref_recall_means = {str(k): [] for k in KS}
    for row in rows:
        ref = ref_by_id.get(row["id"])
        if ref is None:
            relevant_mismatch.append(row["id"])
            continue
        mine = as_int_ids(row["ranked"])
        theirs = list(ref["ranked"])
        if set(row["relevant"]) != {mh.article_id(x) for x in (ref.get("relevant") or [])}:
            relevant_mismatch.append(row["id"])
        identical_full += int(mine == theirs)
        identical_top5 += int(mine[:5] == theirs[:5])
        identical_top20 += int(mine[:20] == theirs[:20])
        diff_at = None
        for index_, (left, right) in enumerate(zip(mine, theirs), start=1):
            if left != right:
                diff_at = index_
                break
        if diff_at is None and len(mine) != len(theirs):
            diff_at = min(len(mine), len(theirs)) + 1
        if diff_at is not None:
            pos_hist[str(diff_at)] = pos_hist.get(str(diff_at), 0) + 1
            if len(first_divergences) < 10:
                first_divergences.append({"id": row["id"], "position": diff_at,
                                          "mine": mine[diff_at - 1:diff_at + 2],
                                          "ref": theirs[diff_at - 1:diff_at + 2]})
        for k in KS:
            ref_recall_means[str(k)].append(float((ref.get("recall") or {}).get(str(k), 0.0)))

    answerable = [row for row in rows if row["relevant"]]
    null_rows = [row for row in rows if not row["relevant"]]
    mine_curve = {str(k): round(mean(r["recall"][str(k)] for r in answerable), 6) for k in KS}
    ref_curve = {str(k): round(mean(ref_recall_means[str(k)]), 6) for k in KS}

    noop_diffs = [row for row in noop_rows if not row["same_top5"]]
    control = {
        "meta": {
            "prereg_commit": "fff00c2", "prereg_amend": "55472c4",
            "census_dir_rel": CENSUS_REL,
            "pool": POOL, "prod_k": K_MAIN,
            "exclude_retired": EXCLUDE_RETIRED,
            "arms": CONTROL_ARMS,
        },
        "recall": {
            "n_questions": len(rows),
            "n_answerable": len(answerable),
            "n_null_query": len(null_rows),
            "recomputed_curve": mine_curve,
            "phase_a_curve_same_200": ref_curve,
            "curve_delta": {str(k): round(mine_curve[str(k)] - ref_curve[str(k)], 6)
                            for k in KS},
        },
        "consistency_with_47": {
            "n": len(rows),
            "identical_full_ranked_questions": identical_full,
            "identical_top5_questions": identical_top5,
            "identical_top20_questions": identical_top20,
            "first_divergence_position_global": (
                min(int(pos) for pos in pos_hist) if pos_hist else None),
            "divergence_position_examples": first_divergences,
            "divergence_position_histogram": dict(sorted(pos_hist.items(),
                                                         key=lambda kv: int(kv[0]))),
            "relevant_set_mismatch_questions": relevant_mismatch,
            "gate_passed": identical_full == len(rows),
        },
        "exclude_retired_noop_test": {
            "n": len(noop_rows),
            "same_top5_questions": sum(1 for row in noop_rows if row["same_top5"]),
            "differing_questions": [row["id"] for row in noop_diffs],
            "a1_actual_value": EXCLUDE_RETIRED,
            "note": ("True -> index.search 的 fetch_k = max(k, pool_size=50)；"
                     "False -> fetch_k = k。本语料无 retired 条目 → 语义 no-op；"
                     "此处量的是机械差异。"),
        },
        "store_signature": {"path_rel": f"{CENSUS_REL}/store/base",
                            "sig_before": before, "sig_after": after,
                            "unchanged": before == after},
        "gold_missing_from_index": bad_gold,
        "rows": rows,
    }
    write_json(CONTROL_JSON, control)
    log(f"[step0] recall@5={mine_curve['5']} recall@50={mine_curve['50']} "
        f"identical_full={identical_full}/{len(rows)} "
        f"first_div={control['consistency_with_47']['first_divergence_position_global']} "
        f"store_unchanged={before == after} exclude_retired_noop="
        f"{control['exclude_retired_noop_test']['same_top5_questions']}/{len(noop_rows)}")
    log(f"[out] {CONTROL_JSON}")
    return 0


# ------------------------------------------------------------------ 冒烟（3 题）

def run_smoke(args) -> int:
    census_dir = args.census_dir
    ids = sample_ids(census_dir)[:args.limit]
    eval_set = load_eval_set(census_dir)
    store, index = open_index(census_dir)
    summary: dict = {"ids": ids, "limit": args.limit, "catalog": None, "grep_timing": None,
                     "dispatch_probe": None, "questions": [], "proxy_env_sanitized": None}
    try:
        registry = MemoryNavToolRegistry(index, exclude_retired=EXCLUDE_RETIRED)
        catalog = registry.list_tools()
        names = [tool["name"] for tool in catalog]
        catalog_text = describe_tools(registry)
        summary["catalog"] = {
            "count": len(names),
            "names": sorted(names),
            "expected_8": sorted(EXPECTED_CATALOG),
            "matches_expected": sorted(names) == sorted(EXPECTED_CATALOG),
            "in_prompt_all": all(name in catalog_text for name in names),
            "prompt_chars": len(catalog_text),
        }

        # ---- memory_grep 单次耗时（609 条目全扫 vs 早退）
        timings = {}
        for label, pattern in (("full_scan_no_hit", "zzzz-a2-no-such-token-7421"),
                               ("early_exit_limit50", "the"),
                               ("mid_frequency", "2023")):
            t0 = time.time()
            hits = registry.call(GREP_TOOL, {"pattern": pattern, "limit": 50})
            timings[label] = {"pattern_len": len(pattern), "hits": len(hits),
                              "elapsed_s": round(time.time() - t0, 4)}
        summary["grep_timing"] = timings

        # ---- history / links 的返回形状（§7.2；结构性近空，不算失败）
        probe_entry = index.known_ids()[0]
        history = registry.call(HISTORY_TOOL, {"entry_id": probe_entry, "op": "log"})
        links = registry.call(LINKS_TOOL, {"entry_id": probe_entry})
        summary["history_links_shape"] = {
            "probe_entry": probe_entry,
            "history_keys": sorted(history) if isinstance(history, dict) else None,
            "history_error": history.get("error") if isinstance(history, dict) else None,
            "history_commits": history.get("count") if isinstance(history, dict) else None,
            "links_keys": sorted(links) if isinstance(links, dict) else None,
            "links_error": links.get("error") if isinstance(links, dict) else None,
            "links_chain_len": len(links.get("chain") or []) if isinstance(links, dict) else None,
        }

        # ---- TOOL: 派发的确定性证明（ScriptedLLM；决策写死、执行是真的）
        from memory_agent.eval.harness.stubs import ScriptedLLM
        probe_registry = RecordingRegistry(MemoryNavToolRegistry(
            index, exclude_retired=EXCLUDE_RETIRED))
        script = [f'TOOL: {GREP_TOOL} {json.dumps({"pattern": "the", "limit": 5})}',
                  "ANSWER: smoke dispatch probe"]
        probe_loop = AgentLoop(ScriptedLLM(script), probe_registry,
                               budget=Budget(max_rounds=2, max_evidence=MAX_EVIDENCE),
                               k=K_MAIN)
        probe_trace = probe_loop.run("smoke dispatch probe", trace_id="smoke-dispatch")
        probe_tools = [call.tool for rnd in probe_trace.rounds for call in rnd.tool_calls]
        summary["dispatch_probe"] = {
            "tools_called": probe_tools,
            "grep_executed": GREP_TOOL in probe_tools,
            "grep_result_grep_hits": [call.result_count for rnd in probe_trace.rounds
                                      for call in rnd.tool_calls
                                      if call.tool == GREP_TOOL],
            "evidence_ids": list(probe_trace.final.get("evidence_ids") or []),
            "evidence_from_grep": [entry for rnd in probe_trace.rounds
                                   for call in rnd.tool_calls if call.tool == GREP_TOOL
                                   for entry in call.added_ids],
            "stop": probe_trace.stop.trigger if probe_trace.stop else None,
        }

        # ---- 3 题真 LLM 冒烟
        summary["proxy_env_sanitized"] = sanitize_proxy_env()
        llm, public = make_llm(args.base_url, args.model)
        summary["llm"] = public
        index.search("warmup", 1)
        os.makedirs(TRACE_DIR, exist_ok=True)
        raw_handle = open(SMOKE_RAW, "w", encoding="utf-8")
        try:
            for qid in ids:
                row = eval_set[qid]
                recorder_llm = RecordingLLM(llm)
                recorder = RecordingRegistry(MemoryNavToolRegistry(
                    index, exclude_retired=EXCLUDE_RETIRED))
                loop = AgentLoop(recorder_llm, recorder,
                                 budget=Budget(max_rounds=MAX_ROUNDS,
                                               max_evidence=MAX_EVIDENCE),
                                 k=K_MAIN)
                t0 = time.time()
                error = None
                trace = None
                try:
                    trace = loop.run(row["query"], trace_id=qid)
                except Exception as exc:  # noqa: BLE001
                    error = f"{type(exc).__name__}: {exc}"
                elapsed = round(time.time() - t0, 3)
                if trace is None:
                    summary["questions"].append({"id": qid, "error": error,
                                                 "elapsed_s": elapsed})
                    continue
                tools_called = [call.tool for rnd in trace.rounds
                                for call in rnd.tool_calls]
                evidence = list(trace.final.get("evidence_ids") or [])
                prompt = (recorder_llm.last_messages or [{}])[-1].get("content") or ""
                summary["questions"].append({
                    "id": qid, "kind": row["kind"], "stop": trace.stop.trigger
                    if trace.stop else None,
                    "rounds": len(trace.rounds), "tools_called": tools_called,
                    "nav_used": any(tool in NAV_TOOLS for tool in tools_called),
                    "n_evidence": len(evidence),
                    "evidence_recall": (round(recall_at_k(evidence, row["relevant"],
                                                          len(row["relevant"])), 6)
                                        if row["relevant"] else None),
                    "elapsed_s": elapsed,
                    "llm_calls": len(recorder_llm.calls),
                    "llm_seconds": round(sum(c["elapsed_s"] for c in recorder_llm.calls), 3),
                    "prompt_has_all_tools": all(name in prompt
                                                for name in summary["catalog"]["names"]),
                    "prompt_has_tool_protocol": "TOOL:" in prompt,
                    "tool_call_timings": recorder.calls,
                    "error": error,
                })
                raw_handle.write(json.dumps(
                    {"id": qid, "trace": trace.to_dict(),
                     "llm_calls": recorder_llm.calls,
                     "tool_calls": recorder.calls}, ensure_ascii=False) + "\n")
                raw_handle.flush()
        finally:
            raw_handle.close()
    finally:
        store.close()

    os.makedirs(SMOKE_DIR, exist_ok=True)
    write_json(SMOKE_JSON, summary)
    ok = all(q.get("stop") for q in summary["questions"]) and \
        summary["catalog"]["matches_expected"] and \
        summary["dispatch_probe"]["grep_executed"] and \
        all(q.get("prompt_has_all_tools") for q in summary["questions"])
    log(f"[smoke] catalog={summary['catalog']['count']} "
        f"dispatch_ok={summary['dispatch_probe']['grep_executed']} "
        f"grep_full_scan={timings['full_scan_no_hit']['elapsed_s']}s "
        f"grep_early={timings['early_exit_limit50']['elapsed_s']}s "
        f"prompt_all_tools={all(q.get('prompt_has_all_tools') for q in summary['questions'])}")
    for question in summary["questions"]:
        log(f"[smoke] {question['id']} stop={question.get('stop')} "
            f"rounds={question.get('rounds')} tools={question.get('tools_called')} "
            f"elapsed={question.get('elapsed_s')}s recall={question.get('evidence_recall')}")
    log(f"[out] {SMOKE_JSON}")
    return 0 if ok else 1


# ------------------------------------------------------------------ A1 真跑 N=200

def run_agent(args) -> int:
    census_dir = args.census_dir
    ids = sample_ids(census_dir)
    if args.limit is not None:
        ids = ids[:args.limit]
    eval_set = load_eval_set(census_dir)
    sanitize_proxy_env()
    llm, public = make_llm(args.base_url, args.model)
    store, index = open_index(census_dir)
    lock = threading.Lock()
    started_iso = time.strftime("%Y-%m-%dT%H:%M:%S")
    t_start = time.time()
    pending: list[str] = []
    log(f"[agent] ids={len(ids)} concurrency={args.concurrency} model={public['model']} "
        f"exclude_retired={EXCLUDE_RETIRED}")
    try:
        index.search("warmup", 1)
        done = read_jsonl_last_wins(A1_TRACES)
        pending = [qid for qid in ids
                   if qid not in done or done[qid].get("error")]
        log(f"[agent] resume: done={len(done)} pending={len(pending)}")

        def work(qid: str) -> dict:
            row = eval_set[qid]
            recorder_llm = RecordingLLM(llm)
            recorder = RecordingRegistry(MemoryNavToolRegistry(
                index, exclude_retired=EXCLUDE_RETIRED))
            loop = AgentLoop(recorder_llm, recorder,
                             budget=Budget(max_rounds=MAX_ROUNDS,
                                           max_evidence=MAX_EVIDENCE),
                             k=K_MAIN)
            t0 = time.time()
            try:
                trace = loop.run(row["query"], trace_id=qid)
                record = {"id": qid, "elapsed_s": round(time.time() - t0, 3),
                          "error": None, "llm_calls": len(recorder_llm.calls),
                          "llm_seconds": round(sum(c["elapsed_s"]
                                                   for c in recorder_llm.calls), 3),
                          "tool_observations": recorder.calls,
                          "trace": trace.to_dict()}
            except Exception as exc:  # noqa: BLE001 - 逐题错误如实落盘
                record = {"id": qid, "elapsed_s": round(time.time() - t0, 3),
                          "error": f"{type(exc).__name__}: {exc}",
                          "llm_calls": len(recorder_llm.calls),
                          "llm_seconds": round(sum(c["elapsed_s"]
                                                   for c in recorder_llm.calls), 3),
                          "tool_observations": recorder.calls, "trace": None}
            append_jsonl(A1_TRACES, record, lock)
            return record

        completed = 0
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            futures = {pool.submit(work, qid): qid for qid in pending}
            for future in as_completed(futures):
                qid = futures[future]
                try:
                    record = future.result()
                except Exception as exc:  # noqa: BLE001 - 线程池级兜底
                    record = {"id": qid, "error": f"pool:{type(exc).__name__}: {exc}"}
                completed += 1
                status = "ok" if not record.get("error") else f"ERR {record['error'][:80]}"
                log(f"[agent] {completed}/{len(pending)} {qid} {status} "
                    f"({record.get('elapsed_s')}s)")
    finally:
        store.close()
    wall = round(time.time() - t_start, 1)
    write_json(RUN_META, {
        "prereg_commit": "fff00c2", "prereg_amend": "55472c4",
        "census_dir_rel": CENSUS_REL, "llm": public,
        "loop": {"max_rounds": MAX_ROUNDS, "max_evidence": MAX_EVIDENCE, "k": K_MAIN},
        "exclude_retired": EXCLUDE_RETIRED,
        "n_target": len(ids), "n_pending": len(pending),
        "concurrency": args.concurrency,
        "started": started_iso, "wall_clock_s": wall,
    })
    log(f"[agent] wall_clock={wall}s  [out] {A1_TRACES}  [out] {RUN_META}")
    return 0


# ------------------------------------------------------------------ 报告

def _trace_percentiles(values: list[float]) -> dict:
    return {"n": len(values), "mean": round(mean(values), 4),
            "p50": percentile(values, 0.50), "p95": percentile(values, 0.95),
            "max": max(values) if values else None}


def build_report(args) -> int:
    census_dir = args.census_dir
    control = load_json(CONTROL_JSON)
    ids = sample_ids(census_dir)
    control_rows = {row["id"]: row for row in control["rows"]}
    records = read_jsonl_last_wins(A1_TRACES)
    run_meta = load_json(RUN_META) if os.path.isfile(RUN_META) else {}

    completed_ids = [qid for qid in ids if qid in records and not records[qid].get("error")]
    error_ids = [qid for qid in ids if qid in records and records[qid].get("error")]
    missing_ids = [qid for qid in ids if qid not in records]

    # ---- A1 recall（复用 scorer.evaluate 口径）
    traces = [Trace.from_dict(records[qid]["trace"]) for qid in completed_ids]
    eval_set = {}
    for qid in ids:
        row = control_rows[qid]
        eval_set[qid] = {"id": qid, "kind": row["kind"], "relevant": row["relevant"],
                         "gold_answer": None}
    scored = score_traces(traces, eval_set)
    a1_by_id = {row["id"]: row["evidence_recall"] for row in scored["rows"]}
    a1_overall = scored["overall"]

    # 自证：逐题 recall 与手算一致
    manual = {}
    for qid in completed_ids:
        row = control_rows[qid]
        evidence = list(records[qid]["trace"]["final"].get("evidence_ids") or [])
        manual[qid] = (round(recall_at_k(evidence, row["relevant"],
                                        len(row["relevant"])), 6)
                       if row["relevant"] else None)
    mismatch = [qid for qid in completed_ids if manual[qid] != a1_by_id.get(qid)]

    answerable_completed = [qid for qid in completed_ids if control_rows[qid]["relevant"]]

    def arm_values(k: int) -> dict[str, float]:
        return {qid: round(recall_at_k(control_rows[qid]["ranked"],
                                       control_rows[qid]["relevant"], k), 6)
                for qid in answerable_completed}

    arm_recall = {"A1": {qid: a1_by_id[qid] for qid in answerable_completed}}
    for name, k in CONTROL_ARMS.items():
        arm_recall[name] = arm_values(k)

    metrics = {
        name: {"mean_evidence_recall": round(mean(values.values()), 6),
               "n": len(values)}
        for name, values in arm_recall.items()
    }

    def paired(a_values: dict[str, float], b_values: dict[str, float]) -> dict:
        left = [{"id": qid, "v": a_values[qid]} for qid in answerable_completed]
        right = [{"id": qid, "v": b_values[qid]} for qid in answerable_completed]
        diffs = paired_diffs(left, right, "v")
        ci = bootstrap_ci(diffs)
        return {"n": ci["n"], "mean_delta": ci["mean"], "lo": ci["lo"], "hi": ci["hi"],
                "significant": ci["significant"]}

    layers = {}
    for label, predicate in (
            ("nav_used", lambda qid: _nav_used(records[qid], control_rows[qid])),
            ("search_only", lambda qid: not _nav_used(records[qid], control_rows[qid]))):
        subset = [qid for qid in answerable_completed if predicate(qid)]
        values_a = {qid: arm_recall["A1"][qid] for qid in subset}
        values_c20 = {qid: arm_recall["C20"][qid] for qid in subset}
        diffs = paired_diffs([{"id": q, "v": values_a[q]} for q in subset],
                             [{"id": q, "v": values_c20[q]} for q in subset], "v")
        ci = bootstrap_ci(diffs)
        layers[label] = {
            "n": len(subset),
            "A1_mean_evidence_recall": round(mean(values_a.values()), 6) if subset else None,
            "C20_mean_evidence_recall": round(mean(values_c20.values()), 6) if subset else None,
            "delta": ci["mean"], "ci": {"lo": ci["lo"], "hi": ci["hi"]},
            "significant": ci["significant"],
        }

    # ---- fallback（R1(c)：两个分母）
    stops = {qid: (records[qid]["trace"].get("stop") or {}).get("trigger")
             for qid in completed_ids}
    fallback_ids = [qid for qid, stop in stops.items() if stop == "fallback"]
    n_completed = len(completed_ids)
    flags_completed = [1.0 if stops[qid] == "fallback" else 0.0 for qid in completed_ids]
    flags_all = [1.0 if records.get(qid, {}).get("trace")
                 and stops.get(qid) == "fallback" else 0.0 for qid in ids]
    ci_completed = bootstrap_ci(flags_completed)
    ci_all = bootstrap_ci(flags_all)
    rate_completed = round(len(fallback_ids) / n_completed, 6) if n_completed else None
    judgement = judge_fallback(ci_completed)
    fallback = {
        "n_completed": n_completed, "n_all_questions": len(ids),
        "count_fallback": len(fallback_ids),
        "rate_completed": rate_completed,
        "rate_all": round(len(fallback_ids) / len(ids), 6),
        "ci_completed": {"mean": ci_completed["mean"], "lo": ci_completed["lo"],
                         "hi": ci_completed["hi"]},
        "ci_all": {"mean": ci_all["mean"], "lo": ci_all["lo"], "hi": ci_all["hi"]},
        "wilson_completed": wilson_interval(len(fallback_ids), n_completed),
        "band": FALLBACK_BAND, "judgement": judgement,
        "n_needed_for_band": n_needed_for_band(rate_completed),
        "fallback_ids": fallback_ids,
        "insufficient_evidence": n_completed < INSUFFICIENT_COMPLETED,
    }

    # ---- 描述性
    stop_distribution: dict[str, int] = {}
    tool_call_counts: dict[str, int] = {}
    search_calls_per_question: dict[str, int] = {}
    rounds_distribution: dict[str, int] = {}
    elapsed_values: list[float] = []
    llm_latency: list[float] = []
    grep_latencies: list[float] = []
    history_links = {"memory_history": {"calls": 0, "errors": 0, "shapes": {}},
                     "memory_links": {"calls": 0, "errors": 0, "shapes": {}}}
    for qid in completed_ids:
        record = records[qid]
        trace = record["trace"]
        trigger = stops[qid] or "none"
        stop_distribution[trigger] = stop_distribution.get(trigger, 0) + 1
        rounds_distribution[str(len(trace.get("rounds") or []))] = \
            rounds_distribution.get(str(len(trace.get("rounds") or [])), 0) + 1
        n_search = 0
        for round_ in trace.get("rounds") or []:
            for call in round_.get("tool_calls") or []:
                tool = call.get("tool")
                tool_call_counts[tool] = tool_call_counts.get(tool, 0) + 1
                if tool == SEARCH_TOOL:
                    n_search += 1
        search_calls_per_question[str(n_search)] = \
            search_calls_per_question.get(str(n_search), 0) + 1
        elapsed_values.append(float(record.get("elapsed_s") or 0.0))
        llm_latency.append(float(record.get("llm_seconds") or 0.0))
        for observation in record.get("tool_observations") or []:
            if observation["tool"] == GREP_TOOL:
                grep_latencies.append(float(observation["elapsed_s"]))
            if observation["tool"] in (HISTORY_TOOL, LINKS_TOOL):
                bucket = history_links[observation["tool"]]
                bucket["calls"] += 1
                if observation.get("error"):
                    bucket["errors"] += 1
                shape = ",".join(observation.get("result_keys") or []) or "none"
                bucket["shapes"][shape] = bucket["shapes"].get(shape, 0) + 1

    nav_used_ids = [qid for qid in completed_ids if _nav_used(records[qid], control_rows[qid])]

    # ---- §7.1：gold 是"靠导航工具补进来的"题数
    nav_gold_questions = []
    nav_gold_pairs = 0
    agent_only_gold_questions = []
    for qid in answerable_completed:
        gold = set(control_rows[qid]["relevant"])
        c20 = set(control_rows[qid]["ranked"][:20])
        recovered = set()
        for round_ in records[qid]["trace"].get("rounds") or []:
            for call in round_.get("tool_calls") or []:
                if call.get("tool") in NAV_TOOLS:
                    for entry in call.get("added_ids") or []:
                        if entry in gold:
                            recovered.add(entry)
        if recovered:
            nav_gold_questions.append(qid)
            nav_gold_pairs += len(recovered)
        final_gold = gold & set(records[qid]["trace"]["final"].get("evidence_ids") or [])
        if final_gold - c20:
            agent_only_gold_questions.append(qid)

    # 首轮检索（C5）缺、A1 展示集有 → agent 把 gold 补进来的题数
    a1_gold_more_than_first_search = [
        qid for qid in answerable_completed
        if set(control_rows[qid]["relevant"]) & (
            set(records[qid]["trace"]["final"].get("evidence_ids") or [])
            - set(control_rows[qid]["ranked"][:K_MAIN]))
    ]

    descriptive = {
        "stop_distribution": dict(sorted(stop_distribution.items())),
        "nav_tool_use_rate": round(len(nav_used_ids) / n_completed, 6) if n_completed else None,
        "nav_used_questions": len(nav_used_ids),
        "tool_call_counts": dict(sorted(tool_call_counts.items())),
        "search_calls_per_question": dict(sorted(search_calls_per_question.items())),
        "rounds_distribution": dict(sorted(rounds_distribution.items())),
        "elapsed_s": _trace_percentiles(elapsed_values),
        "llm_seconds_per_question": _trace_percentiles(llm_latency),
        "memory_grep_latency_s": _trace_percentiles(grep_latencies),
        "memory_grep_calls": len(grep_latencies),
        "smoke_grep_timing": (load_json(SMOKE_JSON).get("grep_timing")
                              if os.path.isfile(SMOKE_JSON) else None),
        "history_links": history_links,
        "errors": {"count": len(error_ids), "ids": error_ids,
                   "details": {qid: records[qid].get("error") for qid in error_ids}},
        "missing": {"count": len(missing_ids), "ids": missing_ids},
        "llm_calls_total": sum(int(records[qid].get("llm_calls") or 0)
                               for qid in completed_ids),
    }

    section7 = {
        "snippet_limit_chars": 240,
        "gold_fact_position_p50_chars": 2316,
        "nav_recovered_gold_questions": {"count": len(nav_gold_questions),
                                         "ids": nav_gold_questions,
                                         "gold_pairs": nav_gold_pairs},
        "final_evidence_beats_c20_questions": {"count": len(agent_only_gold_questions),
                                               "ids": agent_only_gold_questions},
        "final_evidence_beats_first_search_top5": {
            "count": len(a1_gold_more_than_first_search),
            "ids": a1_gold_more_than_first_search},
        "history_links_structurally_empty": (
            "文章 gitignored（不在 repo 的 git 历史里）+ 无 supersede 链 → "
            "memory_history / memory_links 结构性近空；调用次数与返回形状见 descriptive，"
            "不算工具失败。"),
    }

    rows = []
    for qid in ids:
        record = records.get(qid)
        row = control_rows[qid]
        base = {"id": qid, "kind": row["kind"], "question_type": row["question_type"],
                "evidence_count": row["evidence_count"],
                "gold_size": len(row["relevant"]),
                "c20_evidence_recall": (round(recall_at_k(row["ranked"], row["relevant"], 20), 6)
                                        if row["relevant"] else None),
                "c5_evidence_recall": (round(recall_at_k(row["ranked"], row["relevant"], 5), 6)
                                       if row["relevant"] else None)}
        if record is None:
            base.update({"status": "missing", "error": None})
        elif record.get("error"):
            base.update({"status": "error", "error": record["error"],
                         "elapsed_s": record.get("elapsed_s")})
        else:
            trace = record["trace"]
            calls = [call.get("tool") for round_ in (trace.get("rounds") or [])
                     for call in (round_.get("tool_calls") or [])]
            base.update({
                "status": "completed",
                "stop": stops[qid],
                "rounds": len(trace.get("rounds") or []),
                "tools_called": calls,
                "nav_used": bool([tool for tool in calls if tool in NAV_TOOLS]),
                "n_search": sum(1 for tool in calls if tool == SEARCH_TOOL),
                "n_evidence": len(trace.get("final", {}).get("evidence_ids") or []),
                "evidence_ids": list(trace.get("final", {}).get("evidence_ids") or []),
                "a1_evidence_recall": a1_by_id.get(qid),
                "elapsed_s": record.get("elapsed_s"),
                "llm_calls": record.get("llm_calls"),
                "error": None,
            })
        rows.append(base)

    deviations = _deviations(mismatch)
    report = {
        "meta": {
            "ticket": 74, "phase": "A2",
            "prereg_commit": "fff00c2", "prereg_amend": "55472c4",
            "census_dir_rel": CENSUS_REL,
            "runtime": "memory_agent/agent_loop (delivered, unmodified)",
            "tools": "MemoryNavToolRegistry (8 tools)",
            "loop": {"max_rounds": MAX_ROUNDS, "max_evidence": MAX_EVIDENCE, "k": K_MAIN},
            "exclude_retired": EXCLUDE_RETIRED,
            "arms": {"A1": "agent loop, evidence = trace.final.evidence_ids",
                     "C20": "one-shot top-20 (main control, quota-matched)",
                     "C5": "one-shot top-5 (product default)",
                     "C15": "one-shot top-15 (descriptive)",
                     "C50": "one-shot top-50 (descriptive)"},
            "primary_metric": "mean_evidence_recall (memory_agent.eval.harness.scorer.evaluate)",
            "llm": run_meta.get("llm"),
            "sampling": {"n_questions": len(ids),
                         "n_answerable": sum(1 for qid in ids if control_rows[qid]["relevant"]),
                         "n_null_query": sum(1 for qid in ids
                                             if not control_rows[qid]["relevant"])},
            "run": {"completed": n_completed, "errors": len(error_ids),
                    "missing": len(missing_ids),
                    "wall_clock_s": run_meta.get("wall_clock_s"),
                    "concurrency": run_meta.get("concurrency")},
            "note": ("外部语料只作机制证据，不声称本 KB 增益；不判答案正确率。"
                     "入库文件不含数据集 query 文本 / 宿主绝对路径 / 密钥。"),
        },
        "step0": {key: value for key, value in control.items() if key != "rows"},
        "metrics": metrics,
        "paired": {
            "A1_minus_C20": paired(arm_recall["A1"], arm_recall["C20"]),
            "A1_minus_C5": paired(arm_recall["A1"], arm_recall["C5"]),
            "A1_minus_C15": paired(arm_recall["A1"], arm_recall["C15"]),
            "A1_minus_C50": paired(arm_recall["A1"], arm_recall["C50"]),
            "layers": layers,
        },
        "fallback": fallback,
        "descriptive": descriptive,
        "section7": section7,
        "selfcheck": {"scorer_vs_manual_recall_mismatches": mismatch,
                      "scorer_overall": a1_overall},
        "deviations": deviations,
        "rows": rows,
    }
    write_json(REPORT_JSON, report)
    with open(REPORT_MD, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report))
    log(f"[report] A1={metrics['A1']['mean_evidence_recall']} "
        f"C20={metrics['C20']['mean_evidence_recall']} "
        f"delta={report['paired']['A1_minus_C20']['mean_delta']} "
        f"CI=[{report['paired']['A1_minus_C20']['lo']},"
        f"{report['paired']['A1_minus_C20']['hi']}] "
        f"fallback_rate={fallback['rate_completed']} judgement={fallback['judgement']}")
    log(f"[out] {REPORT_JSON}")
    log(f"[out] {REPORT_MD}")
    return 0


def _nav_used(record: dict, control_row: dict) -> bool:
    if not record or not record.get("trace"):
        return False
    for round_ in record["trace"].get("rounds") or []:
        for call in round_.get("tool_calls") or []:
            if call.get("tool") in NAV_TOOLS:
                return True
    return False


def _deviations(mismatch: list[str]) -> list[str]:
    items = []
    if mismatch:
        items.append(f"scorer.evaluate 与手算 recall 不一致 {len(mismatch)} 题（id 见 selfcheck）")
    return items


def _fmt(value) -> str:
    return f"{value:.4f}" if isinstance(value, float) else str(value)


def render_markdown(report: dict) -> str:
    meta = report["meta"]
    step0 = report["step0"]
    lines = [
        "# Phase A″ 报告（ticket #74）— 真 agent 循环 × MultiHop-RAG，N=200",
        "",
        f"- 预注册：`prereg_commit={meta['prereg_commit']}` + R1 `prereg_amend={meta['prereg_amend']}`"
        "（同目录 `PREREGISTRATION.md`）。",
        f"- 被测对象：{meta['runtime']} + {meta['tools']}；`exclude_retired={meta['exclude_retired']}`"
        "（= 运行时出厂默认，R1(a)）。",
        f"- 循环：`Budget(max_rounds={meta['loop']['max_rounds']}, "
        f"max_evidence={meta['loop']['max_evidence']})`、`k={meta['loop']['k']}`；"
        f"LLM：`{meta['llm'].get('model') if meta['llm'] else 'n/a'}` / "
        f"T={meta['llm'].get('temperature') if meta['llm'] else 'n/a'} / "
        f"seed={meta['llm'].get('seed') if meta['llm'] else 'n/a'}。",
        f"- 题集：{meta['sampling']['n_questions']} 条（可答 {meta['sampling']['n_answerable']} + "
        f"null_query {meta['sampling']['n_null_query']}）；主指标只在可答子集上算。",
        f"- 完成 {meta['run']['completed']} / 错误 {meta['run']['errors']} / 缺 {meta['run']['missing']}；"
        f"并发 {meta['run']['concurrency']}；墙钟 {meta['run']['wall_clock_s']}s。",
        f"- **边界**：外部语料只作机制证据，**不声称本 KB 增益**；**不判答案正确率**"
        "（未调裁判 LLM，`answer` 随 trace 落盘）。",
        "",
        "## 1. Step 0（零 LLM）— 控制臂重算 + 与 #47 一致性",
        "",
        "| k | 本次重算 recall@k（200 题） | #47 同 200 题 recall@k | Δ |",
        "|---|---|---|---|",
    ]
    for k in KS:
        lines.append(f"| {k} | {_fmt(step0['recall']['recomputed_curve'][str(k)])} | "
                     f"{_fmt(step0['recall']['phase_a_curve_same_200'][str(k)])} | "
                     f"{step0['recall']['curve_delta'][str(k)]} |")
    cons = step0["consistency_with_47"]
    lines += [
        "",
        f"- **逐位一致性（gate）**：full-ranked 逐位相同 **{cons['identical_full_ranked_questions']}"
        f"/{cons['n']}**；top-5 相同 {cons['identical_top5_questions']}/{cons['n']}；"
        f"top-20 相同 {cons['identical_top20_questions']}/{cons['n']}；"
        f"**首次分歧位置 = {cons['first_divergence_position_global']}**"
        f"（None = 无分歧）；gate_passed = **{cons['gate_passed']}**。",
        f"- relevant 集合不一致题数：{len(cons['relevant_set_mismatch_questions'])}。",
        f"- id 映射：`per_query_ids.json` 存**整数序号**（R1(b)），核对前统一为同一种表示。",
        f"- store 目录签名：before `{step0['store_signature']['sig_before']}` / "
        f"after `{step0['store_signature']['sig_after']}` → **未变 = "
        f"{step0['store_signature']['unchanged']}**（只读证明）。",
        f"- 金标不在索引里的槽位：{len(step0['gold_missing_from_index'])}。",
        "",
        "### R1(a) `exclude_retired` 的机械差异（零 LLM 实测）",
        "",
        f"- 同 query 下 `exclude_retired=True` vs `False` 的 top-5 逐位相同题数："
        f"**{step0['exclude_retired_noop_test']['same_top5_questions']}/"
        f"{step0['exclude_retired_noop_test']['n']}**；不同题 "
        f"{len(step0['exclude_retired_noop_test']['differing_questions'])} 条。",
        f"- A1 实际用值：**{step0['exclude_retired_noop_test']['a1_actual_value']}**"
        "（运行时出厂默认）。**不得**把本票结果说成「延续 #63 同一设置下的复核」。",
        "",
        "## 2. 三臂召回与配对（主指标 = mean_evidence_recall）",
        "",
        "| 臂 | mean_evidence_recall | n |",
        "|---|---|---|",
    ]
    for name in ("A1", "C20", "C5", "C15", "C50"):
        if name in report["metrics"]:
            lines.append(f"| {name} | {_fmt(report['metrics'][name]['mean_evidence_recall'])} | "
                         f"{report['metrics'][name]['n']} |")
    lines += ["", "| 比较 | Δ | 95% CI | 显著 | n |", "|---|---|---|---|---|"]
    for name, key in (("主：A1 − C20", "A1_minus_C20"), ("次：A1 − C5", "A1_minus_C5"),
                      ("描述：A1 − C15", "A1_minus_C15"), ("描述：A1 − C50", "A1_minus_C50")):
        block = report["paired"][key]
        lines.append(f"| {name} | {block['mean_delta']} | "
                     f"[{block['lo']}, {block['hi']}] | {block['significant']} | {block['n']} |")
    lines += ["", "### 预注册分层（`nav_used` / `search_only`）", "",
              "| 层 | n | A1 | C20 | Δ | 95% CI | 显著 |", "|---|---|---|---|---|---|---|"]
    for label in ("nav_used", "search_only"):
        layer = report["paired"]["layers"][label]
        lines.append(f"| {label} | {layer['n']} | {layer['A1_mean_evidence_recall']} | "
                     f"{layer['C20_mean_evidence_recall']} | {layer['delta']} | "
                     f"[{layer['ci']['lo']}, {layer['ci']['hi']}] | {layer['significant']} |")

    fb = report["fallback"]
    lines += [
        "",
        "## 3. `fallback` 协议失配率（预注册 §6，R1(c) 双分母）",
        "",
        f"- `fallback / completed` = **{fb['rate_completed']}** "
        f"（{fb['count_fallback']}/{fb['n_completed']}）；95% bootstrap CI "
        f"[{fb['ci_completed']['lo']}, {fb['ci_completed']['hi']}]；"
        f"Wilson CI [{fb['wilson_completed']['lo']}, {fb['wilson_completed']['hi']}]。",
        f"- `fallback / 200`（保守上界）= **{fb['rate_all']}**；CI "
        f"[{fb['ci_all']['lo']}, {fb['ci_all']['hi']}]。",
        f"- 判定带 {fb['band']}（开跑前写下）→ **判定：{fb['judgement']}**。",
        f"- 未决时的算账（Wilson，另加 N）：{json.dumps(fb['n_needed_for_band'], ensure_ascii=False)}",
        f"- 完成题数 < {INSUFFICIENT_COMPLETED} 则声明证据不足："
        f"**{fb['insufficient_evidence']}**（completed={fb['n_completed']}）。",
        "",
        "## 4. 描述性项",
        "",
        f"- 停止触发器分布：`{json.dumps(report['descriptive']['stop_distribution'], ensure_ascii=False)}`",
        f"- nav 工具使用率：{report['descriptive']['nav_tool_use_rate']} "
        f"（{report['descriptive']['nav_used_questions']}/{meta['run']['completed']}）。",
        f"- 逐工具调用次数：`{json.dumps(report['descriptive']['tool_call_counts'], ensure_ascii=False)}`",
        f"- 每题 `memory_search` 次数分布："
        f"`{json.dumps(report['descriptive']['search_calls_per_question'], ensure_ascii=False)}`",
        f"- 轮数分布：`{json.dumps(report['descriptive']['rounds_distribution'], ensure_ascii=False)}`",
        f"- 每题耗时 p50/p95/max (s)：{report['descriptive']['elapsed_s']['p50']} / "
        f"{report['descriptive']['elapsed_s']['p95']} / {report['descriptive']['elapsed_s']['max']}"
        f"（mean {report['descriptive']['elapsed_s']['mean']}）。",
        f"- LLM 单题耗时 p50/p95/max (s)：{report['descriptive']['llm_seconds_per_question']['p50']} / "
        f"{report['descriptive']['llm_seconds_per_question']['p95']} / "
        f"{report['descriptive']['llm_seconds_per_question']['max']}；"
        f"LLM 调用总数 {report['descriptive']['llm_calls_total']}。",
        f"- `memory_grep` 单次耗时（本次 {report['descriptive']['memory_grep_calls']} 次调用）"
        f"p50/p95/max (s)：{report['descriptive']['memory_grep_latency_s']['p50']} / "
        f"{report['descriptive']['memory_grep_latency_s']['p95']} / "
        f"{report['descriptive']['memory_grep_latency_s']['max']}。",
        f"- 冒烟实测 `memory_grep`（609 条目）：`"
        f"{json.dumps(report['descriptive']['smoke_grep_timing'], ensure_ascii=False)}`",
        f"- `memory_history` / `memory_links` 调用与形状：`"
        f"{json.dumps(report['descriptive']['history_links'], ensure_ascii=False)}`",
        f"- 错误题数：{report['descriptive']['errors']['count']}（id 见 report.json）；"
        f"缺失题数：{report['descriptive']['missing']['count']}。",
        "",
        "## 5. §7 两条口径（如实）",
        "",
        f"1. `MemoryIndex.search` 给模型的 `snippet` 只有前 "
        f"{report['section7']['snippet_limit_chars']} 字，而 gold fact 在文章里的位置 "
        f"p50 ≈ {report['section7']['gold_fact_position_p50_chars']} 字 → 首轮检索结果天生看不全。",
        f"   - A1 展示集里包含 gold、但**不在 C20 top-20**（= 靠 agent 多轮/工具补进来）的题数："
        f"**{report['section7']['final_evidence_beats_c20_questions']['count']}**。",
        f"   - 靠**导航工具**（`memory_read`/`memory_grep`/…）把 gold 补进展示集的题数："
        f"**{report['section7']['nav_recovered_gold_questions']['count']}**"
        f"（gold 槽位 {report['section7']['nav_recovered_gold_questions']['gold_pairs']} 个）。",
        f"   - 展示集 gold 超出首轮 `memory_search` top-5 的题数："
        f"**{report['section7']['final_evidence_beats_first_search_top5']['count']}**。",
        f"2. `memory_history` / `memory_links` 结构性近空："
        f"{report['section7']['history_links_structurally_empty']}",
        "",
        "## 6. 自证",
        "",
        f"- `scorer.evaluate` 与手算逐题 recall 不一致题数："
        f"{len(report['selfcheck']['scorer_vs_manual_recall_mismatches'])}。",
        f"- scorer overall：`{json.dumps(report['selfcheck']['scorer_overall'], ensure_ascii=False)}`。",
        "",
        "## 7. 与预注册的偏差",
        "",
    ]
    if report["deviations"]:
        lines += [f"- {item}" for item in report["deviations"]]
    else:
        lines.append("无。")
    lines += ["", "## 8. 边界", "",
              "- 一次观测，不是可复现性声明（LLM 非逐字节确定）；trace 仍盖 `run_hash` 供重放。",
              "- 三臂共用**同一份** index/store（Qdrant local 同进程单 client）。",
              "- `memory_get` 属检索面（不参与 nav 使用率口径）；`memory_list` / 全局 "
              "`memory_outline` 只导航、不带顶层 id，不占证据额度。",
              "- 入库产物不含数据集 query 文本 / 宿主绝对路径 / 密钥；原始 trace 已被 .gitignore "
              "覆盖，不入库。", ""]
    return "\n".join(lines)


# ------------------------------------------------------------------ main

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="A2 (#74) runner")
    parser.add_argument("--step0", action="store_true", help="零 LLM：重算控制臂排名")
    parser.add_argument("--smoke", action="store_true", help="3 题冒烟（真 LLM）")
    parser.add_argument("--agent", action="store_true", help="A1 真跑（可断点续跑）")
    parser.add_argument("--report", action="store_true", help="出 report.json / report.md")
    parser.add_argument("--census-dir", default=None,
                        help="census 目录（默认解析到主树）")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args(argv)
    args.census_dir = args.census_dir or os.path.join(
        main_worktree(), "experiments", "agentic-rag-census")
    if not os.path.isdir(args.census_dir):
        raise SystemExit(f"census 目录不存在：{args.census_dir}")
    if not any((args.step0, args.smoke, args.agent, args.report)):
        parser.error("至少给一个动作：--step0 / --smoke / --agent / --report")
    status = 0
    if args.step0:
        status |= run_step0(args)
    if args.smoke:
        status |= run_smoke(args)
    if args.agent:
        status |= run_agent(args)
    if args.report:
        status |= build_report(args)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
