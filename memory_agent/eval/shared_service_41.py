"""issue #41 验收：一步安装 + 第二消费者共享同一 daemon（真传输，Stub 嵌入）。

要证明的事（与 issue #41 验收清单对应）：
1. 一步安装：`memory_agent.connect` 写 DeepTutor 部署级 mcp.json（streamableHttp →
   共享 daemon），**幂等**、可 dry-run，保留其它条目。
2. 两个消费者读同一 KB：streamableHttp 客户端 + `proxy.py` stdio 客户端连**同一**
   daemon → 同 `index_status().gen`、同 query 同 top-k id。
3. 共享真相（D9）：外部改一个已收录 `.md` → 两消费者下一次 `memory_search` 都反映，
   **无需重启**。
4. 写入可见：opencode 侧（stdio）`memory_add` → DeepTutor 侧（streamableHttp）
   `memory_search` 搜到；只读边界（D17）落在 DeepTutor 的 `enabled_tools` 白名单。
5. 用 **DeepTutor 自身**的 `load_mcp_config` / `validate_mcp_url`（系统 Python 3.12）
   校验写出的配置。

本题验的是**服务共享 / 基表共享 / 惰性刷 / 边界**，与嵌入质量无关：临时 daemon 注入
**确定性 Stub 嵌入**（无 BGE-M3、无网络、毫秒级），使 top-k 逐位可复现——与
`personal_mode_36.py` 同一口径。真实模型的 daemon 链路由 `issue19_acceptance.py` 覆盖。

临时 KB / 索引 / DeepTutor home，不污染真实语料。用法：
    venv\\Scripts\\python.exe memory_agent/eval/shared_service_41.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _ROOT)

from memory_agent import connect  # noqa: E402
from memory_agent.settings import MCP_HTTP_HOST  # noqa: E402

PY = sys.executable
PROXY = os.path.join(_ROOT, "memory_agent", "proxy.py")
SKILL_SOURCE = os.path.join(_ROOT, "memory_agent", "skill")

WORK = os.path.join(os.environ.get("TEMP", "/tmp"), "opencode",
                    f"shared-service-41-{int(time.time())}")
KB = os.path.join(WORK, "kb")
DOCS = os.path.join(WORK, "docs")
INDEX = os.path.join(WORK, "index")
DT_HOME = os.path.join(WORK, "deeptutor-home")
OPENCODE_HOME = os.path.join(WORK, "opencode-home")
LOG = os.path.join(WORK, "daemon.log")
WRAPPER = os.path.join(WORK, "stub_daemon.py")
HOST = MCP_HTTP_HOST
# 临时 daemon 用**独立端口**：不抢占 / 不停掉真实 daemon（8765 可能正在服务本会话）。
PORT = 0
URL = ""
RESULTS_FILE = os.path.join(_HERE, "shared_service_41_results.md")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]

MARKER = "zephyrhaptic"
WRITE_MARKER = "quillfeather"

RESULTS: list[str] = []
CHECKS: list[tuple[str, bool, str]] = []


# --------------------------------------------------------------------- 记录

def note(msg: str = "") -> None:
    print(msg, flush=True)
    RESULTS.append(msg)


def check(name: str, passed: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(passed), detail))
    note(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(passed)


def _decode(result) -> object:
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


def _text(result) -> str:
    return result.content[0].text if result.content else ""


# --------------------------------------------------------------------- 临时语料

KB_ENTRIES = {
    "topics/shared-service-alpha": (
        "Shared daemon transport",
        "The shared daemon exposes a single memory index to every consumer over the "
        "transport. Both consumers query the same base table and derived index.",
    ),
    "topics/shared-service-beta": (
        "Second consumer streamableHttp",
        "A second consumer connects with streamableHttp and reads the same base table. "
        "No separate server and no separate index are created for it.",
    ),
    "topics/shared-service-gamma": (
        "Lazy refresh freshness",
        "Lazy refresh keeps the derived index fresh without a watcher, so an external "
        "edit to a markdown file shows up on the next search.",
    ),
}


def _render_entry(entry_id: str, title: str, body: str) -> str:
    return (
        "---\n"
        f"id: {entry_id}\n"
        f"title: {json.dumps(title, ensure_ascii=False)}\n"
        "type: topic\n"
        "tags: [demo]\n"
        "status: current\n"
        "updated: 2026-09-16\n"
        "---\n\n"
        f"# {title}\n\n{body}\n"
    )


STUB_DAEMON_SOURCE = '''"""Test-only daemon: deterministic stub embedder (no BGE-M3, no network)."""
import hashlib
import sys

import ragcore.services.local_embedding_service as _les


class _StubEmbedder:
    dimension = 1024

    def __init__(self, *args, **kwargs):
        pass

    def _vec(self, text):
        vec = [0.0] * 1024
        for token in (text or "").lower().split():
            digest = hashlib.md5(token.encode("utf-8")).hexdigest()
            vec[int(digest, 16) % 1024] += 1.0
        norm = sum(value * value for value in vec) ** 0.5 or 1.0
        return [value / norm for value in vec]

    def embed_documents(self, texts):
        return [self._vec(text) for text in texts]

    def embed_query(self, query):
        return self._vec(query)


_les.LocalEmbeddingService = _StubEmbedder

from memory_agent.mcp_server import main

raise SystemExit(main(sys.argv[1:]))
'''


def _write_stub_daemon() -> None:
    with open(WRAPPER, "w", encoding="utf-8") as handle:
        handle.write(STUB_DAEMON_SOURCE)


def setup_workspace() -> None:
    for path in (KB, DOCS, INDEX, DT_HOME, OPENCODE_HOME):
        os.makedirs(path, exist_ok=True)
    _write_stub_daemon()
    with open(os.path.join(KB, "tags.md"), "w", encoding="utf-8") as handle:
        handle.write("- demo\n")
    for entry_id, (title, body) in KB_ENTRIES.items():
        path = os.path.join(KB, *entry_id.split("/")) + ".md"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_render_entry(entry_id, title, body))
    with open(os.path.join(DOCS, "README.md"), "w", encoding="utf-8") as handle:
        handle.write("# Docs\n\nDeployment-level configuration points the second "
                     "consumer at the loopback endpoint.\n")
    for args in (("init", "-q"), ("config", "user.email", "t@example.com"),
                 ("config", "user.name", "t"), ("add", "-A"),
                 ("commit", "-q", "-m", "seed")):
        subprocess.run(["git", "-C", KB, *args], capture_output=True)
    # 让“一步安装”里的 opencode 核验通过（模拟已注册的 opencode）。
    opencode_dir = os.path.dirname(connect.opencode_config_path(OPENCODE_HOME))
    os.makedirs(opencode_dir, exist_ok=True)
    with open(connect.opencode_config_path(OPENCODE_HOME), "w", encoding="utf-8") as handle:
        json.dump({"mcp": {connect.SERVER_NAME: {
            "type": "local", "command": [PY, PROXY], "enabled": True, "timeout": 20000,
        }}}, handle, ensure_ascii=False, indent=2)


def _daemon_env() -> dict:
    env = dict(os.environ)
    env.update({
        "AGENT_KB_DIR": KB,
        "MEMORY_INDEX_DIR": INDEX,
        "MEMORY_READONLY_ROOTS": DOCS,
        "MEMORY_OVERLAY_CONFIG": os.path.join(WORK, "overlay.json"),
        "MEMORY_READONLY_REPOS_CONFIG": os.path.join(WORK, "readonly_repos.json"),
        "MEMORY_MCP_HOST": HOST,
        "MEMORY_MCP_PORT": str(PORT),
        "MEMORY_MCP_PATH": "/mcp",
        "MEMORY_AUDIT_LOG": os.path.join(INDEX, "audit.log"),
        "MEMORY_DAEMON_LOG": LOG,
        "MEMORY_AUTH_REQUIRE_TOKEN": "0",
        "MEMORY_RERANK": "0",
    })
    env.pop("MEMORY_AUTH_TOKENS", None)
    env.pop("MEMORY_STORE_URL", None)
    return env


# --------------------------------------------------------------------- daemon

def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health_ok(timeout: float = 1.0) -> bool:
    try:
        with _opener().open(f"http://{HOST}:{PORT}/health", timeout=timeout) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001
        return False


def _kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


def start_daemon() -> subprocess.Popen:
    creationflags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    log = open(LOG, "wb")
    proc = subprocess.Popen(
        [PY, WRAPPER, "--transport", "http", "--host", HOST, "--port", str(PORT)],
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=_ROOT,
        env=_daemon_env(), creationflags=creationflags, close_fds=True,
    )
    log.close()
    for _ in range(240):
        if proc.poll() is not None:
            raise RuntimeError(f"daemon 提前退出（rc={proc.returncode}），见 {LOG}")
        if health_ok(0.5):
            return proc
        time.sleep(0.25)
    raise RuntimeError("daemon 未就绪")


# --------------------------------------------------------------------- 两个客户端

async def _call_stdio(tool: str, args: dict, timeout: float | None = None):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=PY, args=[PROXY, "--host", HOST, "--port", str(PORT)],
        cwd=_ROOT, env=_daemon_env(),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool, args, read_timeout_seconds=timeout)


async def _call_http(tool: str, args: dict, timeout: float | None = None):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    http_client = create_mcp_http_client()
    try:  # 本机回环不走代理
        http_client.trust_env = False
    except Exception:  # noqa: BLE001
        pass
    async with http_client:
        async with streamable_http_client(URL, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(tool, args, read_timeout_seconds=timeout)


TRANSPORTS = {"opencode-stdio": _call_stdio, "deeptutor-http": _call_http}


async def invoke(transport: str, tool: str, args: dict | None = None,
                 timeout: float | None = None):
    result = await TRANSPORTS[transport](tool, args or {}, timeout)
    if getattr(result, "is_error", False):
        raise RuntimeError(f"[{transport}] {tool} tool error: {_text(result)}")
    return _decode(result)


async def build_index_via_daemon() -> dict:
    """分块全量重建（daemon 内加载一次 BGE-M3；cursor 续调到 done）。"""
    result = await invoke("opencode-stdio", "memory_reindex",
                          {"cursor": None, "batch": 4}, timeout=900)
    while not result.get("done"):
        result = await invoke("opencode-stdio", "memory_reindex",
                              {"cursor": result["cursor"], "batch": 4}, timeout=900)
    return result


# --------------------------------------------------------------------- DeepTutor 校验

def deeptutor_python() -> str | None:
    candidates = [
        os.environ.get("DEEPTUTOR_PYTHON"),
        shutil.which("python"),
        r"D:\Users\Tan\AppData\Local\Programs\Python\Python312\python.exe",
    ]
    for candidate in candidates:
        if not candidate or not os.path.isfile(candidate):
            continue
        probe = subprocess.run([candidate, "-c", "import deeptutor"],
                               capture_output=True, text=True)
        if probe.returncode == 0:
            return candidate
    return None


DEEPTUTOR_SCRIPT = r"""
import json
from deeptutor.services.mcp.config import load_mcp_config, mcp_config_path
from deeptutor.services.mcp.network import validate_mcp_url

