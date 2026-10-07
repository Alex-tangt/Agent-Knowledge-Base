"""#63 导航工具集评测：**脚本化 LLM 回放**（工具派发通路）+ 与 #71 一次性检索的 paired 对照。

> ## 能声称什么 / 不能声称什么（先读这段）
>
> - **脚本臂（`--run`，默认，无需 LLM）**：用 `harness` 的 `ScriptedLLM` 把**决策**写死
>   （= 照 #71 探针的参考动作点名工具），但**执行**是真的：
>   `AgentLoop` 解析 `TOOL:` → `MemoryNavToolRegistry` → 真索引 / 真文件 / 真 `git`。
>   它证明的是 **① 工具可达 + ② 派发通路 + ③ 确定性重放**，**不是** agent 的检索能力
>   （决策由脚本给出 = 决策层有答案泄漏）。paired Δ 因此仍只是「**面**的上界」，与 #71 同口径。
> - **agent 臂（`--agent`，需要真 LLM）**：给 `AgentLoop` 一个真 provider（默认 `opencode-server`
>   借主对话；也可 `openai-compat`），让**模型自己**决定用哪个工具。这一臂的 paired Δ 才是
>   「agentic search vs 一次性检索」的**显著性**证据。本机无 LLM 时该臂不跑，证据标注未验。
>
> ## 隔离纪律
>
> `MEMORY_INDEX_DIR` 指向 `%TEMP%` 的**生产代副本**；惰性刷新只写副本（与 #71 `--baseline` 同法），
> 脚本自身**没有任何写路径**，并在前后比对生产索引目录签名自证零写入。
> 对照臂 = `eval_71_nav_baseline.json`（#71 提交的 19 条可寻址探针一次性检索基线）。

    $py   = "D:\\python_work\\work2026-4\\Agent-Knowledge-Base\\venv\\Scripts\\python.exe"
    $env:PYTHONPATH = "D:\\python_work\\work2026-4\\wk-63-nav"

    # ① 脚本臂（无 LLM；回放通路 + 可达性 + paired）
    & $py memory_agent/eval/nav_63_agentic.py --run --out memory_agent/eval/nav_63_results.json

    # ② agent 臂（有 LLM 时；provider 一把切换，不改代码）
    & $py memory_agent/eval/nav_63_agentic.py --agent --provider opencode-server `
        --base-url http://127.0.0.1:4096 --out memory_agent/eval/nav_63_results.json
    & $py memory_agent/eval/nav_63_agentic.py --agent --provider openai-compat `
        --base-url https://<host>/v1 --model <model> --out memory_agent/eval/nav_63_results.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time

# 只 import **不触碰 `memory_agent.settings`** 的模块：settings 在 import 时读 env，
# 所以索引根 / 语料配置必须在 import 任何重模块之前 pin 好（顺序与 eval_71_nav 一致）。
from memory_agent.eval.eval_71_nav import (
    EVAL_ENV_PINS,
    SELF_EXCLUDE_PREFIXES,
    _dir_signature,
    _prepare_temp_index,
    _temp_readonly_config,
    load_probes,
    main_worktree,
)

HERE = os.path.dirname(os.path.abspath(__file__))
PROBES_JSON = os.path.join(HERE, "standard_sets", "nav_probes_71.json")
BASELINE_JSON = os.path.join(HERE, "eval_71_nav_baseline.json")

#: paired 层的三面（#71 的 19 条可寻址探针都在这些面里）。
PAIRED_FACES = ("grep", "read", "outline")

#: 本票自己的产物（证据 md / 实验记录）也必须排除出语料——它们天然含全部探针 token。
SELF_EXCLUDE_EXTRA = (
    "memory_agent/eval/nav_63",
    "experiments/nav-tools-63/",
)


def sanitize_proxy_env() -> dict:
    """把 `no_proxy` / `NO_PROXY` 里的 IPv6 条目去掉（**进程内**，不落盘）。

    宿主 `NO_PROXY` 含带方括号的 IPv6（如 `[::1]`）时 httpx 建 client 会抛
    `InvalidURL: Invalid port: ':1]'`（#62 已记录，正解是改宿主 env）。本机 env 块里
    **同一个键有重复项**，PowerShell 改不动 Python 看到的那一份，所以在进程内改。
    只删 IPv6-ish 条目（含 `:` / `[` / `]`），保留 `localhost,127.0.0.1,...`。
    """
    changed: dict[str, str] = {}
    for key in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(key)
        if not raw:
            continue
        parts = [item.strip() for item in raw.split(",") if item.strip()]
        kept = [item for item in parts if not any(ch in item for ch in ":[]")]
        if kept != parts:
            cleaned = ",".join(kept)
            os.environ[key] = cleaned
            changed[key] = cleaned
    return changed


# ---------------------------------------------------------------- 隔离环境

def setup_isolated_index(prod_index: str, index_dir: str) -> dict:
    """把生产代复制到临时索引根，并把 env pin 到临时目录（返回隔离快照）。"""
    copy_info = _prepare_temp_index(prod_index, index_dir)
    main_root = main_worktree()
    config = _temp_readonly_config(
        os.path.join(main_root, "memory_agent", "readonly_repos.json"), main_root, index_dir)
    overlay_path = os.path.join(index_dir, "overlay_63.json")
    with open(overlay_path, "w", encoding="utf-8") as handle:
        json.dump({"include": [], "exclude": [
            os.path.join(main_root, prefix).replace("\\", "/") + "*"
            for prefix in (*SELF_EXCLUDE_PREFIXES, *SELF_EXCLUDE_EXTRA)]},
            handle, ensure_ascii=False, indent=1)
    os.environ["MEMORY_INDEX_DIR"] = index_dir
    os.environ["MEMORY_READONLY_REPOS_CONFIG"] = config
    os.environ["MEMORY_OVERLAY_CONFIG"] = overlay_path
    for key, value in EVAL_ENV_PINS.items():
        os.environ[key] = value
    return {**copy_info, "config": config, "overlay": overlay_path, "main_root": main_root}


# ------------------------------------------------------------------ 计划 / 判定

def scripted_plan(probe: dict, *, tolerance: int = 10) -> list[tuple[str, dict]]:
    """把探针的**参考动作**翻译成对**真工具**的调用（决策由脚本给出）。

    read 面的行窗按探针的 **tolerance（默认 ±10）** 加宽：`nav_probes_71.spec.md` §4 的
    ③ 位置判据就是"锚在记录行 ±tolerance 内"，行窗位移只记 `window_shifted` **告警**。
    加宽后的读窗口正是"吸收文档漂移"的导航动作（否则要把探针的陈旧行窗当工具失败——那是
    拿 #71 的位置元数据当被测语义，spec 明确说那是告警）。
    """
    from memory_agent.agent_loop.tools import GREP_TOOL, OUTLINE_TOOL, READ_TOOL

    face = probe["face"]
    expected = probe["expected"]
    action = probe["action"]
    target = probe.get("baseline_entry_id")
    if face == "grep":
        return [(GREP_TOOL, {"pattern": action["pattern"],
                             "glob": action.get("globs") or [],
                             "limit": 50})]
    if face == "read":
        lo, hi = expected["line_range"]
        recorded = expected.get("span_line") or expected.get("anchor_line")
        if recorded:
            lo = min(lo, recorded - tolerance)
            hi = max(hi, recorded + tolerance + (expected.get("line_width") or 1))
        return [(READ_TOOL, {"entry_id": target, "start_line": lo, "end_line": hi})]
    if face == "outline":
        return [(OUTLINE_TOOL, {"entry_id": target})]
    raise ValueError(f"不支持的 face：{face}")


def verify_probe(probe: dict, results: list) -> tuple[bool, str]:
    """内容级判定：工具结果里**真能看到探针的语义锚**才算命中。"""
    from memory_agent.agent_loop.tools import GREP_TOOL

    expected = probe["expected"]
    face = probe["face"]
    target = probe.get("baseline_entry_id")
    if not results or not isinstance(results[0], (list, dict)):
        return False, "empty_result"
    first = results[0]
    if isinstance(first, dict) and first.get("error"):
        return False, f"tool_error:{first['error']}"
    if face == "grep":
        hits = first if isinstance(first, list) else []
        ids = {hit.get("id") for hit in hits}
        seen = any(expected["span"] in str(hit.get("text", "")) for hit in hits)
        if seen and target in ids:
            return True, "span_and_entry"
        return False, ("span_not_found" if not seen else "entry_not_in_hits")
    if face == "read":
        if not isinstance(first, dict):
            return False, "bad_shape"
        if expected["span"] in str(first.get("text", "")):
            return True, "span_in_window"
        return False, "span_outside_window"
    if face == "outline":
        if not isinstance(first, dict):
            return False, "bad_shape"
        ok = any(item.get("text") == expected["heading"]
                 and item.get("level") == expected["level"]
                 for item in first.get("headings") or [])
        return (True, "heading_found") if ok else (False, "heading_missing")
    return False, f"unsupported_face:{face}"


def detect_stale_window(registry, probe: dict) -> int | None:
    """read 探针未命中时：锚**还在不在**？在的话给出实际行（记录行窗口已过期）。"""
    from memory_agent.agent_loop.tools import GREP_TOOL

    expected = probe["expected"]
    target = probe.get("baseline_entry_id")
    hits = registry.call(GREP_TOOL, {"pattern": expected["span"],
                                     "glob": [expected["file"]], "limit": 50})
    if not isinstance(hits, list):
        return None
    for hit in hits:
        if hit.get("id") == target:
            return int(hit["line"])
    return None


# ------------------------------------------------------------- 脚本臂（直调）

def run_scripted(registry, probes: list[dict], catalog: dict[str, dict]) -> list[dict]:
    rows = []
    for probe in probes:
        target = probe.get("baseline_entry_id")
        usable = target in catalog
        calls = scripted_plan(probe)
        results = []
        for tool, args in calls:
            results.append(registry.call(tool, args))
        ok, reason = verify_probe(probe, results)
        actual_line = None
        if usable and probe["face"] == "read":
            # 诊断：锚在**当前文档**里的实际行（顺带核对记录行窗是否已过期）。
            actual_line = detect_stale_window(registry, probe)
        rows.append({
            "id": probe["id"], "face": probe["face"], "scope": probe.get("scope"),
            "difficulty": probe["difficulty"], "target": target,
            "usable": usable,
            "tool_calls": [{"tool": tool, "args": args} for tool, args in calls],
            "hit": bool(ok), "reason": reason,
            "recorded_window_shifted": (
                actual_line is not None
                and not (probe["expected"]["line_range"][0]
                         <= actual_line <= probe["expected"]["line_range"][1])),
            "anchor_actual_line": actual_line,
        })
    return rows


# ------------------------------------------------- 脚本臂（harness 回放通路）

class NavOnlyTools:
    """`AgentLoop` 用的注册表：**导航工具是真的**，`memory_search` 打桩返回 []。

    为什么打桩：回放只想证明「`TOOL:` → 派发 → 结果进证据」这条通路，不想为了首轮检索
    加载 BGE-M3；**检索臂不在这里度量**（它来自 #71 提交的基线）。
    """

    def __init__(self, registry):
        self._registry = registry
        self.search_calls: list[dict] = []

    def list_tools(self):
        return self._registry.list_tools()

    def call(self, name, args):
        if name == "memory_search":
            self.search_calls.append(dict(args or {}))
            return []
        return self._registry.call(name, args)


def run_replay(registry, probes: list[dict], *, rounds: int = 2) -> dict:
    """用 `ScriptedLLM` 回放：每个探针跑两遍，核对派发 / 证据 / `run_hash` 确定性。"""
    from memory_agent.agent_loop import AgentLoop, Budget
    from memory_agent.eval.harness.stubs import ScriptedLLM

    rows = []
    hashes = []
    for probe in probes:
        calls = scripted_plan(probe)
        script = [f"TOOL: {tool} {json.dumps(args, ensure_ascii=False)}"
                  for tool, args in calls]
        script.append("ANSWER: (scripted replay: 不判答案，只证通路)")
        per_probe = []
        for _ in range(2):
            tools = NavOnlyTools(registry)
            loop = AgentLoop(ScriptedLLM(script), tools,
                             budget=Budget(max_rounds=max(1, rounds + 1)))
            trace = loop.run(probe["query"], trace_id=probe["id"])
            per_probe.append({
                "run_hash": trace.meta.get("run_hash"),
                "evidence_ids": list(trace.final.get("evidence_ids") or []),
                "tools_called": [call.tool for rnd in trace.rounds for call in rnd.tool_calls],
                "search_stubbed": bool(tools.search_calls),
                "stop": trace.stop.trigger if trace.stop else None,
            })
        first, second = per_probe
        target = probe.get("baseline_entry_id")
        rows.append({
            "id": probe["id"], "face": probe["face"],
            "tools_called": first["tools_called"],
            "expected_tools": [tool for tool, _args in calls],
            "dispatched": all(tool in first["tools_called"] for tool, _args in calls),
            "target_in_evidence": target in first["evidence_ids"],
            "search_stubbed": first["search_stubbed"],
            "deterministic": first["run_hash"] == second["run_hash"],
            "run_hash": first["run_hash"],
        })
        hashes.append((probe["id"], first["run_hash"]))
    aggregate = hashlib.sha256(
        json.dumps(sorted(hashes), ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    return {"rows": rows, "run_hash": aggregate,
            "all_dispatched": all(row["dispatched"] for row in rows),
            "all_deterministic": all(row["deterministic"] for row in rows)}


# ------------------------------------------------------------- 历史面（#71）

def history_plan(probe: dict, entry_id: str) -> list[tuple[str, dict]]:
    from memory_agent.agent_loop.tools import HISTORY_TOOL

    action = probe["action"]
    args: dict = {"entry_id": entry_id, "op": "log", "limit": 20}
    if action.get("op") == "log_added":
        args["added_only"] = True
    elif action.get("op") == "log_pickaxe":
        args["pattern"] = action.get("pattern")
    return [(HISTORY_TOOL, args)]


def resolve_entry_id(catalog: dict[str, dict], rel_path: str, label: str | None = None) -> str | None:
    """按 doc 相对路径定位条目 id：优先带来源标签的精确匹配（避免同名文件撞车）。"""
    want = rel_path.replace("\\", "/")
    if label:
        candidate = f"repo:{label}/{want}"
        if candidate in catalog:
            return candidate
    if want in catalog:
        return want
    for entry_id in sorted(catalog):
        source = str(catalog[entry_id].get("source") or "")
        if source == want or source.endswith("/" + want):
            return entry_id
    return None


def run_history_face(registry, catalog: dict[str, dict], probes: list[dict],
                     label: str | None) -> dict:
    rows = []
    for probe in probes:
        expected = probe["expected"]
        entry_id = resolve_entry_id(catalog, expected["file"], label)
        if entry_id is None:
            rows.append({"id": probe["id"], "reachable": False,
                         "reason": "not_in_base_table: read-only corpus indexes .md only"})
            continue
        result = registry.call(*history_plan(probe, entry_id)[0])
        shas = [item.get("sha") for item in (result.get("commits") or [])]
        reachable = bool(result.get("error") is None and expected["commit"] in shas)
        rows.append({
            "id": probe["id"], "reachable": reachable, "entry_id": entry_id,
            "op": result.get("op"), "count": result.get("count"),
            "commit_found": expected["commit"] in shas,
            "reason": None if reachable else (result.get("error") or "commit_not_found"),
        })
    return {"rows": rows, "reachable": sum(1 for r in rows if r["reachable"]),
            "total": len(rows)}


def run_boundary_face(registry, catalog: dict[str, dict], probes: list[dict],
                      label: str | None) -> list[dict]:
    """代码面（`scope=repo`）：真跑一次工具，把「够不到」作为**声明边界**量出来。"""
    from memory_agent.agent_loop.tools import GREP_TOOL, READ_TOOL

    rows = []
    for probe in probes:
        action = probe["action"]
        if probe["face"] == "grep":
            hits = registry.call(GREP_TOOL, {"pattern": action["pattern"],
                                             "glob": action.get("globs") or [],
                                             "limit": 50})
            count = len(hits) if isinstance(hits, list) else 0
        else:
            entry_id = resolve_entry_id(catalog, probe["expected"]["file"], label)
            if entry_id is None:
                count = 0
            else:
                got = registry.call(READ_TOOL, {"entry_id": entry_id})
                count = 1 if not got.get("error") else 0
        rows.append({"id": probe["id"], "file": probe["expected"]["file"],
                     "hits": count, "reachable": count > 0,
                     "note": "code files are not in the index base table (#71 boundary layer)"})
    return rows


# ------------------------------------------------------------------ 配对统计

def paired_stats(rows: list[dict], baseline_rows: list[dict], *,
                 caution: str | None = None) -> dict:
    from memory_agent.eval.harness.stats import bootstrap_ci, paired_diffs

    by_id = {row["id"]: row for row in baseline_rows}
    usable = [row for row in rows
              if row["id"] in by_id and row.get("target") and row.get("usable", True)]
    tool = [{"id": row["id"], "v": 1.0 if row["hit"] else 0.0} for row in usable]
    base = [{"id": row["id"], "v": 1.0 if by_id[row["id"]].get("hit@14") else 0.0}
            for row in usable]
    excluded = [{"id": row["id"], "reason": "entry_missing"} for row in rows
                if row["id"] in by_id and not row.get("usable", True)]
    diffs = paired_diffs(tool, base, "v")
    ci = bootstrap_ci(diffs)
    per_face = {}
    for face in PAIRED_FACES:
        subset = [row for row in usable if row["face"] == face]
        if not subset:
            continue
        base_subset = [1.0 if by_id[row["id"]].get("hit@14") else 0.0 for row in subset]
        per_face[face] = {
            "n": len(subset),
            "tool_hit": round(sum(1 for row in subset if row["hit"]) / len(subset), 6),
            "retrieval_hit": round(sum(base_subset) / len(base_subset), 6),
        }
    return {
        "n": ci["n"],
        "excluded": excluded,
        "tool_hit_rate": round(sum(item["v"] for item in tool) / len(tool), 6) if tool else None,
        "retrieval_hit_rate": round(sum(item["v"] for item in base) / len(base), 6) if base else None,
        "delta": ci["mean"], "ci": {"lo": ci["lo"], "hi": ci["hi"]},
        "significant": ci["significant"],
        "by_face": per_face,
        "caution": caution or (
            "scripted decisions (reference actions) -> delta measures the face + tool "
            "execution upper bound, NOT LLM-agent significance (#71 same caveat)"),
    }


# ------------------------------------------------------------------ agent 臂

def run_agent(registry, probes: list[dict], *, provider, base_url, model,
              rounds: int, limit: int | None, k: int = 5) -> dict:
    """真 LLM 臂：模型自己决定工具。**本机无 LLM 时不会跑到这里**。"""
    from memory_agent.agent_loop import AgentLoop, Budget
    from memory_agent.agent_loop.llm import (
        ProviderSpec,
        build_llm_client,
        resolve_provider,
    )
    from memory_agent.agent_loop.tools import SEARCH_TOOL

    spec = resolve_provider(ProviderSpec(provider=provider, base_url=base_url, model=model))
    llm = build_llm_client(spec)      # 缺 key / server 缺席 → 这里抛，不伪造结果；key 不进 repr
    chosen = probes[:limit] if limit else probes
    rows = []
    for probe in chosen:
        loop = AgentLoop(llm, registry, budget=Budget(max_rounds=max(1, rounds + 1)), k=k)
        t0 = time.time()
        trace = loop.run(probe["query"], trace_id=probe["id"])
        elapsed = round(time.time() - t0, 3)
        tools_called = [call.tool for rnd in trace.rounds for call in rnd.tool_calls]
        target = probe.get("baseline_entry_id")
        nav_calls = [tool for tool in tools_called if tool != SEARCH_TOOL]
        rows.append({
            "id": probe["id"], "face": probe["face"], "target": target,
            "hit": target in (trace.final.get("evidence_ids") or []),
            "evidence_ids": list(trace.final.get("evidence_ids") or []),
            "tools_called": tools_called, "nav_calls": nav_calls,
            "used_nav_tool": bool(nav_calls),
            "stop": trace.stop.trigger if trace.stop else None,
            "run_hash": trace.meta.get("run_hash"),
            "elapsed_s": elapsed,
            "answer": trace.final.get("answer"),
        })
    return {"rows": rows, "provider": spec.provider, "base_url": spec.base_url,
            "model": spec.model, "temperature": spec.temperature, "seed": spec.seed,
            "max_rounds": max(1, rounds + 1), "k": k,
            "n": len(rows), "elapsed_total_s": round(sum(r["elapsed_s"] for r in rows), 1),
            "nav_tool_use_rate": round(
                sum(1 for row in rows if row["used_nav_tool"]) / len(rows), 6) if rows else None}


# -------------------------------------------------------------------- main

def build_registry(index, exclude_retired: bool = True):
    from memory_agent.agent_loop.tools import MemoryNavToolRegistry

    return MemoryNavToolRegistry(index, exclude_retired=exclude_retired)


def catalog_of(index) -> dict[str, dict]:
    """`{entry_id: meta}`（manifest 元数据 + 正文）——供按路径定位条目。"""
    out: dict[str, dict] = {}
    for entry_id in index.known_ids():
        try:
            out[entry_id] = index.get(entry_id)
        except (KeyError, FileNotFoundError):
            continue
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="#63 nav tools eval (scripted + agent arms)")
    parser.add_argument("--run", action="store_true", help="scripted arm (no LLM; default)")
    parser.add_argument("--agent", action="store_true", help="real-LLM arm")
    parser.add_argument("--provider", default=None, help="opencode-server (default) | openai-compat")
    parser.add_argument("--base-url", default=None,
                        help="opencode-server default http://127.0.0.1:4096")
    parser.add_argument("--model", default=None,
                        help="opencode-server needs <providerID>/<modelID>")
    parser.add_argument("--port", type=int, default=None, help="shorthand for opencode-server URL")
    parser.add_argument("--rounds", type=int, default=2, help="extra rounds (total = rounds + 1)")
    parser.add_argument("--k", type=int, default=5,
                        help="loop search k (production default 5; #71 baseline is top-14)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-refresh", action="store_true",
                        help="skip the lazy refresh that brings the temp copy to corpus parity")
    parser.add_argument("--sanitize-proxy-env", action="store_true",
                        help="drop IPv6 entries from NO_PROXY in-process (host env defect)")
    parser.add_argument("--probes", default=PROBES_JSON)
    parser.add_argument("--baseline-json", default=BASELINE_JSON)
    parser.add_argument("--out", default=os.path.join(HERE, "nav_63_results.json"))
    parser.add_argument("--prod-index", default=None)
    parser.add_argument("--index-dir", default=None,
                        help="temp index root (default %%TEMP%%/eval63-nav/index)")
    args = parser.parse_args(argv)
    if not args.run and not args.agent:
        args.run = True
    proxy_env = sanitize_proxy_env() if args.sanitize_proxy_env else {}

    probes_doc = load_probes(args.probes)
    label = probes_doc.get("readonly_label")
    with open(args.baseline_json, "r", encoding="utf-8") as handle:
        baseline = json.load(handle)
    prod_index = args.prod_index or os.path.join(main_worktree(), "memory_agent", "vector_db")
    index_dir = args.index_dir or os.path.join(tempfile.gettempdir(), "eval63-nav", "index")

    before = _dir_signature(prod_index)
    isolation = setup_isolated_index(prod_index, index_dir)

    from memory_agent.memory.index import MemoryIndex

    index = MemoryIndex()
    stats_before = index.stats()
    refresh: dict = {"ran": False}
    if not args.no_refresh:
        t0 = time.time()
        index.search("warmup", 1)   # 惰性增量追平（只写临时副本，与 #71 --baseline 同法）
        refresh = {"ran": True, "seconds": round(time.time() - t0, 2)}
    stats_after = index.stats()
    refresh.update({"entries_before": stats_before.get("entries"),
                    "entries_after": stats_after.get("entries")})
    catalog = catalog_of(index)
    registry = build_registry(index)

    addressable = [p for p in probes_doc["probes"] if p.get("baseline_entry_id")]
    history_probes = [p for p in probes_doc["probes"] if p["face"] == "history"]
    boundary_probes = [p for p in probes_doc["probes"]
                       if p.get("scope") == "repo" and not p.get("baseline_entry_id")]
    missing_targets = [p["id"] for p in addressable
                       if p.get("baseline_entry_id") not in catalog]

    result: dict = {
        "mode": "nav-63",
        "probes_total": len(probes_doc["probes"]),
        "addressable": len(addressable),
        "entry_missing": missing_targets,
        "baseline": {
            "source": os.path.basename(args.baseline_json),
            "k": baseline.get("k"), "recall": baseline.get("recall"),
            "mrr": baseline.get("mrr"), "run_hash": baseline.get("run_hash"),
        },
        "index": {**isolation, "entries": len(catalog), "refresh": refresh},
        "env_pins": EVAL_ENV_PINS,
        "proxy_env_sanitized": proxy_env,
    }

    if args.run:
        scripted = run_scripted(registry, addressable, catalog)
        replay = run_replay(registry, addressable, rounds=args.rounds)
        result["scripted"] = {
            "reach": {
                "n": len(scripted),
                "hits": sum(1 for row in scripted if row["hit"]),
                "misses": [row["id"] for row in scripted if not row["hit"]],
                "rows": scripted,
            },
            "replay": replay,
            "paired": paired_stats(scripted, baseline.get("rows") or []),
        }
        result["history_face"] = run_history_face(registry, catalog, history_probes, label)
        result["boundary_face"] = run_boundary_face(registry, catalog, boundary_probes, label)
        result["end_to_end_significance"] = {
            "verified": False,
            "reason": ("no LLM on this host (no opencode serve on :4096 + no openai-compat key) "
                       "-> agent arm not run; the scripted arm's delta is a face/tool upper bound"),
        }

    if args.agent:
        base_url = args.base_url
        if base_url is None and args.port:
            base_url = f"http://127.0.0.1:{args.port}"
        agent = run_agent(registry, addressable, provider=args.provider, base_url=base_url,
                          model=args.model, rounds=args.rounds, limit=args.limit, k=args.k)
        agent["paired"] = paired_stats(
            [{"id": row["id"], "face": row["face"], "target": row["target"],
              "hit": row["hit"], "usable": True} for row in agent["rows"]],
            baseline.get("rows") or [],
            caution=("real LLM in the loop (model picks the tools; temperature=0, seed=42). "
                     "LLM is NOT byte-deterministic -> this is one observed run, not a "
                     "reproducibility claim; n is small so the CI is wide."))
        agent["end_to_end_significance"] = {
            "verified": True,
            "reason": f"real LLM in the loop (provider={agent['provider']}); model picks tools",
        }
        result["agent"] = agent
        # 顶层口径以**真跑的臂**为准（§B 跑过之后就不能再声称"未验"）。
        result["end_to_end_significance"] = agent["end_to_end_significance"]

    after = _dir_signature(prod_index)
    result["isolation_proof"] = {
        "prod_index_dir": prod_index, "sig_before": before, "sig_after": after,
        "prod_index_unchanged": before == after,
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=1)

    print(f"[nav63] probes={result['probes_total']} addressable={result['addressable']} "
          f"entries={len(catalog)} entry_missing={len(missing_targets)} "
          f"refresh={refresh.get('seconds')}s")
    if args.run:
        reach = result["scripted"]["reach"]
        pair = result["scripted"]["paired"]
        replay = result["scripted"]["replay"]
        print(f"  scripted reach: {reach['hits']}/{reach['n']}  misses={reach['misses']}")
        print(f"  replay: dispatched={replay['all_dispatched']} "
              f"deterministic={replay['all_deterministic']} run_hash={replay['run_hash']}")
        print(f"  paired delta(tool-retrieval@14)={pair['delta']} "
              f"CI=[{pair['ci']['lo']},{pair['ci']['hi']}] significant={pair['significant']} "
              f"n={pair['n']} excluded={pair['excluded']}")
        hist = result["history_face"]
        print(f"  history face reachable: {hist['reachable']}/{hist['total']}")
    if args.agent:
        agent = result["agent"]
        print(f"  agent arm: n={agent['n']} hit_rate={agent['paired']['tool_hit_rate']} "
              f"nav_tool_use={agent['nav_tool_use_rate']}")
        print(f"  agent paired delta={agent['paired']['delta']} "
              f"CI=[{agent['paired']['ci']['lo']},{agent['paired']['ci']['hi']}] "
              f"significant={agent['paired']['significant']}")
    print(f"  prod_index_unchanged={result['isolation_proof']['prod_index_unchanged']}")
    print(f"  [out] {args.out}")
    return 0 if result["isolation_proof"]["prod_index_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
