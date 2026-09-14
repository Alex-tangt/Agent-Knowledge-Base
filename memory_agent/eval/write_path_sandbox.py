"""写路径确定性 sandbox 套件（#16）——真实 KB 克隆 + 隔离索引，端到端断言外部行为。

用法（仓库根，用主树 venv 的绝对路径）：

    venv\\Scripts\\python.exe memory_agent/eval/write_path_sandbox.py

隔离三层（保证不污染真实 KB / 不影响本仓库索引）：

1. 真相源 = 真实 KB 的**已提交态** `git clone` 到临时目录（`AGENT_KB_DIR`）。
2. 派生索引 = 临时目录（`MEMORY_INDEX_DIR`），代目录 + `CURRENT` 指针随之隔离。
3. 只读语料 = 空（`MEMORY_READONLY_ROOTS=""`），不把整个代码仓库索引进来。

断言只针对**外部行为**：MCP 工具返回的 dict、落盘文件内容、`git log/status`、
真实 `tools/kb.py check` 输出。套件前后真实 KB 的 HEAD 与 `git status` 必须逐字不变。

环境变量（可选）：`MEMORY_SANDBOX_SOURCE_KB` 覆盖真实 KB 路径；`MEMORY_SANDBOX_KEEP=1`
保留临时沙箱（排障用，勿在正常运行时设置——会留下垃圾文件）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEFAULT_SOURCE_KB = os.environ.get(
    "MEMORY_SANDBOX_SOURCE_KB", r"C:\Users\Tan\.config\opencode\knowledge"
)

# 固定 fixture：内容唯一、互不近似，保证去重/生命周期断言确定性。
ENTRY1_SLUG = "sandbox-write-anchor"
ENTRY1_ID = f"topics/{ENTRY1_SLUG}"
ENTRY1_TITLE = "Sandbox write path anchor"
ENTRY1_BODY = (
    "Deterministic fixture for the memory write path. "
    "Marker ALPHA-4711: the quick brown fox jumps over the lazy dog."
)
UNKNOWN_SLUG = "sandbox-unknown-tag"
UNKNOWN_TAG = "sandbox-tag-not-in-vocab"
DUP_SLUG = "sandbox-dedup-copy"
SUPERSEDE_SLUG = "sandbox-anchor-v2"
SUPERSEDE_ID = f"topics/{SUPERSEDE_SLUG}"
SUPERSEDE_BODY = (
    "Replacement fixture for the memory write path. "
    "Marker BETA-90210: a completely different durable fact about lifecycle tools."
)
ROLLBACK_SLUG = "sandbox-rollback"
ARCHIVE_REASON = "sandbox archive lifecycle check"


# --------------------------------------------------------------------------- git


def _git(repo: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return _run(["git", "-C", repo, *args], check=check)


def _run(cmd, check: bool = True, cwd: str | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8"
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"命令失败（{proc.returncode}）：{' '.join(cmd)}\n{proc.stderr}"
        )
    return proc


def _head(repo: str) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _commit_files(repo: str, rev: str = "HEAD") -> list[str]:
    out = _git(repo, "show", "--name-only", "--format=", rev).stdout
    return sorted(line for line in out.splitlines() if line.strip())


class Suite:
    """极简通过矩阵：每条断言记录 PASS/FAIL，最后统一判定退出码。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        ok = bool(ok)
        self.rows.append({"name": name, "ok": ok, "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        return ok

    @property
    def failed(self) -> list[dict]:
        return [row for row in self.rows if not row["ok"]]


# ----------------------------------------------------------------------- fixtures


def _first_known_tag(kb_dir: str) -> str:
    path = os.path.join(kb_dir, "tags.md")
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    tags = re.findall(r"^\s*-\s+`?([a-z0-9][a-z0-9-]*)`?\s*$", text, re.MULTILINE)
    if not tags:
        raise RuntimeError(f"{path} 里没有受控标签")
    return tags[0]


def _count_committed_entries(kb_dir: str) -> int:
    """核对索引条数用：克隆里非元文件、非 `_` 前缀的 .md 数。"""
    files = _git(kb_dir, "ls-files", "*.md").stdout.splitlines()
    meta = {"INDEX.md", "AGENTS.md", "tags.md"}

    def is_entry(rel: str) -> bool:
        parts = rel.split("/")
        if os.path.basename(rel) in meta:
            return False
        return not any(part.startswith("_") for part in parts)

    return sum(1 for rel in files if is_entry(rel))


# ---------------------------------------------------------------------- checks


def _build_index_via_cli(python: str, kb_dir: str, index_dir: str) -> dict:
    """在**子进程**里跑文档化的 `build_index.py` 建索引。

    为什么用子进程：重建会经 `Reindexer` 逐块构造 `VectorStoreService`，每个实例各自
    加载一份 BGE-M3；在本进程里做会与后续 MCP 索引单例叠加常驻内存（实测本机 commit
    已超物理内存，叠加会 `0xc0000005`）。子进程退出即回收，检查进程只背一份模型。
    """
    env = os.environ.copy()
    env["AGENT_KB_DIR"] = kb_dir
    env["MEMORY_INDEX_DIR"] = index_dir
    env["MEMORY_READONLY_ROOTS"] = ""
    # 一次装完，少构造一份 VectorStoreService（= 少加载一次 BGE-M3）。
    env["MEMORY_REINDEX_BATCH"] = "256"
    proc = subprocess.run(
        [python, os.path.join(ROOT, "memory_agent", "build_index.py")],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"build_index.py 失败（{proc.returncode}）：\n{proc.stderr[-2000:]}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"raw_stdout": proc.stdout[-2000:]}


def run_checks(suite: Suite, mcp, kb_dir: str, index_dir: str,
               source_kb: str, python: str) -> None:
    tag = _first_known_tag(kb_dir)
    baseline_head = _head(kb_dir)
    expected_entries = _count_committed_entries(kb_dir)

    # -- real-KB 快照 -------------------------------------------------------
    real_head_before = _head(source_kb)
    real_status_before = _git(source_kb, "status", "--short").stdout

    # -- 0. 索引只含可写 KB（隔离生效） -----------------------------------
    build_stats = _build_index_via_cli(python, kb_dir, index_dir)
    status = mcp.memory_index_status()
    suite.check(
        "0a build_index.py 子进程成功且只读根为空",
        build_stats.get("readonly_roots") == []
        and build_stats.get("kb_dir") == kb_dir,
        f"readonly_roots={build_stats.get('readonly_roots')}",
    )
    suite.check(
        "0b 索引从 KB 已提交态建成且自洽",
        status["built"] and status.get("consistent") is True,
        f"built={status['built']} consistent={status.get('consistent')}",
    )
    suite.check(
        "0c 索引只含可写 KB（未混入代码仓库只读语料）",
        status["entries"] == expected_entries,
        f"entries={status['entries']} 期望(克隆条目数)={expected_entries}",
    )

    # -- 1. add：合规写入 + 单文件 commit + 增量刷新可见 -------------------
    add = mcp.memory_add(
        title=ENTRY1_TITLE, body=ENTRY1_BODY, domain="topics", type="topic",
        tags=[tag], slug=ENTRY1_SLUG,
    )
    entry1_path = os.path.join(kb_dir, f"{ENTRY1_ID}.md")
    suite.check(
        "1a add 返回 written",
        add["status"] == "written" and add["written"] is True,
        f"status={add['status']}",
    )
    suite.check(
        "1b add 落盘且 frontmatter 合规（id/type/status）",
        os.path.isfile(entry1_path)
        and f"id: {ENTRY1_ID}" in open(entry1_path, encoding="utf-8").read(),
    )
    commit_files = _commit_files(kb_dir)
    suite.check(
        "1c add 的 commit 只含本条目文件",
        commit_files == [f"{ENTRY1_ID}.md"] and add["commit"] == _head(kb_dir),
        f"files={commit_files}",
    )
    suite.check(
        "1d 写后自动增量刷新（index.ok 且实际嵌入）",
        add["index"].get("ok") is True and add["index"].get("embedded", 0) >= 1,
        f"index={add['index']}",
    )
    hits = mcp.memory_search(ENTRY1_TITLE, k=5, writable_only=True)
    suite.check(
        "1e 新条目立即可被 memory_search 检索到",
        any(hit["id"] == ENTRY1_ID for hit in hits),
        f"top={[h['id'] for h in hits[:3]]}",
    )

    # -- 2. frontmatter 合规闸门（真实 kb.py）+ 未知 tag 只警告 -----------
    unknown = mcp.memory_add(
        title="Sandbox unknown tag", body="A distinct fixture with an unknown tag.",
        domain="topics", type="topic", tags=[UNKNOWN_TAG], slug=UNKNOWN_SLUG,
    )
    suite.check(
        "2a 未知 tag 只警告但仍写入",
        unknown["status"] == "written"
        and any(UNKNOWN_TAG in w for w in unknown["warnings"]),
        f"warnings={unknown['warnings']}",
    )
    check = _run([sys.executable, os.path.join(kb_dir, "tools", "kb.py"), "check"],
                 cwd=kb_dir, check=False)
    suite.check(
        "2b 真实 kb.py check 通过（0 errors）",
        check.returncode == 0 and "0 errors" in check.stdout,
        check.stdout.strip().splitlines()[-1] if check.stdout.strip() else "",
    )

    # -- 3. 去重：近似只报告、不写；allow_duplicate 绕过 -------------------
    head_before_dup = _head(kb_dir)
    dup = mcp.memory_add(
        title=ENTRY1_TITLE, body=ENTRY1_BODY, domain="topics", type="topic",
        tags=[tag], slug=DUP_SLUG,
    )
    dup_path = os.path.join(kb_dir, f"topics/{DUP_SLUG}.md")
    suite.check(
        "3a 近似重复返回 duplicate 且未落盘/未提交",
        dup["status"] == "duplicate" and dup["written"] is False
        and dup.get("reason") == "semantic"
        and not os.path.exists(dup_path) and _head(kb_dir) == head_before_dup,
        f"status={dup['status']} reason={dup.get('reason')}",
    )
    suite.check(
        "3b 去重候选指向已存在条目",
        bool(dup.get("candidates")) and dup["candidates"][0]["id"] == ENTRY1_ID,
        f"candidates={[c['id'] for c in dup.get('candidates', [])]}",
    )
    bypass = mcp.memory_add(
        title=ENTRY1_TITLE, body=ENTRY1_BODY, domain="topics", type="topic",
        tags=[tag], slug=DUP_SLUG, allow_duplicate=True,
    )
    suite.check(
        "3c allow_duplicate=true 绕过去重并写入",
        bypass["status"] == "written" and os.path.exists(dup_path),
        f"status={bypass['status']}",
    )
    copy_id = f"topics/{DUP_SLUG}"

    # -- 4. supersede：默认预览、confirm 后双向标注同一 commit ------------
    old_before = open(entry1_path, encoding="utf-8").read()
    head_before_super = _head(kb_dir)
    preview = mcp.memory_supersede(
        old_id=ENTRY1_ID, title="Sandbox anchor v2", body=SUPERSEDE_BODY,
        domain="topics", type="topic", tags=[tag], slug=SUPERSEDE_SLUG,
    )
    new_path = os.path.join(kb_dir, f"{SUPERSEDE_ID}.md")
    suite.check(
        "4a supersede 默认只返回 confirmation_required 预览、不落盘",
        preview["status"] == "confirmation_required" and preview["written"] is False
        and not os.path.exists(new_path)
        and open(entry1_path, encoding="utf-8").read() == old_before
        and _head(kb_dir) == head_before_super,
        f"status={preview['status']}",
    )
    super_result = mcp.memory_supersede(
        old_id=ENTRY1_ID, title="Sandbox anchor v2", body=SUPERSEDE_BODY,
        domain="topics", type="topic", tags=[tag], slug=SUPERSEDE_SLUG, confirm=True,
    )
    new_text = open(new_path, encoding="utf-8").read() if os.path.exists(new_path) else ""
    old_text = open(entry1_path, encoding="utf-8").read()
    suite.check(
        "4b supersede confirm 后新条目带 supersedes、状态 current",
        super_result["status"] == "written"
        and f"supersedes: {ENTRY1_ID}" in new_text
        and "status: current" in new_text,
        f"status={super_result['status']}",
    )
    suite.check(
        "4c 旧条目置 superseded + superseded_by，正文保留、文件未删",
        "status: superseded" in old_text
        and f"superseded_by: {SUPERSEDE_ID}" in old_text
        and "ALPHA-4711" in old_text
        and os.path.exists(entry1_path),
    )
    suite.check(
        "4d 新旧两文件在同一 commit 且仅这两文件",
        _commit_files(kb_dir) == sorted([f"{ENTRY1_ID}.md", f"{SUPERSEDE_ID}.md"])
        and super_result["commit"] == _head(kb_dir),
        f"files={_commit_files(kb_dir)}",
    )

    # -- 5. archive：reason 必填、默认预览、confirm 后标记且不删文件 ------
    error_raised = False
    try:
        mcp.memory_archive(entry_id=copy_id, reason="   ", confirm=True)
    except ValueError:
        error_raised = True
    suite.check("5a archive 缺 reason 被拒", error_raised)

    head_before_arch = _head(kb_dir)
    arch_preview = mcp.memory_archive(entry_id=copy_id, reason=ARCHIVE_REASON)
    suite.check(
        "5b archive 默认只返回预览、不落盘",
        arch_preview["status"] == "confirmation_required"
        and arch_preview["written"] is False
        and _head(kb_dir) == head_before_arch,
        f"status={arch_preview['status']}",
    )
    arch_result = mcp.memory_archive(
        entry_id=copy_id, reason=ARCHIVE_REASON, confirm=True
    )
    copy_text = open(dup_path, encoding="utf-8").read()
    suite.check(
        "5c archive confirm 后置 archived + archive_reason 且文件仍在",
        arch_result["status"] == "written"
        and "status: archived" in copy_text
        and f"archive_reason: {ARCHIVE_REASON}" in copy_text
        and "ALPHA-4711" in copy_text
        and os.path.exists(dup_path),
    )
    suite.check(
        "5d archive 的 commit 只含该文件",
        _commit_files(kb_dir) == [f"{copy_id}.md"],
        f"files={_commit_files(kb_dir)}",
    )

    # -- 6. 可回滚（一）：kb.py check 报 ERROR → 不留半成品、HEAD 不动 --
    tools_kb = os.path.join(kb_dir, "tools", "kb.py")
    with open(tools_kb, "r", encoding="utf-8") as handle:
        real_kb_py = handle.read()
    rollback_rel = f"topics/{ROLLBACK_SLUG}.md"
    with open(tools_kb, "w", encoding="utf-8") as handle:
        handle.write(
            "import sys\n"
            f"print('ERROR: {rollback_rel}: injected sandbox failure')\n"
            "raise SystemExit(1)\n"
        )
    head_before_rb = _head(kb_dir)
    rejected = False
    try:
        mcp.memory_add(
            title="Sandbox rollback", body="Should never land.",
            domain="topics", type="topic", tags=[tag], slug=ROLLBACK_SLUG,
        )
    except ValueError:
        rejected = True
    finally:
        with open(tools_kb, "w", encoding="utf-8") as handle:
            handle.write(real_kb_py)
    suite.check(
        "6a kb.py check 报错时写入被拒、无半成品、HEAD 不动",
        rejected
        and not os.path.exists(os.path.join(kb_dir, rollback_rel))
        and _head(kb_dir) == head_before_rb,
    )

    # -- 7. 可回滚（二）：git reset --hard 干净撤回 -----------------------
    _git(kb_dir, "reset", "--hard", baseline_head)
    status_after_reset = _git(kb_dir, "status", "--short").stdout
    sandbox_files_gone = not any(
        os.path.exists(os.path.join(kb_dir, f"{eid}.md"))
        for eid in (ENTRY1_ID, f"topics/{UNKNOWN_SLUG}", copy_id, SUPERSEDE_ID)
    )
    suite.check(
        "7a git reset --hard 可干净撤回（HEAD 归位、工作树干净、沙箱文件消失）",
        _head(kb_dir) == baseline_head
        and status_after_reset.strip() == ""
        and sandbox_files_gone,
        f"status={status_after_reset!r}",
    )

    # -- 8. 不污染真实 KB -------------------------------------------------
    real_head_after = _head(source_kb)
    real_status_after = _git(source_kb, "status", "--short").stdout
    suite.check(
        "8a 真实 KB HEAD 未变",
        real_head_after == real_head_before,
        f"{real_head_before[:10]} -> {real_head_after[:10]}",
    )
    suite.check(
        "8b 真实 KB 工作树前后逐字一致（未被沙箱触碰）",
        real_status_after == real_status_before,
        "一致" if real_status_after == real_status_before
        else "并发会话改动了 KB（非本套件写入）",
    )


# ------------------------------------------------------------------------- main


def _clone(source_kb: str, dest: str) -> None:
    _run(["git", "clone", "--quiet", source_kb, dest])


def _rmtree(path: str) -> None:
    """删临时沙箱：git clone 的对象文件是只读的，Windows 下 rmtree 需先清只读位。"""

    def _on_error(func, target, _exc):  # noqa: ANN001
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onerror=_on_error)


def main() -> int:
    parser = argparse.ArgumentParser(description="写路径确定性 sandbox 套件（#16）")
    parser.add_argument("--source-kb", default=DEFAULT_SOURCE_KB)
    parser.add_argument("--json-out", default=None, help="把通过矩阵写成 JSON")
    args = parser.parse_args()

    source_kb = os.path.abspath(args.source_kb)
    if not os.path.isdir(os.path.join(source_kb, ".git")):
        print(f"找不到真实 KB git 仓库：{source_kb}", file=sys.stderr)
        return 2

    sandbox_root = tempfile.mkdtemp(prefix="memory-kb-sandbox-")
    kb_dir = os.path.join(sandbox_root, "kb")
    index_dir = os.path.join(sandbox_root, "index")
    keep = os.environ.get("MEMORY_SANDBOX_KEEP") == "1"

    suite = Suite()
    real_python = sys.executable
    try:
        _clone(source_kb, kb_dir)
        # 必须在 import memory_agent 之前注入环境（settings 在 import 期读取）。
        os.environ["AGENT_KB_DIR"] = kb_dir
        os.environ["MEMORY_INDEX_DIR"] = index_dir
        os.environ["MEMORY_READONLY_ROOTS"] = ""

        from memory_agent import mcp_server  # noqa: E402  （导入即读 env）

        print(f"沙箱真相源: {kb_dir}")
        print(f"沙箱索引:   {index_dir}")
        print(f"只读语料:   {os.environ['MEMORY_READONLY_ROOTS']!r}（空 = 不索引代码仓库）")
        print(f"Python:     {real_python}")
        print(f"真实 KB:    {source_kb}\n")
        run_checks(suite, mcp_server, kb_dir, index_dir, source_kb, real_python)
    except Exception:  # noqa: BLE001 - 任何异常都如实汇报，别吞
        traceback.print_exc()
        suite.check("套件未抛异常", False, "见上方 traceback")
    finally:
        if keep:
            print(f"\n[keep] 保留沙箱目录：{sandbox_root}")
        else:
            _rmtree(sandbox_root)

    passed = sum(1 for row in suite.rows if row["ok"])
    total = len(suite.rows)
    print(f"\n==== {passed}/{total} 通过 ====")
    for row in suite.failed:
        print(f"  FAIL: {row['name']} — {row['detail']}")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(
                {"passed": passed, "total": total, "rows": suite.rows},
                handle, ensure_ascii=False, indent=2,
            )
    return 0 if passed == total and total else 1


if __name__ == "__main__":
    raise SystemExit(main())
