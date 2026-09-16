"""issue #26 验收：安装后的 `memory_agent` 经**标准 MCP 客户端**跑通 search/get/add。

证明「可安装、不靠 CWD」：

- MCP server 用 `python -m memory_agent.mcp_server` 启动，**工作目录在仓库之外**的临时目录——
  源码树既不在 CWD、也不在 sys.path 上，只能靠安装好的包解析（`pip install -e ragcore -e memory_agent`
  或等效的已安装环境）。
- 官方 `mcp` 客户端经 stdio 调用 `memory_search` / `memory_get` / `memory_add`。

隔离（同 #16 sandbox）：真相源 = 真实 KB 的**已提交态** `git clone` 到临时目录；
派生索引 = 临时目录；只读语料 = 空。真实 KB 前后逐字不变。

前置：`venv\\Scripts\\python.exe -m pip install -e ragcore -e memory_agent`
运行（任意 CWD）：
    venv\\Scripts\\python.exe memory_agent/eval/mcp_install_smoke_26.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
DEFAULT_SOURCE_KB = os.environ.get(
    "MEMORY_SANDBOX_SOURCE_KB", r"C:\Users\Tan\.config\opencode\knowledge"
)

ADD_SLUG = "install-smoke-26-anchor"
ADD_ID = f"topics/{ADD_SLUG}"
ADD_TITLE = "Install smoke anchor ZETA2619"
ADD_BODY = (
    "Install-time fixture for issue #26. Marker ZETA2619: an installed memory_agent "
    "persists a durable fact through the standard stdio MCP client."
)
SEED_QUERY = "记忆架构 Markdown 真相源 派生索引 写入网关"

ROWS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    ROWS.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    return bool(ok)


def payload(result) -> object:
    """取 CallToolResult 的结构化返回（SDK 同时给 content 与 structured_content）。"""
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    text = result.content[0].text if result.content else ""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, IndexError):
        return text


def _run(cmd, *, cwd=None, env=None, check_=True) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8")
    if check_ and proc.returncode != 0:
        raise RuntimeError(f"命令失败（{proc.returncode}）：{' '.join(cmd)}\n{proc.stderr[-2000:]}")
    return proc


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return _run(["git", "-C", repo, *args])


def _head(repo: str) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _first_known_tag(kb_dir: str) -> str:
    with open(os.path.join(kb_dir, "tags.md"), "r", encoding="utf-8") as handle:
        tags = re.findall(r"^\s*-\s+`?([a-z0-9][a-z0-9-]*)`?\s*$", handle.read(), re.MULTILINE)
    if not tags:
        raise RuntimeError("tags.md 里没有受控标签")
    return tags[0]


def _child_env(kb_dir: str, index_dir: str) -> dict:
    env = dict(os.environ)
    env["AGENT_KB_DIR"] = kb_dir
    env["MEMORY_INDEX_DIR"] = index_dir
    env["MEMORY_READONLY_ROOTS"] = ""
    env["MEMORY_REINDEX_BATCH"] = "256"
    env.pop("MEMORY_ENV_FILE", None)
    return env


def _build_index(env: dict, workdir: str) -> dict:
    """子进程跑 `-m memory_agent.build_index`——与 server 分开，避免同时常驻两份 BGE-M3。"""
    proc = _run([sys.executable, "-m", "memory_agent.build_index"],
                cwd=workdir, env=env, check_=False)
    if proc.returncode != 0:
        raise RuntimeError(f"build_index 失败（{proc.returncode}）：\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


async def run_checks(kb_dir: str, workdir: str, env: dict) -> None:
    tag = _first_known_tag(kb_dir)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "memory_agent.mcp_server"],
        cwd=workdir,
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = [tool.name for tool in (await session.list_tools()).tools]
            check(
                "1. 安装包暴露记忆工具",
                {"memory_search", "memory_get", "memory_add"} <= set(tools),
                f"tools={sorted(tools)}",
            )

            status = payload(await session.call_tool("memory_index_status", {}))
            check(
                "2. 索引从克隆 KB 建成且自洽",
                bool(status.get("built")) and status.get("consistent") is True
                and status.get("entries") == status.get("points"),
                f"gen={status.get('gen')} entries={status.get('entries')} "
                f"points={status.get('points')}",
            )

            hits = payload(await session.call_tool(
                "memory_search", {"query": SEED_QUERY, "k": 5, "writable_only": True}
            ))
            check(
                "3. memory_search 返回可写条目",
                bool(hits) and all(hit["writable"] is True for hit in hits),
                f"top={[h['id'] for h in hits[:3]]}",
            )

            got = payload(await session.call_tool(
                "memory_get", {"entry_id": hits[0]["id"]}
            ))
            check(
                "4. memory_get 读回真实 Markdown",
                got.get("id") == hits[0]["id"] and bool(got.get("content")),
                f"id={got.get('id')} chars={len(got.get('content', ''))}",
            )

            head_before = _head(kb_dir)
            # allow_duplicate=true：#26 只验「安装包能执行写入工具」，去重闸门是 #16 的断言面
            # （关键词命中分 >1，在真实 KB 上会先命中候选；此处不重测该语义）。
            add = payload(await session.call_tool("memory_add", {
                "title": ADD_TITLE, "body": ADD_BODY, "domain": "topics",
                "type": "topic", "tags": [tag], "slug": ADD_SLUG,
                "allow_duplicate": True,
            }))
            check(
                "5. memory_add 写入并单文件 commit",
                add.get("status") == "written" and add.get("written") is True
                and add.get("commit") != head_before,
                f"status={add.get('status')} commit={str(add.get('commit'))[:10]} "
                f"candidates={add.get('candidates')}",
            )

            added = payload(await session.call_tool(
                "memory_search", {"query": ADD_TITLE, "k": 5, "writable_only": True}
            ))
            check(
                "6. 新条目立即可检索（查询时惰性刷新，D13）",
                any(hit["id"] == ADD_ID for hit in added),
                f"top={[h['id'] for h in added[:3]]}",
            )


def _rmtree(path: str) -> None:
    def _on_error(func, target, _exc):  # noqa: ANN001
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onerror=_on_error)


def main() -> int:
    parser = argparse.ArgumentParser(description="issue #26 安装 + MCP 集成冒烟")
    parser.add_argument("--source-kb", default=DEFAULT_SOURCE_KB)
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()

    source_kb = os.path.abspath(args.source_kb)
    if not os.path.isdir(os.path.join(source_kb, ".git")):
        print(f"找不到真实 KB git 仓库：{source_kb}", file=sys.stderr)
        return 2

    workdir = tempfile.mkdtemp(prefix="memory-install-smoke-")
    kb_dir = os.path.join(workdir, "kb")
    index_dir = os.path.join(workdir, "index")
    keep = os.environ.get("MEMORY_SANDBOX_KEEP") == "1"

    real_head_before = _head(source_kb)
    real_status_before = _git(source_kb, "status", "--short").stdout
    try:
        _run(["git", "clone", "--quiet", source_kb, kb_dir])
        env = _child_env(kb_dir, index_dir)

        print(f"工作目录（仓库外）: {workdir}")
        print(f"源码树是否在工作目录下: {os.path.abspath(REPO_ROOT).startswith(os.path.abspath(workdir))}")
        print(f"沙箱 KB: {kb_dir}\n沙箱索引: {index_dir}\n")

        stats = _build_index(env, workdir)
        check(
            "0. 子进程 `-m memory_agent.build_index` 成功且只读根为空",
            stats.get("readonly_roots") == [] and stats.get("kb_dir") == kb_dir,
            f"readonly_roots={stats.get('readonly_roots')}",
        )

        asyncio.run(run_checks(kb_dir, workdir, env))

        check(
            "7. 真实 KB HEAD 未变",
            _head(source_kb) == real_head_before,
            f"{real_head_before[:10]} -> {_head(source_kb)[:10]}",
        )
        check(
            "8. 真实 KB 工作树前后逐字一致",
            _git(source_kb, "status", "--short").stdout == real_status_before,
        )
    finally:
        if keep:
            print(f"\n[keep] 保留：{workdir}")
        else:
            _rmtree(workdir)

    passed = sum(1 for row in ROWS if row["ok"])
    total = len(ROWS)
    print(f"\n==== {passed}/{total} 通过 ====")
    for row in ROWS:
        if not row["ok"]:
            print(f"  FAIL: {row['name']} - {row['detail']}")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump({"passed": passed, "total": total, "rows": ROWS}, handle,
                      ensure_ascii=False, indent=2)
    return 0 if passed == total and total else 1


if __name__ == "__main__":
    raise SystemExit(main())
