"""导航探针（#71）的**校验 + 一次性检索基线**。

两个子命令（见 `standard_sets/nav_probes_71.spec.md`）：

- `--verify`：无模型、秒级。用 Python **复刻参考导航动作**（grep / read 行窗 / outline 标题树 /
  git log），断言 28 条探针全部可解、期望位置存在、且 `baseline_entry_id` 与本仓语料一致。
  这是探针集的 L0 自证：题解不出来 = 探针坏了，不是 #63 坏了。
- `--baseline`：需 BGE-M3。在**临时索引副本**上跑生产记忆检索链（`MemoryIndex.search`：
  BGE-M3 dense + BM25 sparse + store 原生 DBSF，rerank 关，pool=14），对每条探针报
  recall@1/5/10/14 + MRR + paired bootstrap CI。**绝不写生产 `memory_agent/vector_db`**
  （前后目录签名比对自证）。

隔离纪律：
- `MEMORY_INDEX_DIR` 指向临时目录；首次运行时把生产 `CURRENT` 指向的**代目录复制**过去
  （含 manifest），再让正常的惰性刷新在新文件上增量追平——**生产一代一个字节都不动**。
- 只读来源用**临时生成的绝对路径 config**（label 与生产一致），避免 `ROOT_DIR` 相对路径
  落到 worktree（那会让 entry id 全变、触发整库重建）。

    $py = "D:\\<repo>\\venv\\Scripts\\python.exe"      # venv 在主树（worktree 里没有）
    $env:PYTHONPATH = "D:\\<repo-or-worktree>"          # 跑哪个 checkout 就指哪个
    & $py memory_agent/eval/eval_71_nav.py --verify
    & $py memory_agent/eval/eval_71_nav.py --refresh    # 文档位移后修位置元数据（幂等）
    & $py memory_agent/eval/eval_71_nav.py --baseline --out memory_agent/eval/eval_71_nav_baseline.json
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(PKG_DIR)
PROBES_JSON = os.path.join(HERE, "standard_sets", "nav_probes_71.json")

KS = (1, 5, 10, 14)          # 14 = 生产 MEMORY_RETRIEVAL_POOL
PROD_POOL = 14

# 评测口径 pin：在本模块 import memory_agent **之前**生效（settings 在 import 时读 env）。
EVAL_ENV_PINS = {
    "MEMORY_LOCAL_HYBRID": "1",      # store 原生 hybrid（dense + sparse）
    "MEMORY_SPARSE_BACKEND": "bm25",  # 词法路 = BM25（ADR-0019 D16）
    "MEMORY_STORE_FUSION": "dbsf",    # 原生融合 = DBSF
    "MEMORY_RERANK": "0",            # 生产默认关
    "MEMORY_RETRIEVAL_POOL": str(PROD_POOL),
    "MEMORY_WARMUP": "0",
}

_EXCLUDE_WALK_DIRS = frozenset({
    ".git", "venv", ".venv", "__pycache__", "node_modules", ".pytest_cache",
    "vector_db", ".cache", ".mypy_cache", ".ruff_cache",
})

# 自产证据的**排除表**（判据见 `standard_sets/nav_probes_71.spec.md` §7）：
# 凡「本批 Agent-Teams 自己产出的评测/压测/验收证据」都不进测量语料。两条判据：
#  (1) 答案泄漏 —— 文件含探针锚 / 探针 id / 命中结论，留在语料里等于"拿自己的题面与答案当语料"；
#      实测泄漏源：`eval_71_results.md`(6 锚) / `nav_probes_71.spec.md`(4 锚) /
#      `phase_c/report.md`(1) / `experiments/nav-probes-71/README.md`(1) / `nav_63_results.md`(全部探针 id + 命中表)。
#  (2) 语料漂移 —— 同批其它票的证据不是冻结快照的基表内容，却会改变语料规模与 BM25 统计
#      （`perf_70*` / `isolation_bypass_*` 等），让"基线"随同批产出漂移。
# verify 与 baseline 共用本表（baseline 用 overlay `exclude` 落地，语义同源）。
# 注意：**只排除自产证据**——`memory_agent/eval/README.md`（g05/r03 的目标）、
# `experiments/agentic-rag-census/report.md`（#47 不可变证据）等**仍在语料内**。
SELF_EXCLUDE_PREFIXES = (
    # #71 自己的产物（题面 / 规格 / 结果 / 实验记录）
    "memory_agent/eval/standard_sets/",
    "memory_agent/eval/eval_71",
    "experiments/nav-probes-71/",
    "experiments/agentic-rag-census/phase_c/",
    # 同批其它票的自产证据（判据 1 + 2）
    "memory_agent/eval/nav_63",
    "memory_agent/eval/perf_70",
    "memory_agent/eval/isolation_bypass_",
    "experiments/nav-tools-63/",
)


def _is_self_artifact(rel: str) -> bool:
    return any(rel.startswith(prefix) for prefix in SELF_EXCLUDE_PREFIXES)



# ------------------------------------------------------------------ 通用工具

def load_probes(path: str = PROBES_JSON) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def glob_match(rel: str, pattern: str) -> bool:
    """支持 `**` 的简化 glob（相对 posix 路径）。"""
    if "**" in pattern:
        head, _, tail = pattern.partition("**")
        prefix = head.rstrip("/")
        suffix = tail.lstrip("/") or "*"
        return (not prefix or rel == prefix or rel.startswith(prefix + "/")) \
            and fnmatch.fnmatch(rel, suffix)
    return fnmatch.fnmatch(rel, pattern)


def read_lines(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read().splitlines()


def headings(text: str) -> list[tuple[int, str, int]]:
    """标题树：`[(level, text, line)]`，**跳过 fenced code block**（``` / ~~~）。"""
    out: list[tuple[int, str, int]] = []
    fence: str | None = None
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
            continue
        match = re.match(r"^(#{1,6})\s+(.*?)\s*$", line)
        if match:
            out.append((len(match.group(1)), match.group(2).strip(), lineno))
    return out


def _git(root: str, args: list[str]) -> list[str]:
    proc = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def main_worktree(repo_root: str = REPO_ROOT) -> str:
    """`git worktree list` 第一项 = 主工作树（生产索引 / venv 所在）。"""
    for line in _git(repo_root, ["worktree", "list", "--porcelain"]):
        pass
    proc = subprocess.run(["git", "-C", repo_root, "worktree", "list", "--porcelain"],
                          capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            return line.split(" ", 1)[1].strip()
    return repo_root


def _scope_files(probe: dict, roots: dict) -> list[tuple[str, str]]:
    """返回 scope 内的 `[(绝对路径, 相对 posix 路径)]`。"""
    scope = probe.get("scope", "corpus")
    root = roots[scope]
    if scope == "repo":
        found = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_WALK_DIRS]
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root).replace("\\", "/")
                found.append((full, rel))
        return found
    # corpus / kb：只取 .md，沿用 loader 的噪声排除
    from memory_agent.corpus.loader import _iter_markdown

    files = [(full, rel) for full, rel, _m, _s in _iter_markdown(root)]
    if scope == "corpus":
        files = [(full, rel) for full, rel in files if not _is_self_artifact(rel)]
    return files


# -------------------------------------------------- 观测 / 三段判定 / 刷新
#
# 口径（#71 收尾，Lead 复核后修订）：
#   ① 目标文件存在
#   ② **锚短语仍在**（语义；锚 = `expected.span` / `expected.heading` / 历史提交）
#   ③ 位置在**记录值 ± tolerance**（默认 10）内；历史面无位置轴
# 文档位移（①②通过、③超差）→ `--refresh` 可修；**② 失败 = 真失效**（锚短语被删改）→ 必须改探针，
# `--refresh` 不会自动改语义，只把它列进 `unrefreshable`。
# `unique`（grep）降级为**告警**：仓库合法地多出一处同名 token 不该判负（记录在 flags）。


def _sha1(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:12]


def _roots(repo_root: str) -> dict:
    import memory_agent.settings as settings

    return {"corpus": repo_root, "repo": repo_root, "kb": os.path.abspath(settings.KB_DIR)}


def _probe_path(probe: dict, roots: dict) -> str:
    root = roots[probe.get("scope", "corpus")]
    return os.path.join(root, *probe["expected"]["file"].split("/"))


def observe(probe: dict, roots: dict) -> dict:
    """一次观测（不做判定）：文件存在性 / 锚短语位置 / 标题 / 历史提交。"""
    face = probe["face"]
    expected = probe["expected"]
    action = probe["action"]
    obs = {"exists": False, "file": expected["file"], "occurrences": [], "scope_occurrences": [],
           "heading": None, "level": None, "heading_line": None, "heading_text": None,
           "commits": None, "error": None}

    if face == "history":
        obs["exists"] = os.path.isfile(os.path.join(roots["repo"], *action["path"].split("/")))
        if action["op"] == "log_added":
            args = ["log", "--diff-filter=A", "--format=%H", "--", action["path"]]
        elif action["op"] == "log_pickaxe":
            args = ["log", "-S", action["pattern"], "--format=%H", "--", action["path"]]
        else:
            obs["error"] = f"未知历史动作：{action['op']}"
            return obs
        obs["commits"] = _git(roots["repo"], args)
        return obs

    path = _probe_path(probe, roots)
    obs["exists"] = os.path.isfile(path)
    if not obs["exists"]:
        return obs
    lines = read_lines(path)
    if face == "outline":
        found = headings("\n".join(lines))
        matches = [(lvl, txt, ln) for lvl, txt, ln in found if txt == expected["heading"]]
        obs["heading_matches"] = matches
        if matches:
            obs["level"], obs["heading"], obs["heading_line"] = matches[0][0], matches[0][1], matches[0][2]
            obs["heading_text"] = lines[matches[0][2] - 1]
        return obs

    span = expected["span"]
    obs["occurrences"] = [{"file": expected["file"], "line": i, "text": line}
                          for i, line in enumerate(lines, start=1) if span in line]
    if face == "grep":  # scope 级命中：只供唯一性告警
        for full, rel in _scope_files(probe, roots):
            if not any(glob_match(rel, g) for g in (action.get("globs") or ["**/*"])):
                continue
            for i, line in enumerate(read_lines(full), start=1):
                if span in line:
                    obs["scope_occurrences"].append({"file": rel, "line": i})
    return obs


def _pick(occurrences: list[dict], recorded: int | None) -> int | None:
    """多命中时取**离记录行最近**的那个（判定与刷新用同一规则）。"""
    lines = [o["line"] for o in occurrences]
    if not lines:
        return None
    if not recorded:
        return lines[0]
    return min(lines, key=lambda x: (abs(x - recorded), x))


def judge(probe: dict, obs: dict, tolerance: int) -> dict:
    """三段判定：① 文件存在 ② 锚仍在（语义）③ 位置在记录值 ±tolerance。"""
    face = probe["face"]
    expected = probe["expected"]
    notes: list[str] = []
    flags: list[str] = []
    recorded = expected.get("span_line") or expected.get("anchor_line")

    if obs.get("error"):
        return {"ok": False, "checks": {"exists": obs["exists"], "anchor": False, "position": False},
                "actual_line": None, "notes": [obs["error"]], "flags": []}

    exists_ok = bool(obs["exists"])
    actual = None
    if face == "history":
        anchor_ok = expected["commit"] in (obs.get("commits") or [])
        position_ok = True
        if not anchor_ok:
            notes.append(f"期望提交不在 git 结果里；实测 {(obs.get('commits') or [])[:4]}")
    elif face == "outline":
        anchor_ok = (obs.get("heading") == expected["heading"]
                     and obs.get("level") == expected["level"])
        actual = obs.get("heading_line")
        position_ok = True
        if obs.get("heading") is None:
            notes.append(f"标题不存在：{expected['heading']!r}（期望 level={expected['level']}）")
        elif not anchor_ok:
            notes.append(f"标题层级不符：实测 {obs.get('heading_matches')}")
        elif recorded and actual is not None:
            position_ok = abs(actual - recorded) <= tolerance
            if not position_ok:
                notes.append(f"标题位移 {recorded} → {actual}（超 ±{tolerance}）：请 --refresh")
        if anchor_ok and expected.get("span_sha1") and obs.get("heading_text") \
                and _sha1(obs["heading_text"]) != expected["span_sha1"]:
            flags.append("content_changed")
    else:  # grep / read
        anchor_ok = bool(obs["occurrences"])
        if not anchor_ok:
            notes.append(f"锚短语已不在目标文件：{expected['span']!r}"
                         "（**真失效**：改探针 / 换锚；--refresh 不会自动救）")
        actual = _pick(obs["occurrences"], recorded)
        position_ok = True
        if anchor_ok and recorded and actual is not None:
            position_ok = abs(actual - recorded) <= tolerance
            if not position_ok:
                notes.append(f"锚短语位移 {recorded} → {actual}（超 ±{tolerance}）：请 --refresh")
        if anchor_ok and expected.get("span_sha1") and actual is not None:
            occ = next((o for o in obs["occurrences"] if o["line"] == actual), None)
            if occ and _sha1(occ["text"]) != expected["span_sha1"]:
                flags.append("content_changed")
        # 锚短语在**目标文件内**出现多次 = 潜在歧义。只在**真会误导 refresh** 时告警：
        # 存在离记录行超过 tolerance 的另一处（refresh 的「就近取用」可能跳到别的段落；
        # 2026-10-07 实测命中过 r04：L229 vs L343）。窗口内紧邻的重复（如 b03 的
        # `STOP_FALLBACK` 定义行 + 元组引用行）无害，不告警。
        if anchor_ok and len(obs["occurrences"]) > 1:
            far = [o["line"] for o in obs["occurrences"]
                   if recorded is None or abs(o["line"] - recorded) > tolerance]
            if far:
                flags.append(f"anchor_ambiguous(in_file={len(obs['occurrences'])},far={far[:3]})")
        if face == "grep" and expected.get("unique"):
            total = len(obs.get("scope_occurrences") or obs["occurrences"])
            if total != 1:
                flags.append(f"unique_mismatch(scope={total})")
        if face == "read" and anchor_ok and actual is not None:
            lo, hi = expected["line_range"]
            if not (lo <= actual <= hi):
                flags.append("window_shifted")
                if not recorded:  # 未刷新的 read 探针：行窗就是判据（旧口径，硬）
                    position_ok = False
                    notes.append(f"行窗 [{lo},{hi}] 不含锚短语（实际行 {actual}）：请 --refresh")

    ok = exists_ok and anchor_ok and position_ok
    if not exists_ok:
        notes.insert(0, f"文件不存在：{expected['file']}")
    elif face != "history" and not recorded:
        flags.append("no_recorded_line")
    return {"ok": ok,
            "checks": {"exists": exists_ok, "anchor": bool(anchor_ok), "position": bool(position_ok)},
            "actual_line": actual, "notes": notes, "flags": flags}


def refresh(probes_doc: dict, *, repo_root: str = REPO_ROOT, tolerance: int | None = None,
            out_path: str | None = None, today: str | None = None) -> dict:
    """重推锚行 / 行窗并记录**内容指纹**（`span_sha1`）与刷新提交；写回探针 JSON。

    - 只改**位置元数据**（`span_line` / `anchor_line` / read 的 `line_range` / `span_sha1` /
      `refreshed_at`），**不动 query / 期望语义**。
    - 锚短语**不在文件里**（真失效）→ 进 `unrefreshable`，需人工改探针（不自动换锚）。
    """
    from datetime import datetime, timezone

    roots = _roots(repo_root)
    head = (_git(repo_root, ["rev-parse", "HEAD"]) or [None])[0]
    if today is None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    updated, unrefreshable, skipped = [], [], []
    for probe in probes_doc["probes"]:
        face = probe["face"]
        expected = probe["expected"]
        if face == "history":
            skipped.append(probe["id"])
            continue
        obs = observe(probe, roots)
        if not obs["exists"]:
            unrefreshable.append({"id": probe["id"], "reason": f"文件不存在：{expected['file']}"})
            continue
        if face == "outline":
            if obs.get("heading") is None or obs.get("level") != expected["level"]:
                unrefreshable.append({"id": probe["id"],
                                      "reason": f"标题不在 / 层级不符：{expected['heading']!r}"})
                continue
            before = expected.get("anchor_line")
            expected["anchor_line"] = obs["heading_line"]
            expected["span_line"] = obs["heading_line"]
            expected["span_sha1"] = _sha1(obs["heading_text"])
            probe["refreshed_at"] = head
            updated.append({"id": probe["id"], "file": expected["file"],
                            "line": f"{before} -> {obs['heading_line']}"})
            continue
        if not obs["occurrences"]:
            unrefreshable.append({"id": probe["id"],
                                  "reason": f"锚短语已不在文件：{expected['span']!r}"})
            continue
        before = expected.get("span_line") or expected.get("anchor_line")
        actual = _pick(obs["occurrences"], before)
        line_text = next(o["text"] for o in obs["occurrences"] if o["line"] == actual)
        expected["span_line"] = actual
        expected["anchor_line"] = actual
        expected["span_sha1"] = _sha1(line_text)
        if face == "read" and before:
            lo, hi = expected["line_range"]
            delta = actual - before
            expected["line_range"] = [lo + delta, hi + delta]
        elif face == "read" and expected.get("span_offset") is not None:
            # 首次刷新（没有记录行）：用 span_offset / line_width 把行窗**刚性平移**到锚上
            lo, hi = expected["line_range"]
            off = expected["span_offset"]
            width = expected.get("line_width", hi - lo)
            expected["line_range"] = [actual - off, actual - off + width]
        if face == "read" and isinstance(probe.get("action"), dict) \
                and "line_range" in probe["action"]:
            # 参考动作与期望保持同一条行窗（同为**位置元数据**；否则读起来自相矛盾）
            probe["action"]["line_range"] = list(expected["line_range"])
        probe["refreshed_at"] = head
        updated.append({"id": probe["id"], "file": expected["file"],
                        "line": f"{before} -> {actual}",
                        "line_range": expected.get("line_range")})

    probes_doc["refresh"] = {
        "tolerance": tolerance if tolerance is not None else probes_doc.get("tolerance", 10),
        "recorded_at": today,
        "recorded_at_commit": head,
        "semantics": "① 文件存在 ② 锚短语仍在 ③ 位置在记录值 ±tolerance（文档位移 ⇒ --refresh 可修；"
                     "锚短语消失 ⇒ 真失效，必须改探针）",
    }
    if tolerance is not None:
        probes_doc["tolerance"] = tolerance
    probes_doc["tolerance"] = probes_doc["refresh"]["tolerance"]
    target = out_path or PROBES_JSON
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(probes_doc, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return {"mode": "refresh", "out": target, "tolerance": probes_doc["refresh"]["tolerance"],
            "head": head, "updated": updated, "unrefreshable": unrefreshable,
            "skipped_immutable": skipped}


def verify(probes_doc: dict, *, repo_root: str = REPO_ROOT,
           tolerance: int | None = None) -> dict:
    roots = _roots(repo_root)
    tol = tolerance if tolerance is not None else int(
        probes_doc.get("refresh", {}).get("tolerance", probes_doc.get("tolerance", 10)))
    label = probes_doc.get("readonly_label", "agent-knowledge-base")
    from memory_agent.corpus.loader import _iter_markdown

    corpus = {rel for _f, rel, _m, _s in _iter_markdown(repo_root)
              if not _is_self_artifact(rel)}
    kb_ids = {}
    for full, rel, _m, _s in _iter_markdown(roots["kb"]):
        from memory_agent.memory.entries import parse_frontmatter

        with open(full, "r", encoding="utf-8", errors="replace") as handle:
            meta, _body = parse_frontmatter(handle.read())
        if meta.get("id"):
            kb_ids[rel] = str(meta["id"])

    rows = []
    for probe in probes_doc["probes"]:
        face = probe["face"]
        obs = observe(probe, roots)
        result = judge(probe, obs, tol)
        # baseline_entry_id 一致性（探针自身自洽性）
        entry = probe.get("baseline_entry_id")
        entry_actual = None
        if entry:
            if probe.get("scope") == "kb":
                entry_actual = kb_ids.get(probe["expected"]["file"])
            elif probe.get("in_corpus"):
                entry_actual = f"repo:{label}/{probe['expected']['file']}" \
                    if probe["expected"]["file"] in corpus else None
        entry_ok = (entry == entry_actual) if entry else True
        result.update({
            "id": probe["id"], "face": face, "scope": probe.get("scope", "corpus"),
            "difficulty": probe["difficulty"],
            "recorded_line": probe["expected"].get("span_line")
            or probe["expected"].get("anchor_line"),
            "baseline_entry_id": entry,
            "baseline_entry_actual": entry_actual,
            "baseline_entry_ok": entry_ok,
        })
        if entry and not entry_ok:
            result["ok"] = False
            result["checks"]["anchor"] = False
            result["notes"] = list(result.get("notes") or []) + [
                f"baseline_entry_id 不自洽：期望 {entry} 实测 {entry_actual}"]
        rows.append(result)

    by_face: dict[str, dict] = {}
    for row in rows:
        bucket = by_face.setdefault(row["face"], {"n": 0, "ok": 0})
        bucket["n"] += 1
        bucket["ok"] += int(row["ok"])
    flagged = [{"id": r["id"], "flags": r["flags"]} for r in rows if r.get("flags")]
    return {
        "mode": "verify",
        "n": len(rows),
        "ok": sum(1 for r in rows if r["ok"]),
        "tolerance": tol,
        "failed": [r for r in rows if not r["ok"]],
        "flagged": flagged,
        "by_face": by_face,
        "by_scope": {scope: sum(1 for r in rows if r["scope"] == scope)
                     for scope in ("corpus", "kb", "repo")},
        "entry_addressable": sum(1 for r in rows if r["baseline_entry_id"]),
        "structurally_unreachable": sum(1 for r in rows if not r["baseline_entry_id"]),
        "refresh": probes_doc.get("refresh"),
        "rows": rows,
    }


# ------------------------------------------------------------------ baseline

def _dir_signature(path: str) -> str | None:
    if not os.path.isdir(path):
        return None
    parts = []
    for root, _dirs, files in os.walk(path):
        for name in sorted(files):
            if name.lower().endswith((".log", ".pid", ".lock", ".tmp")):
                continue
            full = os.path.join(root, name)
            try:
                stat = os.stat(full)
            except OSError:
                continue
            parts.append(f"{os.path.relpath(full, path)}:{stat.st_size}:{stat.st_mtime_ns}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def _prepare_temp_index(prod_index: str, index_dir: str) -> dict:
    """把生产 CURRENT 指向的代目录复制到临时索引根；已存在则复用。"""
    pointer = os.path.join(prod_index, "CURRENT")
    if not os.path.isfile(pointer):
        raise SystemExit(f"生产索引没有 CURRENT 指针：{prod_index}")
    with open(pointer, "r", encoding="utf-8") as handle:
        gen = handle.read().strip()
    src = os.path.join(prod_index, gen)
    dst = os.path.join(index_dir, gen)
    info = {"prod_index": prod_index, "prod_gen": gen, "index_dir": index_dir,
            "reused_copy": os.path.isdir(dst)}
    if not os.path.isdir(dst):
        os.makedirs(index_dir, exist_ok=True)
        shutil.copytree(src, dst)
    with open(os.path.join(index_dir, "CURRENT"), "w", encoding="utf-8") as handle:
        handle.write(gen)
    return info


def _temp_readonly_config(prod_config: str, main_root: str, index_dir: str) -> str:
    """把生产的只读来源注册表改写成**绝对路径**版本，落在临时目录。

    生产 config 的相对路径按 `ROOT_DIR` 解析；临时进程的 `ROOT_DIR` 是 worktree，
    直接用它会得到完全不同的 entry id（进而整库重建）。所以这里显式展开成绝对路径。
    """
    items = []
    if os.path.isfile(prod_config):
        with open(prod_config, "r", encoding="utf-8") as handle:
            items = json.load(handle)
    resolved = []
    for item in items if isinstance(items, list) else []:
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        if not os.path.isabs(path):
            path = os.path.normpath(os.path.join(main_root, path))
        resolved.append({"label": item.get("label") or os.path.basename(path),
                         "path": os.path.abspath(path).replace("\\", "/")})
    target = os.path.join(index_dir, "readonly_repos_71.json")
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(resolved, handle, ensure_ascii=False, indent=1)
    return target


def _import_runtime():
    from memory_agent.memory.index import MemoryIndex

    return MemoryIndex


def _mrr(ranked: list[str], target: str) -> float:
    for i, entry_id in enumerate(ranked, start=1):
        if entry_id == target:
            return 1.0 / i
    return 0.0


def baseline(probes_doc: dict, *, prod_index: str, index_dir: str,
             out_path: str | None, k: int = PROD_POOL, limit: int | None = None) -> dict:
    # 顺序关键：`memory_agent.settings` 在 **import 时**读 env，所以先把索引根 / 语料
    # 配置 pin 好，再 import 任何 memory_agent 模块。
    copy_info = _prepare_temp_index(prod_index, index_dir)
    main_root = main_worktree()
    prod_config = os.path.join(main_root, "memory_agent", "readonly_repos.json")
    config = _temp_readonly_config(prod_config, main_root, index_dir)
    # 本票产物排除（绝对 glob，因 corpus 根是**主树**而 `ROOT_DIR` 是 worktree）。
    overlay_path = os.path.join(index_dir, "overlay_71.json")
    with open(overlay_path, "w", encoding="utf-8") as handle:
        json.dump({"include": [], "exclude": [
            os.path.join(main_root, prefix).replace("\\", "/") + "*"
            for prefix in SELF_EXCLUDE_PREFIXES]}, handle, ensure_ascii=False, indent=1)

    os.environ["MEMORY_INDEX_DIR"] = index_dir
    os.environ["MEMORY_READONLY_REPOS_CONFIG"] = config
    os.environ["MEMORY_OVERLAY_CONFIG"] = overlay_path
    for key, value in EVAL_ENV_PINS.items():
        os.environ[key] = value

    from memory_agent.eval.harness.stats import bootstrap_ci, paired_diffs

    MemoryIndex = _import_runtime()
    before = _dir_signature(prod_index)

    index = MemoryIndex()
    stats_before = index.stats()
    t0 = time.time()
    index.search("warmup", 1)          # 触发惰性刷新（只写临时副本）
    refresh_s = round(time.time() - t0, 2)
    stats_after = index.stats()
    known = set(index.known_ids())

    probes = [p for p in probes_doc["probes"] if p.get("baseline_entry_id")]
    if limit is not None:
        probes = probes[:limit]
    rows = []
    for probe in probes:
        target = probe["baseline_entry_id"]
        hits = index.search(probe["query"], k=k)
        ranked = [h["id"] for h in hits]
        row = {
            "id": probe["id"], "face": probe["face"], "scope": probe.get("scope", "corpus"),
            "difficulty": probe["difficulty"],
            "target": target, "ranked": ranked,
            "scores": [round(float(h["score"]), 6) for h in hits],
            "entry_in_index": target in known,
            "rank": (ranked.index(target) + 1) if target in ranked else None,
            "mrr": round(_mrr(ranked, target), 6),
        }
        for kk in KS:
            row[f"hit@{kk}"] = bool(target in ranked[:kk])
        rows.append(row)

    usable = [r for r in rows if r["entry_in_index"]]
    missing = [r["id"] for r in rows if not r["entry_in_index"]]
    recall = {str(kk): round(sum(1 for r in usable if r[f"hit@{kk}"]) / len(usable), 6)
              if usable else None for kk in KS}
    ci = {str(kk): bootstrap_ci([1.0 if r[f"hit@{kk}"] else 0.0 for r in usable])
          for kk in KS}
    mrr_ci = bootstrap_ci([r["mrr"] for r in usable])
    # paired：导航参考动作（verify 通过 = 每条都能定位）− 一次性检索 top-k 命中。
    # 导航臂恒为 1 → 区间宽度 0，按 harness 口径标 degenerate，**不当显著**。
    nav = [1.0 for _ in usable]
    paired = {str(kk): bootstrap_ci(paired_diffs(
        [{"id": r["id"], "v": 1.0} for r in usable],
        [{"id": r["id"], "v": 1.0 if r[f"hit@{kk}"] else 0.0} for r in usable], "v"))
        for kk in KS}
    for kk in KS:
        paired[str(kk)]["degenerate"] = bool(paired[str(kk)]["lo"] == paired[str(kk)]["hi"])

    # 结构性不可达层（history 6 条 + 代码边界 3 条）
    unreachable = [{"id": p["id"], "face": p["face"], "scope": p.get("scope"),
                    "baseline_note": p.get("baseline_note")}
                   for p in probes_doc["probes"] if not p.get("baseline_entry_id")]

    signature = hashlib.sha256(json.dumps(
        [{"id": r["id"], "ranked": r["ranked"]} for r in rows],
        ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]

    after = _dir_signature(prod_index)
    result = {
        "mode": "baseline",
        "probes_total": len(probes_doc["probes"]),
        "probes_entry_addressable": len(rows),
        "probes_usable": len(usable),
        "entry_missing": missing,
        "k": k,
        "chain": "MemoryIndex.search（LOCAL_HYBRID=1 / BM25 / DBSF / rerank=0）",
        "env_pins": EVAL_ENV_PINS,
        "index": {**copy_info, "config": config,
                  "entries_before": stats_before.get("entries"),
                  "entries_after": stats_after.get("entries"),
                  "refresh_s": refresh_s,
                  "gen": copy_info["prod_gen"]},
        "recall": recall,
        "recall_ci": ci,
        "mrr": round(sum(r["mrr"] for r in usable) / len(usable), 6) if usable else None,
        "mrr_ci": mrr_ci,
        "paired_nav_minus_retrieval": paired,
        "structurally_unreachable": unreachable,
        "by_face": {face: {
            "n": sum(1 for r in usable if r["face"] == face),
            "hit@1": round(sum(1 for r in usable if r["face"] == face and r["hit@1"])
                           / max(1, sum(1 for r in usable if r["face"] == face)), 6),
            "hit@5": round(sum(1 for r in usable if r["face"] == face and r["hit@5"])
                           / max(1, sum(1 for r in usable if r["face"] == face)), 6),
        } for face in ("grep", "read", "outline")},
        "by_difficulty": {level: {
            "n": sum(1 for r in usable if r["difficulty"] == level),
            "hit@5": round(sum(1 for r in usable
                               if r["difficulty"] == level and r["hit@5"])
                           / max(1, sum(1 for r in usable if r["difficulty"] == level)), 6),
        } for level in ("easy", "medium", "hard")},
        "run_hash": signature,
        "isolation": {"prod_index_dir_sig_before": before,
                      "prod_index_dir_sig_after": after,
                      "prod_index_unchanged": before == after},
        "rows": rows,
    }
    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=1)
    return result


# ------------------------------------------------------------------ main

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="nav probes #71 verifier + one-shot baseline")
    parser.add_argument("--verify", action="store_true", help="只读校验探针（无模型）")
    parser.add_argument("--refresh", action="store_true",
                        help="重推锚行 / 行窗 + 记录内容指纹（写回探针 JSON，不动 query）")
    parser.add_argument("--baseline", action="store_true", help="跑一次性检索基线（需 BGE-M3）")
    parser.add_argument("--probes", default=PROBES_JSON)
    parser.add_argument("--probes-out", default=None, help="--refresh 的写回目标（默认原地）")
    parser.add_argument("--tolerance", type=int, default=None,
                        help="③ 位置判定的 ±N（默认取探针 meta.tolerance / 10）")
    parser.add_argument("--out", default=None)
    parser.add_argument("--k", type=int, default=PROD_POOL)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--prod-index", default=None,
                        help="生产索引根（默认 = 主工作树 memory_agent/vector_db）")
    parser.add_argument("--index-dir", default=None,
                        help="临时索引根（默认 %%TEMP%%/eval71-nav/index）")
    args = parser.parse_args(argv)
    if not args.verify and not args.baseline and not args.refresh:
        parser.error("至少要给 --verify / --refresh / --baseline")

    probes_doc = load_probes(args.probes)
    rc = 0
    # 顺序关键：`verify` / `refresh` 会 import `memory_agent.settings`（冻结 INDEX_DIR），
    # 而 baseline 必须在那之前 pin 临时索引根。baseline 放最前，三者同给时顺序不会错。
    if args.baseline:
        prod_index = args.prod_index or os.path.join(
            main_worktree(), "memory_agent", "vector_db")
        index_dir = args.index_dir or os.path.join(
            tempfile.gettempdir(), "eval71-nav", "index")
        result = baseline(probes_doc, prod_index=prod_index, index_dir=index_dir,
                          out_path=args.out, k=args.k, limit=args.limit)
        print(f"[baseline] usable={result['probes_usable']} "
              f"miss_entry={len(result['entry_missing'])} k={result['k']}")
        print(f"  recall={result['recall']}")
        print(f"  MRR={result['mrr']} CI={result['mrr_ci']}")
        print(f"  run_hash={result['run_hash']}")
        print(f"  prod_index_unchanged={result['isolation']['prod_index_unchanged']} "
              f"refresh_s={result['index']['refresh_s']} "
              f"entries {result['index']['entries_before']}->{result['index']['entries_after']}")

    if args.refresh:
        refreshed = refresh(probes_doc, tolerance=args.tolerance,
                            out_path=args.probes_out)
        print(f"[refresh] 更新 {len(refreshed['updated'])} 条；"
              f"不动 {len(refreshed['skipped_immutable'])} 条（history 不可变）；"
              f"不可自动刷新 {len(refreshed['unrefreshable'])} 条")
        for item in refreshed["unrefreshable"]:
            print(f"  UNREFRESHABLE {item['id']}: {item['reason']}")
        print(f"  tolerance=±{refreshed['tolerance']} head={refreshed['head']}")
        print(f"  [out] {refreshed['out']}")
        probes_doc = load_probes(args.probes_out or args.probes)

    if args.verify:
        result = verify(probes_doc, tolerance=args.tolerance)
        print(f"[verify] {result['ok']}/{result['n']} 条通过（±{result['tolerance']}）；"
              f"by_face={result['by_face']}")
        for row in result["failed"]:
            print(f"  FAIL {row['id']} ({row['face']}): checks={row['checks']} {row['notes']}")
        for row in result["flagged"]:
            print(f"  flag {row['id']}: {row['flags']}")
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                json.dump(result, handle, ensure_ascii=False, indent=1)
        rc = 0 if result["ok"] == result["n"] else 1

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
