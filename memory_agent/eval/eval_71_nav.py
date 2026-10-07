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

    $py = "D:\\...\\venv\\Scripts\\python.exe"
    $env:PYTHONPATH = "D:\\...\\wk-71-eval"
    & $py memory_agent/eval/eval_71_nav.py --verify
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

# 本票自己的产物（探针集 / 规格 / 结果 / 实验记录）**不进测量语料**：
# 它们不是冻结快照里的基表内容，却天然含全部探针 token——留在语料里会
# ① 让 grep 唯一性失真，② 在每条 query 上霸占 top-k，把对照基线污染成"检索我自己的题面"。
# 因此 verify 与 baseline 都排除这些前缀（baseline 用 overlay `exclude` 落地，语义同源）。
SELF_EXCLUDE_PREFIXES = (
    "memory_agent/eval/standard_sets/",
    "memory_agent/eval/eval_71",
    "experiments/nav-probes-71/",
    "experiments/agentic-rag-census/phase_c/",
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


# ------------------------------------------------------------------ verify

def _verify_grep(probe: dict, roots: dict) -> dict:
    pattern = probe["action"]["pattern"]
    globs = probe["action"].get("globs") or ["**/*"]
    expected = probe["expected"]
    hits: list[tuple[str, int]] = []
    for full, rel in _scope_files(probe, roots):
        if not any(glob_match(rel, g) for g in globs):
            continue
        for lineno, line in enumerate(read_lines(full), start=1):
            if pattern in line:
                hits.append((rel, lineno))
    in_expected = any(rel == expected["file"] for rel, _lineno in hits)
    ok = in_expected
    notes = []
    if expected.get("unique") and len(hits) != 1:
        ok = False
        notes.append(f"期望唯一命中，实测 {len(hits)} 处：{hits[:5]}")
    return {"ok": ok, "occurrences": [{"file": rel, "line": lineno} for rel, lineno in hits][:8],
            "occurrence_count": len(hits), "notes": notes}


def _verify_read(probe: dict, roots: dict) -> dict:
    expected = probe["expected"]
    lo, hi = expected["line_range"]
    root = roots[probe.get("scope", "corpus")]
    path = os.path.join(root, *expected["file"].split("/"))
    if not os.path.isfile(path):
        return {"ok": False, "notes": [f"文件不存在：{expected['file']}"]}
    lines = read_lines(path)
    window = "\n".join(lines[lo - 1:hi])
    ok = expected["span"] in window
    notes = []
    if not ok:
        actual = [i for i, line in enumerate(lines, start=1) if expected["span"] in line]
        notes.append(f"行窗 [{lo},{hi}] 不含 span；该 span 实际在行 {actual[:5]}")
    return {"ok": ok, "notes": notes}


def _verify_outline(probe: dict, roots: dict) -> dict:
    expected = probe["expected"]
    root = roots[probe.get("scope", "corpus")]
    path = os.path.join(root, *expected["file"].split("/"))
    if not os.path.isfile(path):
        return {"ok": False, "notes": [f"文件不存在：{expected['file']}"]}
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        found = headings(handle.read())
    matches = [(lvl, txt, lineno) for lvl, txt, lineno in found if txt == expected["heading"]]
    ok = any(lvl == expected["level"] for lvl, _t, _l in matches)
    notes = []
    if not matches:
        notes.append(f"标题不存在：{expected['heading']!r}")
    elif not ok:
        notes.append(f"标题存在但层级不符：{matches}")
    elif matches[0][2] != expected.get("anchor_line"):
        notes.append(f"行号漂移：期望 {expected.get('anchor_line')} 实际 {matches[0][2]}（不判负）")
    return {"ok": bool(ok), "found_line": matches[0][2] if matches else None, "notes": notes}


def _verify_history(probe: dict, roots: dict) -> dict:
    action = probe["action"]
    root = roots["repo"]
    if action["op"] == "log_added":
        args = ["log", "--diff-filter=A", "--format=%H", "--", action["path"]]
    elif action["op"] == "log_pickaxe":
        args = ["log", "-S", action["pattern"], "--format=%H", "--", action["path"]]
    else:
        return {"ok": False, "notes": [f"未知历史动作：{action['op']}"]}
    shas = _git(root, args)
    ok = probe["expected"]["commit"] in shas
    notes = [] if ok else [f"期望提交不在结果里；实测 {shas[:4]}"]
    return {"ok": ok, "commits": shas[:4], "notes": notes}


def verify(probes_doc: dict, *, repo_root: str = REPO_ROOT) -> dict:
    import memory_agent.settings as settings  # 需要 KD_DIR；无模型

    roots = {
        "corpus": repo_root,
        "repo": repo_root,
        "kb": os.path.abspath(settings.KB_DIR),
    }
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

    runner = {"grep": _verify_grep, "read": _verify_read,
              "outline": _verify_outline, "history": _verify_history}
    rows = []
    for probe in probes_doc["probes"]:
        face = probe["face"]
        result = runner[face](probe, roots)
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
            "baseline_entry_id": entry,
            "baseline_entry_actual": entry_actual,
            "baseline_entry_ok": entry_ok,
        })
        if entry and not entry_ok:
            result["ok"] = False
            result["notes"] = list(result.get("notes") or []) + [
                f"baseline_entry_id 不自洽：期望 {entry} 实测 {entry_actual}"]
        rows.append(result)

    by_face: dict[str, dict] = {}
    for row in rows:
        bucket = by_face.setdefault(row["face"], {"n": 0, "ok": 0})
        bucket["n"] += 1
        bucket["ok"] += int(row["ok"])
    return {
        "mode": "verify",
        "n": len(rows),
        "ok": sum(1 for r in rows if r["ok"]),
        "failed": [r for r in rows if not r["ok"]],
        "by_face": by_face,
        "by_scope": {scope: sum(1 for r in rows if r["scope"] == scope)
                     for scope in ("corpus", "kb", "repo")},
        "entry_addressable": sum(1 for r in rows if r["baseline_entry_id"]),
        "structurally_unreachable": sum(1 for r in rows if not r["baseline_entry_id"]),
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
    from memory_agent.eval.harness.stats import bootstrap_ci, paired_diffs

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
                  "gen": stats_after.get("gen")},
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
    parser.add_argument("--baseline", action="store_true", help="跑一次性检索基线（需 BGE-M3）")
    parser.add_argument("--probes", default=PROBES_JSON)
    parser.add_argument("--out", default=None)
    parser.add_argument("--k", type=int, default=PROD_POOL)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--prod-index", default=None,
                        help="生产索引根（默认 = 主工作树 memory_agent/vector_db）")
    parser.add_argument("--index-dir", default=None,
                        help="临时索引根（默认 %%TEMP%%/eval71-nav/index）")
    args = parser.parse_args(argv)
    if not args.verify and not args.baseline:
        parser.error("至少要给 --verify 或 --baseline")

    probes_doc = load_probes(args.probes)
    rc = 0
    if args.verify:
        result = verify(probes_doc)
        print(f"[verify] {result['ok']}/{result['n']} 条通过；by_face={result['by_face']}")
        for row in result["failed"]:
            print(f"  FAIL {row['id']} ({row['face']}): {row['notes']}")
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                json.dump(result, handle, ensure_ascii=False, indent=1)
        rc = 0 if result["ok"] == result["n"] else 1

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
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