cfg = load_mcp_config()
entry = cfg.servers.get("memory-agent")
out = {"config_path": str(mcp_config_path()), "present": entry is not None}
if entry is not None:
    out.update({
        "resolved_type": entry.resolved_type(),
        "url": entry.url,
        "enabled": entry.enabled,
        "enabled_tools": list(entry.enabled_tools),
        "validate_deployment": list(validate_mcp_url(entry.url, strict=False)),
        "validate_strict_ok": validate_mcp_url(entry.url, strict=True)[0],
    })
print(json.dumps(out, ensure_ascii=False))
"""


def validate_with_deeptutor() -> dict | None:
    python = deeptutor_python()
    if python is None:
        return None
    env = dict(os.environ)
    env["DEEPTUTOR_HOME"] = DT_HOME
    result = subprocess.run([python, "-c", DEEPTUTOR_SCRIPT], capture_output=True,
                            text=True, encoding="utf-8", env=env, cwd=WORK)
    if result.returncode != 0:
        note(f"    DeepTutor 校验进程失败：{(result.stderr or '').strip()[:400]}")
        return None
    return json.loads(result.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------- 主流程

def main() -> int:
    global PORT, URL
    note(f"# issue #41 验收 @ {time.strftime('%Y-%m-%d %H:%M:%S')}")
    note(f"    workdir = {WORK}")
    PORT = _free_port()
    URL = connect.daemon_url(HOST, PORT)
    setup_workspace()

    # --- 1. 一步安装：DeepTutor 部署级 mcp.json（幂等 + dry-run） -------------
    note("\n[1] 一步安装（DeepTutor 部署级 mcp.json）")
    mcp_path = connect.deeptutor_mcp_config_path(DT_HOME)
    entry = connect.build_server_entry(URL)
    first_cfg, first_changed = connect.merge_mcp_config(
        connect.read_json_object(mcp_path), connect.SERVER_NAME, entry)
    connect.atomic_write_json(mcp_path, first_cfg)
    bytes_after_first = open(mcp_path, "rb").read()

    second_cfg, second_changed = connect.merge_mcp_config(
        connect.read_json_object(mcp_path), connect.SERVER_NAME, entry)
    bytes_after_second = open(mcp_path, "rb").read()
    check("安装写入 streamableHttp 条目", first_changed and not second_changed,
          f"changed: {first_changed} -> {second_changed}")
    check("幂等（第二次不改动文件字节）", bytes_after_first == bytes_after_second)

    # 保留其它条目
    extra = {"servers": {"keepme": {"type": "sse", "url": "https://example.com/sse"}}}
    merged, _ = connect.merge_mcp_config(extra, connect.SERVER_NAME, entry)
    check("合并保留其它服务条目",
          merged["servers"]["keepme"] == extra["servers"]["keepme"]
          and merged["servers"][connect.SERVER_NAME] == entry)

    dry_rc = connect.run(deeptutor_home=DT_HOME, url=URL, opencode_home=OPENCODE_HOME,
                         dry_run=True, ensure=False, skill_source=SKILL_SOURCE)
    check("dry-run 走通且不动 daemon", dry_rc == 0)

    install_rc = connect.run(deeptutor_home=DT_HOME, url=URL, opencode_home=OPENCODE_HOME,
                             dry_run=False, ensure=False, skill_source=SKILL_SOURCE)
    check("一步安装 PASS（opencode 已注册 + skill 落位）", install_rc == 0)
    check("skill 已落位到 opencode skills 目录",
          connect.skill_status(connect.opencode_skill_dir(OPENCODE_HOME)))

    # --- 2. DeepTutor 自身函数校验写出的配置 ---------------------------------
    note("\n[2] 用 DeepTutor 自身的 load_mcp_config / validate_mcp_url 校验")
    dt = validate_with_deeptutor()
    if dt is None:
        check("DeepTutor 侧校验", False, "未找到可 import deeptutor 的 Python")
    else:
        check("DeepTutor 读到 memory-agent 且解析为 streamableHttp",
              dt["present"] and dt["resolved_type"] == "streamableHttp",
              f"type={dt.get('resolved_type')} url={dt.get('url')}")
        check("部署级校验放行 loopback（strict=False）",
              dt["validate_deployment"] == [True, ""], str(dt["validate_deployment"]))
        check("自服务严格校验拦下 loopback（证明必须走部署级配置）",
              dt["validate_strict_ok"] is False)
        for forbidden in ("memory_add", "memory_supersede", "memory_archive",
                          "memory_reindex"):
            check(f"只读边界：enabled_tools 不含 {forbidden}",
                  forbidden not in dt["enabled_tools"])
        check("只读白名单 = search/get/index_status/ingest_list",
              set(dt["enabled_tools"]) == set(connect.READONLY_TOOLS),
              ", ".join(dt["enabled_tools"]))

    # --- 3. 临时 daemon + 建索引 --------------------------------------------
    note("\n[3] 启动临时 daemon（Stub 嵌入，确定性）并重建索引")
    proc = start_daemon()
    note(f"    daemon pid={proc.pid} url={URL}")
    try:
        built = asyncio.run(build_index_via_daemon())
        expected = len(KB_ENTRIES) + 1
        check("索引重建完成且条数正确", built.get("entries") == expected,
              f"entries={built.get('entries')} (期望 {expected})")

        # --- 4. 两传输共享同一 daemon -----------------------------------
        note("\n[4] streamableHttp 与 stdio 代理连同一 daemon")
        status_http = asyncio.run(invoke("deeptutor-http", "memory_index_status"))
        status_stdio = asyncio.run(invoke("opencode-stdio", "memory_index_status"))
        check("两消费者同 index_status().gen",
              status_http["gen"] == status_stdio["gen"],
              f"gen={status_http['gen']}")
        check("两消费者同条数且自洽",
              status_http["entries"] == status_stdio["entries"]
              and status_http.get("consistent") and status_stdio.get("consistent"),
              f"entries={status_http['entries']}")

        query = "second consumer streamableHttp shared base table"
        hits_http = asyncio.run(invoke("deeptutor-http", "memory_search",
                                       {"query": query, "k": 3}))
        hits_stdio = asyncio.run(invoke("opencode-stdio", "memory_search",
                                        {"query": query, "k": 3}))
        ids_http = [hit["id"] for hit in hits_http]
        ids_stdio = [hit["id"] for hit in hits_stdio]
        check("同 query 同 top-k id",
              ids_http == ids_stdio and "topics/shared-service-beta" in ids_http,
              f"{ids_http}")

        # --- 5. 共享真相：外部改文件 → 惰性追平（D9） ---------------------
        note("\n[5] 外部改一个已收录 .md → 两消费者下一次 search 都反映（无重启）")
        before = asyncio.run(invoke("deeptutor-http", "memory_search",
                                    {"query": MARKER, "k": 3}))
        check("改动前 marker 不可见", all(h["id"] != "topics/shared-service-alpha"
                                          for h in before))
        alpha_path = os.path.join(KB, "topics", "shared-service-alpha.md")
        with open(alpha_path, "a", encoding="utf-8") as handle:
            handle.write(f"\n\n{MARKER} appears right after the external markdown edit.\n")
        after_http = asyncio.run(invoke("deeptutor-http", "memory_search",
                                        {"query": MARKER, "k": 3}))
        after_stdio = asyncio.run(invoke("opencode-stdio", "memory_search",
                                         {"query": MARKER, "k": 3}))
        check("DeepTutor 侧（http）搜到外部改动",
              after_http and after_http[0]["id"] == "topics/shared-service-alpha",
              after_http[0]["id"] if after_http else "no hits")
        check("opencode 侧（stdio）搜到外部改动",
              after_stdio and after_stdio[0]["id"] == "topics/shared-service-alpha",
              after_stdio[0]["id"] if after_stdio else "no hits")
        status_after = asyncio.run(invoke("deeptutor-http", "memory_index_status"))
        check("惰性追平后同 gen（未换代）且自洽",
              status_after["gen"] == status_http["gen"] and status_after["consistent"],
              f"gen={status_after['gen']}")

        # --- 6. 跨消费者写 → 读（只读边界下的可见性） --------------------
        note("\n[6] opencode（stdio）写 → DeepTutor（http）读可见")
        added = asyncio.run(invoke("opencode-stdio", "memory_add", {
            "title": f"Shared service write visibility {WRITE_MARKER}",
            "body": f"Written by the opencode side; {WRITE_MARKER} must be readable "
                    "from the second consumer on its next search.",
            "domain": "topics", "type": "topic", "tags": ["demo"],
            "slug": "shared-service-write-visibility", "allow_duplicate": True,
        }))
        check("stdio 侧 memory_add 落盘", bool(added.get("written")),
              f"id={added.get('id')}")
        found_http = asyncio.run(invoke("deeptutor-http", "memory_search",
                                        {"query": WRITE_MARKER, "k": 3}))
        found_stdio = asyncio.run(invoke("opencode-stdio", "memory_search",
                                         {"query": WRITE_MARKER, "k": 3}))
        new_id = added.get("id")
        check("DeepTutor 侧搜到 opencode 写入的条目",
              any(h["id"] == new_id for h in found_http), new_id)
        check("两消费者对新写入条目可见性一致",
              [h["id"] for h in found_http] == [h["id"] for h in found_stdio])
    finally:
        _kill_tree(proc.pid)
        time.sleep(1.0)

    # --- 7. 本机真实安装核验（只读） ----------------------------------------
    note("\n[7] 本机 opencode 注册 / skill 落位核验（只读）")
    real_registration = connect.opencode_registration(
        connect.read_json_object(connect.opencode_config_path()))
    check("本机 opencode 已注册 memory-agent → proxy.py", real_registration["ok"],
          real_registration["detail"])
    check("本机全局 skill 已落位", connect.skill_status(connect.opencode_skill_dir()))

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    verdict = "PASS" if passed == total else "FAIL"
    note(f"\n结论：{verdict}  ({passed}/{total})")
    note("（临时 daemon 已停；真实 daemon 未被打扰）")
    write_results(verdict, passed, total)
    return 0 if passed == total else 1


def write_results(verdict: str, passed: int, total: int) -> None:
    """把结论 + PASS 矩阵 + 运行日志写成 UTF-8 的 `_results.md`。"""
    lines = [
        "# #41 共享服务验收结果（第二消费者 = DeepTutor）",
        "",
        f"- 日期：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 脚本：`memory_agent/eval/shared_service_41.py`",
        f"- 结论：**{verdict}**（{passed}/{total}）",
        f"- 临时工作区：`{WORK}`（临时 KB / 索引 / DeepTutor home；真实语料与真实 daemon 未动）",
        "",
        "## PASS 矩阵",
        "",
        "| # | 检查 | 结果 | 详情 |",
        "|---|------|------|------|",
    ]
    for index, (name, ok, detail) in enumerate(CHECKS, start=1):
        safe_detail = str(detail).replace("|", "\\|")
        lines.append(f"| {index} | {name} | {'PASS' if ok else 'FAIL'} | {safe_detail} |")
    lines += ["", "## 运行日志", "", "```text", *RESULTS, "```", ""]
    with open(RESULTS_FILE, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    print(f"[结果] 已写入 {RESULTS_FILE}")


if __name__ == "__main__":
    raise SystemExit(main())
