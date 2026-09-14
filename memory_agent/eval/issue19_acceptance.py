"""issue #19 验收：单实例 daemon + stdio 代理（真实模型，真并发）。

要证明的事（与 issue #19 验收口径对应）：
1. 只有一个进程持有嵌入引擎（~3.9GB），其余会话是瘦客户端。
2. 多个会话并行 memory_search 均正常，无 Qdrant local-mode 锁错误。
3. daemon 已 eager 预热，首次检索不冷启动。
4. daemon 不可用时错误清晰（不静默、不返回空）。
5. 并发 memory_add 不撞 .git/index.lock。

为不污染真实 KB / 真实索引，本脚本用**临时 KB + 临时索引副本**启动 daemon。用法：
    venv\\Scripts\\python.exe memory_agent/eval/issue19_acceptance.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _ROOT)

from memory_agent.settings import INDEX_DIR, MCP_HTTP_HOST, MCP_HTTP_PORT  # noqa: E402

PY = sys.executable
PROXY = os.path.join(_ROOT, "memory_agent", "proxy.py")
SERVER = os.path.join(_ROOT, "memory_agent", "mcp_server.py")
WORK = os.path.join(
    os.environ.get("TEMP", "/tmp"), "opencode", f"issue19-{int(time.time())}"
)
KB = os.path.join(WORK, "kb")
INDEX = os.path.join(WORK, "index")
LOG = os.path.join(WORK, "daemon.log")
PORT = MCP_HTTP_PORT
HOST = MCP_HTTP_HOST
RESULTS: list[str] = []


def note(msg: str) -> None:
    print(msg, flush=True)
    RESULTS.append(msg)


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health_ok(timeout: float = 1.0) -> bool:
    try:
        with _opener().open(f"http://{HOST}:{PORT}/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def wait_for_log(marker: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(LOG, "r", encoding="utf-8", errors="replace") as f:
                if marker in f.read():
                    return True
        except OSError:
            pass
        time.sleep(0.5)
    return False


def one_embed_process_count() -> int:
    """统计「HTTP 常驻 daemon」里私有内存 >1GB 的进程数（排除旧 stdio 会话）。"""
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -like '*mcp_server.py*--transport*' } | "
        "ForEach-Object { (Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue)."
        "PrivateMemorySize64 } | Where-Object { $_ -gt 1GB } | Measure-Object | "
        "Select-Object -ExpandProperty Count"
    )
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True, encoding="utf-8")
    return int((out.stdout or "0").strip() or 0)


def setup_daemon():
    os.makedirs(KB, exist_ok=True)
    shutil.copytree(INDEX_DIR, INDEX)
    with open(os.path.join(KB, "tags.md"), "w", encoding="utf-8") as f:
        f.write("- demo\n- topic-x\n")
    for args in (("init", "-q"), ("config", "user.email", "t@example.com"),
                 ("config", "user.name", "t")):
        subprocess.run(["git", "-C", KB, *args], capture_output=True)

    env = dict(os.environ)
    env.update({"AGENT_KB_DIR": KB, "MEMORY_INDEX_DIR": INDEX})
    creationflags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    log = open(LOG, "wb")
    proc = subprocess.Popen(
        [PY, SERVER, "--transport", "http", "--host", HOST, "--port", str(PORT)],
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=_ROOT, env=env,
        creationflags=creationflags, close_fds=True,
    )
    log.close()
    for _ in range(160):
        if proc.poll() is not None:
            raise RuntimeError(f"daemon 提前退出（rc={proc.returncode}），见 {LOG}")
        if health_ok(0.5):
            return proc
        time.sleep(0.25)
    raise RuntimeError("daemon 未就绪")


async def _session_call(tool: str, args: dict):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=PY, args=[PROXY, "--port", str(PORT)], cwd=_ROOT, env=os.environ.copy(),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool, args)


def _decode(result) -> object:
    """把 CallToolResult 尽量还原成 JSON（优先 structured_content）。"""
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        if isinstance(structured, dict) and "result" in structured:
            return structured["result"]
        return structured
    text = result.content[0].text if result.content else ""
    try:
        data = json.loads(text)
    except (ValueError, IndexError):
        return None
    if isinstance(data, dict) and "result" in data:
        return data["result"]
    return data


async def concurrent_searches():
    queries = ["qdrant local mode 的锁语义", "memory index 代目录指针", "MCP stdio 铁律"]
    tasks = [asyncio.create_task(_session_call("memory_search", {"query": q, "k": 3}))
             for q in queries]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    ok = 0
    for q, res in zip(queries, results):
        if isinstance(res, Exception):
            note(f"  search {q!r}: ERROR {type(res).__name__}: {str(res)[:120]}")
        elif res.is_error:
            note(f"  search {q!r}: tool error {res.content[0].text[:120]}")
        else:
            data = _decode(res)
            if not isinstance(data, list):
                note(f"  search {q!r}: UNEXPECTED {res.content[0].text[:300]!r}")
                continue
            ok += 1
            note(f"  search {q!r}: {len(data)} hits, top score={data[0]['score']:.3f}" if data
                 else f"  search {q!r}: 0 hits")
    return ok


async def concurrent_adds():
    specs = [
        ("issue19 add alpha", "first concurrent write"),
        ("issue19 add beta", "second concurrent write"),
    ]
    tasks = [asyncio.create_task(_session_call("memory_add", {
        "title": t, "body": b, "domain": "topics", "type": "topic",
        "tags": ["demo"], "slug": s, "allow_duplicate": True,
    })) for (t, b), s in zip(specs, ("issue19-alpha", "issue19-beta"))]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    written = 0
    for res in results:
        if isinstance(res, Exception):
            note(f"  add: ERROR {type(res).__name__}: {str(res)[:160]}")
        elif res.is_error:
            note(f"  add: tool error {res.content[0].text[:160]}")
        else:
            payload = _decode(res) or {}
            written += 1 if payload.get("written") else 0
            note(f"  add: {payload.get('status')} id={payload.get('id')}")
    return written


def main() -> int:
    note(f"# issue #19 验收 @ {time.strftime('%Y-%m-%d %H:%M:%S')}")
    proc = setup_daemon()
    try:
        note(f"[1] daemon pid={proc.pid}, port={PORT}")
        ready = wait_for_log("memory index warmup done", 180)
        time.sleep(1.0)
        count = one_embed_process_count()
        note(f"[2] 私有内存 >1GB 的 python 进程数 = {count}（期望 1）")
        note(f"[3] eager 预热完成（log 出现 warmup done）= {ready}")

        note("[4] 3 个会话并发 memory_search：")
        ok = asyncio.run(concurrent_searches())
        note(f"  => 成功 {ok}/3")

        note("[5] 2 个会话并发 memory_add（临时 KB）：")
        written = asyncio.run(concurrent_adds())
        commits = subprocess.run(["git", "-C", KB, "rev-list", "--count", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()
        lock_files = [n for n in os.listdir(os.path.join(KB, ".git"))
                      if n == "index.lock"]
        note(f"  => 写入 {written}/2，临时 KB 提交数={commits}，残留 index.lock={lock_files}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        time.sleep(1.0)

    note("[6] daemon 关掉后（不经代理）直连：")
    down_ok = not health_ok(1.0)
    note(f"  /health 可达 = {not down_ok}（期望 False）")
    status = subprocess.run([PY, PROXY, "--status", "--port", str(PORT)],
                            capture_output=True, text=True, encoding="utf-8")
    note(f"  proxy --status: rc={status.returncode} out={status.stdout.strip()!r}")

    passed = (count == 1 and ready and ok == 3 and written == 2
              and not lock_files and down_ok)
    note(f"\n结论：{'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
