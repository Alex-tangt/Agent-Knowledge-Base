"""issue #42 场景评测：agent loop 的读 / 写 / 冲突裁决（真 MCP 经 daemon，沙箱）。

要证明的事（对应 issue #42 验收清单）：
A. 确定性——`exclude_retired` 行为 + 工具契约（不调 LLM、只断言外部行为）：
   A1 默认 False：已退役条目（superseded / archived）**照常可见**（不静默改行为）。
   A2 `exclude_retired=True`：superseded / archived 被排除。
   A3 保留 `current` / `draft`。
   A4 **无 status 的只读语料不被误伤**（语义是「排除已退役」而非「只要 current」）。
   A5 命中契约：带 `owner` / `status`；唯一写入域 = 全局 KB（`writable`）。
   A6 退役项占据前排时仍回填够 k 条（多取召回池再筛）。
B. dogfood——一次真实「存 -> 新会话召回 -> supersede -> 召回取新弃旧」：
   B1 `memory_add` 落盘（真 git commit）。
   B2 **新会话**召回得到该事实。
   B3 `memory_supersede` 默认只预览、不落盘（文件与 git 未动）。
   B4 `confirm=true` 落盘：新旧双向标注（`supersedes` / `superseded_by`）+ 一个 commit。
   B5 新会话 `exclude_retired=True` 召回**只**剩新条目（取新弃旧）。
   B6 新会话默认召回两条都在（证明 A1/A2 就是开关的差别）。

隔离与成本：临时 KB / 只读语料 / 索引 / daemon，全在临时目录；真实全局 KB、真实语料、
真实 daemon 不被触碰。daemon 注入**确定性 Stub 嵌入**（无 BGE-M3、无网络、秒级）——
本票验的是读 / 写 / 裁决机制，与嵌入质量无关。

用法（仓库根，主树 venv 绝对路径）：
    venv\\Scripts\\python.exe memory_agent/eval/agent_loop_42.py
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
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from memory_agent.settings import MCP_HTTP_HOST  # noqa: E402

PY = sys.executable
PROXY = os.path.join(_ROOT, "memory_agent", "proxy.py")

WORK = os.path.join(os.environ.get("TEMP", "/tmp"), "opencode",
                    f"agent-loop-42-{int(time.time())}")
KB = os.path.join(WORK, "kb")
DOCS = os.path.join(WORK, "docs")
INDEX = os.path.join(WORK, "index")
LOG = os.path.join(WORK, "daemon.log")
WRAPPER = os.path.join(WORK, "stub_daemon.py")
HOST = MCP_HTTP_HOST
PORT = 0  # 独立端口：不抢占 / 不停掉真实 daemon（8765 可能正在服务本会话）
RESULTS_FILE = os.path.join(_HERE, "agent_loop_42_results.md")

# 标记 token：ASCII 单词，Stub 嵌入按 blank 切分 → 精确命中、可复现。
MK_SUPERSEDED = "OLDRETIREDSUPERSEDED42"
MK_ARCHIVED = "OLDRETIREDARCHIVED42"
MK_CURRENT = "LIVECURRENT42"
MK_DRAFT = "LIVEDRAFT42"
MK_READONLY = "READONLYNOSTATUS42"
MK_BACKFILL = "BACKFILLMARKER42"
MK_DOGFOOD = "DOGFOODWALRUS42"

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


# --------------------------------------------------------------------- 沙箱语料

def _render(path: str, entry_id: str, title: str, status: str | None, body: str) -> None:
    front = [f"id: {entry_id}", f"title: {json.dumps(title, ensure_ascii=False)}",
             "type: topic", "tags: [demo]"]
    if status is not None:
        front.append(f"status: {status}")
    front.append("updated: 2026-09-16")
    text = "---\n" + "\n".join(front) + "\n---\n\n" + f"# {title}\n\n{body}\n"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _kb_path(entry_id: str) -> str:
    return os.path.join(KB, *entry_id.split("/")) + ".md"


def setup_workspace() -> None:
    for path in (KB, DOCS, INDEX):
        os.makedirs(path, exist_ok=True)
    _write_stub_daemon()
    with open(os.path.join(KB, "tags.md"), "w", encoding="utf-8") as handle:
        handle.write("- demo\n")
    _render(_kb_path("topics/retired-superseded"), "topics/retired-superseded",
            "Retired superseded note", "superseded",
            f"An entry already replaced by a newer one. {MK_SUPERSEDED}")
    _render(_kb_path("topics/retired-archived"), "topics/retired-archived",
            "Retired archived note", "archived",
            f"An entry retired with a reason. {MK_ARCHIVED}")
    _render(_kb_path("topics/live-current"), "topics/live-current",
            "Live current note", "current", f"A durable current fact. {MK_CURRENT}")
    _render(_kb_path("topics/live-draft"), "topics/live-draft",
            "Live draft note", "draft", f"A provisional draft fact. {MK_DRAFT}")
    # 回填用：两条退役 + 两条现役，共享一个 marker。
    _render(_kb_path("topics/backfill-old-1"), "topics/backfill-old-1",
            "Backfill retired one", "superseded", f"{MK_BACKFILL} retired copy one.")
    _render(_kb_path("topics/backfill-old-2"), "topics/backfill-old-2",
            "Backfill retired two", "archived", f"{MK_BACKFILL} retired copy two.")
    _render(_kb_path("topics/backfill-live-1"), "topics/backfill-live-1",
            "Backfill live one", "current", f"{MK_BACKFILL} live copy one.")
    _render(_kb_path("topics/backfill-live-2"), "topics/backfill-live-2",
            "Backfill live two", None, f"{MK_BACKFILL} live copy two.")
    # 只读语料：**没有 frontmatter / 没有 status**（真实只读文档的常态）。
    os.makedirs(DOCS, exist_ok=True)
    with open(os.path.join(DOCS, "notes.md"), "w", encoding="utf-8") as handle:
        handle.write("# Notes\n\nA read-only project note without any frontmatter. "
                     f"{MK_READONLY}\n")
    for args in (("init", "-q"), ("config", "user.email", "t@example.com"),
                 ("config", "user.name", "t"), ("add", "-A"),
                 ("commit", "-q", "-m", "seed")):
        subprocess.run(["git", "-C", KB, *args], capture_output=True)


STUB_DAEMON_SOURCE = '''"""Test-only daemon: deterministic stub embedder (no BGE-M3, no network)."""
import hashlib

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

raise SystemExit(main(__import__("sys").argv[1:]))
'''


def _write_stub_daemon() -> None:
    with open(WRAPPER, "w", encoding="utf-8") as handle:
        handle.write(STUB_DAEMON_SOURCE)


def _daemon_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = _ROOT + os.pathsep + env.get("PYTHONPATH", "")
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

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


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


# --------------------------------------------------- MCP 客户端（每调用 = 新会话）

async def _call_stdio(tool: str, args: dict, timeout: float | None = None):
    """经 `proxy.py` 连共享 daemon 的 stdio 客户端——与 opencode 的真实路径一致。

    每次调用都新建 stdio 连接（= 一个新 MCP 会话）；dogfood 的「新会话召回」即由此保证。
    """
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


async def invoke(tool: str, args: dict | None = None, timeout: float | None = None):
    result = await _call_stdio(tool, args or {}, timeout)
    if getattr(result, "is_error", False):
        raise RuntimeError(f"{tool} tool error: {_text(result)}")
    return _decode(result)


def call(tool: str, **args):
    return asyncio.run(invoke(tool, args))


def fresh_session_search(query: str, **args):
    """新会话（新 stdio 连接）里跑一次 `memory_search`。"""
    return call("memory_search", query=query, **args)


def build_index() -> dict:
    result = call("memory_reindex", cursor=None, batch=4, timeout=900)
    while not result.get("done"):
        result = call("memory_reindex", cursor=result["cursor"], batch=4, timeout=900)
    return result


# --------------------------------------------------------------------- 主流程

def _ids(hits) -> list:
    return [hit["id"] for hit in hits]


def _git_count() -> int:
    proc = subprocess.run(["git", "-C", KB, "rev-list", "--count", "HEAD"],
                          capture_output=True, text=True)
    return int(proc.stdout.strip() or 0)


def _git_clean() -> bool:
    proc = subprocess.run(["git", "-C", KB, "status", "--porcelain"],
                          capture_output=True, text=True)
    return proc.stdout.strip() == ""


def run() -> None:
    # ---------------- A. 确定性：exclude_retired 行为 + 工具契约 -------------
    note("\n[A] 确定性：exclude_retired（真 MCP 经 daemon）")

    default_hits = fresh_session_search(MK_SUPERSEDED, k=5)
    check("A1 默认 False：superseded 条目照常可见（不静默改行为）",
          any(h["id"] == "topics/retired-superseded" for h in default_hits),
          f"ids={_ids(default_hits)}")

    filtered = fresh_session_search(MK_SUPERSEDED, k=5, exclude_retired=True)
    check("A2a exclude_retired=True：superseded 被排除",
          all(h["id"] != "topics/retired-superseded" for h in filtered),
          f"ids={_ids(filtered)}")

    filtered_arch = fresh_session_search(MK_ARCHIVED, k=5, exclude_retired=True)
    check("A2b exclude_retired=True：archived 被排除",
          all(h["id"] != "topics/retired-archived" for h in filtered_arch),
          f"ids={_ids(filtered_arch)}")

    live = fresh_session_search(MK_CURRENT, k=5, exclude_retired=True)
    draft = fresh_session_search(MK_DRAFT, k=5, exclude_retired=True)
    check("A3 exclude_retired=True：current / draft 保留",
          any(h["id"] == "topics/live-current" for h in live)
          and any(h["id"] == "topics/live-draft" for h in draft),
          f"current={_ids(live)} draft={_ids(draft)}")

    readonly = fresh_session_search(MK_READONLY, k=5, exclude_retired=True)
    ro = next((h for h in readonly if h["id"] == "repo:docs/notes.md"), None) \
        if any(h["id"] == "repo:docs/notes.md" for h in readonly) else None
    if ro is None:  # 只读条目 id 由 loader 决定，兜底按 source 找
        ro = next((h for h in readonly if h["source"] == "docs/notes.md"), None)
    check("A4 无 status 的只读语料不被误伤（排除已退役 ≠ 只要 current）",
          ro is not None and ro.get("status") is None and ro.get("writable") is False,
          f"hit={ro}")

    contract_ok = all("owner" in h and "status" in h for h in (default_hits or [{}]))
    check("A5 命中契约：带 owner / status 字段",
          contract_ok, f"keys={sorted((default_hits or [{}])[0].keys())}")

    backfill = fresh_session_search(MK_BACKFILL, k=2, exclude_retired=True)
    check("A6 退役项占前排时仍回填够 k 条（多取召回池再筛）",
          len(backfill) == 2 and all(h["status"] not in ("superseded", "archived")
                                     for h in backfill),
          f"ids={_ids(backfill)} status={[h['status'] for h in backfill]}")

    # ---------------- B. dogfood：存 -> 新会话召回 -> supersede -> 取新弃旧 ----
    note("\n[B] dogfood：存 -> 新会话召回 -> supersede -> 取新弃旧")

    commits_before = _git_count()
    added = call("memory_add",
                 title=f"Agent loop dogfood fact {MK_DOGFOOD}",
                 body=f"A durable fact written by the agent loop dogfood run. {MK_DOGFOOD}",
                 section="topics", type="topic", tags=["demo"],
                 slug="agent-loop-dogfood-fact")
    old_id = added.get("id")
    check("B1 memory_add 落盘（真 git commit）",
          added.get("status") == "written" and bool(old_id)
          and _git_count() == commits_before + 1,
          f"id={old_id} commit={str(added.get('commit'))[:10]}")

    recalled = fresh_session_search(MK_DOGFOOD, k=5, exclude_retired=True)
    check("B2 新会话召回得到该事实",
          any(h["id"] == old_id for h in recalled), f"ids={_ids(recalled)}")

    old_path = call("memory_get", entry_id=old_id)["path"]
    files_before = (open(old_path, encoding="utf-8").read(), _git_count())
    preview = call("memory_supersede", old_id=old_id,
                   title=f"Agent loop dogfood fact v2 {MK_DOGFOOD}",
                   body=f"Updated durable fact; supersedes the first copy. {MK_DOGFOOD}",
                   section="topics", type="topic", tags=["demo"],
                   slug="agent-loop-dogfood-fact-v2")
    check("B3a supersede 默认只预览、不落盘（confirmation_required）",
          preview.get("status") == "confirmation_required"
          and preview.get("written") is False,
          f"status={preview.get('status')}")
    check("B3b 预览后文件与 git 未动（没有偷偷写）",
          open(old_path, encoding="utf-8").read() == files_before[0]
          and _git_count() == files_before[1])

    written = call("memory_supersede", old_id=old_id,
                   title=f"Agent loop dogfood fact v2 {MK_DOGFOOD}",
                   body=f"Updated durable fact; supersedes the first copy. {MK_DOGFOOD}",
                   section="topics", type="topic", tags=["demo"],
                   slug="agent-loop-dogfood-fact-v2", confirm=True)
    new_id = written.get("new_id")
    old_text = open(old_path, encoding="utf-8").read()
    new_text = open(written["path"], encoding="utf-8").read()
    check("B4 supersede 落盘：新旧双向标注 + 一个 commit + KB 干净",
          written.get("written") is True
          and f"superseded_by: {new_id}" in old_text.replace("\r\n", "\n")
          and f"supersedes: {old_id}" in new_text.replace("\r\n", "\n")
          and _git_count() == files_before[1] + 1 and _git_clean(),
          f"new_id={new_id} commits+1={_git_count() == files_before[1] + 1} clean={_git_clean()}")

    after = fresh_session_search(MK_DOGFOOD, k=5, exclude_retired=True)
    check("B5 新会话 exclude_retired=True：只召回新条目（取新弃旧）",
          any(h["id"] == new_id for h in after)
          and all(h["id"] != old_id for h in after),
          f"ids={_ids(after)}")

    after_default = fresh_session_search(MK_DOGFOOD, k=5)
    check("B6 新会话默认召回：新旧都在（证明差异来自开关，而非写入）",
          any(h["id"] == new_id for h in after_default)
          and any(h["id"] == old_id for h in after_default),
          f"ids={_ids(after_default)}")


def main() -> int:
    global PORT
    PORT = _free_port()
    note(f"# issue #42 场景评测 @ {time.strftime('%Y-%m-%d %H:%M:%S')}")
    note(f"    沙箱 = {WORK}  daemon port = {PORT}")
    setup_workspace()

    note("\n[0] 启动临时 daemon（Stub 嵌入，确定性）并重建索引")
    proc = start_daemon()
    note(f"    daemon pid={proc.pid}")
    try:
        built = build_index()
        check("0 索引重建完成且自洽",
              built.get("done") is True and built.get("entries", 0) >= 9,
              f"entries={built.get('entries')} gen={built.get('gen')}")
        run()
    except Exception as exc:  # noqa: BLE001 - 评测脚本要如实汇报
        import traceback
        traceback.print_exc()
        check("套件未抛异常", False, repr(exc))
    finally:
        _kill_tree(proc.pid)
        time.sleep(1.0)

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    verdict = "PASS" if passed == total else "FAIL"
    note(f"\n结论：{verdict}  ({passed}/{total})")
    note("（临时 daemon 已停；真实 daemon / 真实全局 KB 未被打扰）")
    write_results(verdict, passed, total)
    return 0 if passed == total else 1


def write_results(verdict: str, passed: int, total: int) -> None:
    lines = [
        "# #42 agent loop 场景评测结果（skill 化读/写/冲突裁决 + exclude_retired）",
        "",
        f"- 日期：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "- 脚本：`memory_agent/eval/agent_loop_42.py`",
        f"- 结论：**{verdict}**（{passed}/{total}）",
        f"- 临时工作区：`{WORK}`（临时 KB / 只读语料 / 索引 / daemon；真实全局 KB 未动）",
        "- 口径：真 MCP 经共享 daemon（`proxy.py`）；daemon 注入确定性 Stub 嵌入，",
        "  **不调 LLM 判分**，只断言外部行为（文件 / git / 命中）。",
        "",
        "## PASS 矩阵",
        "",
        "| # | 检查 | 结果 | 详情 |",
        "|---|------|------|------|",
    ]
    for index, (name, ok, detail) in enumerate(CHECKS, start=1):
        safe_detail = str(detail).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {index} | {name} | {'PASS' if ok else 'FAIL'} | {safe_detail} |")
    lines += ["", "## 运行日志", "", "```text", *RESULTS, "```", ""]
    with open(RESULTS_FILE, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    print(f"[结果] 已写入 {RESULTS_FILE}")


if __name__ == "__main__":
    raise SystemExit(main())
