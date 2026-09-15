"""memory-agent MCP server：读路径最小闭环（issue #10）+ 写路径（#11/#12）+ 索引维护（#13）。

暴露 memory_search / memory_get / memory_add / memory_supersede / memory_archive /
memory_reindex / memory_index_status。真相源是 Markdown；索引是派生物。破坏性变更
（supersede / archive）需 confirm=true。

两种传输（issue #19）：
- `stdio`（默认）：每个 opencode 会话各拉起一份进程——简单，但每份各加载一份嵌入模型。
- `http`：`--transport http --host 127.0.0.1 --port 8765` 起一个常驻单实例，多个会话经
  `proxy.py`（stdio↔HTTP 转发）共享同一份模型（约 3.9GB 只付一次）。HTTP 模式默认
  eager 预热（常驻才有意义），并用 DNS-rebinding 防护把 Host 限到本机。
"""
from __future__ import annotations

import argparse
import atexit
import os
import sys
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _HERE]
sys.path.insert(0, _ROOT)

from memory_agent.runtime import get_index, get_writer, reindex  # noqa: E402  (导入即配 stderr 日志 + ragcore 路径)
from memory_agent.memory.errors import IndexConsistencyError  # noqa: E402
from memory_agent.memory.writer import MemoryWriteError  # noqa: E402
from memory_agent.settings import (  # noqa: E402
    MCP_HTTP_HOST,
    MCP_HTTP_PATH,
    MCP_HTTP_PORT,
    daemon_pid_path,
    warmup_on_start,
)

from mcp.server import MCPServer  # noqa: E402
# 工具里的「预期失败」必须 raise ToolError，不能 raise ValueError：后者被 SDK 当崩溃，
# 客户端只看到 `Error executing tool <name>`，写入网关那些面向调用方的提示（去重候选 /
# 校验失败 / 只读拒写）全被吞掉。#17 验收时实测并修正。
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from utils.logger import logger  # noqa: E402

DEFAULT_HOST = MCP_HTTP_HOST
DEFAULT_PORT = MCP_HTTP_PORT
DEFAULT_PATH = MCP_HTTP_PATH

mcp = MCPServer(
    "memory-agent",
    version="0.1.0",
    instructions=(
        "Agent 长期记忆：memory_search 语义检索条目，memory_get 读回真实 Markdown，"
        "memory_add 写入新条目（先去重，命中近似则不写并返回候选）；"
        "memory_supersede 替代旧条目（新旧双向标注），memory_archive 只标记退役、不删文件。"
        "supersede / archive 是破坏性变更，先看 preview，再以 confirm=true 重试。"
        "写入会自动增量刷新索引；索引不自洽时用 memory_reindex 分块全量重建"
        "（拿 cursor 续调到 done=true），memory_index_status 查当前代与自洽性。"
        "只读语料（writable=false）不可写入；没有裸文件写工具。"
    ),
)


@mcp.custom_route("/health", methods=["GET"])
async def _health(_request):
    """就绪/存活探测：HTTP 模式下 proxy 与运维脚本用它判断 daemon 是否在跑。"""
    return JSONResponse({"status": "ok", "service": "memory-agent"})


@mcp.tool()
def memory_search(query: str, k: int = 5, writable_only: bool = False) -> list[dict]:
    """语义检索长期记忆条目（可写 KB 记忆 + 只读项目语料）。

    返回条目级命中：id / title / source / writable / type / tags / status / score / snippet。
    score 越大越相关：检索链路是向量 + 关键词混合召回（关键词命中批次分数 >1，
    其余为余弦相似度 ∈[-1,1]）。用 memory_get(id) 读回完整 Markdown。
    writable=false 的是只读参考语料，不可写入。
    """
    return get_index().search(query, k=k, writable_only=writable_only)


@mcp.tool()
def memory_get(entry_id: str) -> dict:
    """按 memory_search 返回的 id，读回该条目的真实 Markdown 内容与元数据。"""
    try:
        return get_index().get(entry_id)
    except KeyError:
        raise ToolError(f"未知条目 id：{entry_id}（先用 memory_search 取 id）")
    except FileNotFoundError as exc:
        raise ToolError(f"条目文件已不存在（索引孤儿，需重建）：{exc}")


@mcp.tool()
def memory_add(
    title: str,
    body: str,
    domain: str,
    type: str,
    tags: list[str],
    slug: str | None = None,
    sources: list[str] | None = None,
    status: str = "current",
    allow_duplicate: bool = False,
) -> dict:
    """新增一条长期记忆（唯一写入口：不覆盖、不删除、不原地编辑已有条目）。

    写入前先去重——命中近似条目时**不写**，返回候选 id/score，交由调用方决定
    （确认不同则 allow_duplicate=true 重试；要替换则用 memory_supersede, #12）。
    写入 = frontmatter 过 KB 校验 + 一个新 git commit（只含本条目文件）。

    - domain: topics | decisions | projects/<slug>
    - type: topic | decision | research | project-knowledge（须与 domain 匹配）
    - tags: 取 tags.md 的受控标签
    - slug: 英文 slug；省略时从 title 派生（纯中文标题请显式给）
    """
    try:
        return get_writer().add(
            title=title, body=body, domain=domain, type=type, tags=tags,
            slug=slug, sources=sources, status=status,
            allow_duplicate=allow_duplicate,
        )
    except MemoryWriteError as exc:
        raise ToolError(str(exc))


@mcp.tool()
def memory_supersede(
    old_id: str,
    title: str,
    body: str,
    domain: str,
    type: str,
    tags: list[str],
    slug: str | None = None,
    sources: list[str] | None = None,
    confirm: bool = False,
) -> dict:
    """用新条目替代一条旧记忆（破坏性：旧条目被标注退役，但**文件保留**）。

    confirm=False（默认）不落盘，只返回 `{status:"confirmation_required", preview}`；
    调用方必须先把它给用户看、得到明确同意，再以 confirm=True 重试。
    落盘 = 新条目（frontmatter 带 supersedes=<old_id>）+ 旧条目改
    status: superseded / superseded_by=<new_id>，两个文件在同一个 commit 里。
    """
    try:
        return get_writer().supersede(
            old_id=old_id, title=title, body=body, domain=domain, type=type,
            tags=tags, slug=slug, sources=sources, confirm=confirm,
        )
    except MemoryWriteError as exc:
        raise ToolError(str(exc))


@mcp.tool()
def memory_archive(entry_id: str, reason: str, confirm: bool = False) -> dict:
    """把一条记忆标记退役（破坏性：改 status 为 archived，**文件永不删除**）。

    `reason` 会写进 frontmatter 的 archive_reason 字段（归档必须留下为什么）。
    confirm=False（默认）不落盘，只返回 `{status:"confirmation_required", preview}`；
    得到用户明确同意后，再以 confirm=True 重试。
    """
    try:
        return get_writer().archive(entry_id=entry_id, reason=reason, confirm=confirm)
    except MemoryWriteError as exc:
        raise ToolError(str(exc))


@mcp.tool()
def memory_reindex(cursor: dict | None = None, batch: int = 16) -> dict:
    """分块全量重建派生索引（从 Markdown 真相源恢复；新代 + 原子切指针）。

    单次调用只嵌入 batch 条（默认 16），避免超过 MCP 调用超时。`cursor=None` 开始
    新一轮，返回 `{done,total,processed,cursor,gen}`；拿返回的 `cursor` 原样续调，
    直到 `done=true`（此时指针已切换，新索引生效）。
    中断安全：完成前指针不动，旧索引继续服务；核对 manifest 条数 == 点数，不等则报错。
    """
    try:
        return reindex(cursor=cursor, batch=batch)
    except IndexConsistencyError as exc:
        raise ToolError(str(exc))


@mcp.tool()
def memory_index_status() -> dict:
    """查当前索引代：`{built, gen, entries, points, consistent, built_at, path}`。

    `consistent=false` 表示 manifest 条数 ≠ 集合点数（需 memory_reindex 重建）。
    """
    return get_index().status()


def _warmup() -> None:
    try:
        index = get_index()
        index.store.warmup()
        logger.info("memory index warmup done")
    except Exception as exc:  # noqa: BLE001 - 预热失败不阻断服务
        logger.warning(f"memory index warmup failed: {exc}")


def _start_warmup(*, enabled: bool) -> bool:
    """按需启动预热线程，返回是否真的启动了。

    默认不启动（issue #19）：BGE-M3 约 3.9GB 私有内存，而且每个 opencode 会话都会拉起
    一份 MCP——无条件预热 = 每会话白付 3.9GB。改为首次真正检索时才加载。

    但 **HTTP 常驻单实例**例外：全局只有一份，eager 预热才不冷启动（由 `--warmup` /
    `--transport http` 决定，见 `_resolve_warmup`）。
    """
    if not enabled:
        logger.info("skip warmup：惰性加载，首次检索时才载入嵌入模型（#19）")
        return False
    threading.Thread(target=_warmup, name="memory-warmup", daemon=True).start()
    return True


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="memory-agent MCP server")
    parser.add_argument(
        "--transport", choices=["stdio", "http"], default="stdio",
        help="stdio（默认，每会话一进程）或 http（常驻单实例，供 proxy 转发）",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="HTTP 绑定地址（默认仅本机）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--path", default=DEFAULT_PATH, help="streamable-http 端点路径")
    warm = parser.add_mutually_exclusive_group()
    warm.add_argument("--warmup", dest="warmup", action="store_true", default=None,
                      help="启动即加载嵌入模型")
    warm.add_argument("--no-warmup", dest="warmup", action="store_false", default=None,
                      help="惰性加载（首次检索才载入）")
    return parser


def _resolve_warmup(args: argparse.Namespace) -> bool:
    """预热决策：显式 flag > HTTP 模式默认 eager > 环境变量（stdio 默认惰性）。"""
    if args.warmup is not None:
        return args.warmup
    if args.transport == "http":
        return True
    return warmup_on_start()


def _transport_security(host: str, port: int) -> TransportSecuritySettings:
    """把 Host 限到本机，挡浏览器 DNS rebinding（SDK 该路径默认关闭该防护）。"""
    allowed = [f"{host}:{port}"]
    for loopback in ("127.0.0.1", "localhost"):
        entry = f"{loopback}:{port}"
        if entry not in allowed:
            allowed.append(entry)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=allowed,
    )


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    _start_warmup(enabled=_resolve_warmup(args))

    if args.transport == "http":
        logger.info(
            f"memory-agent HTTP daemon: http://{args.host}:{args.port}{args.path} "
            f"(health: /health, warmup: {_resolve_warmup(args)})"
        )
        _write_pid_file(args.port)
        mcp.run(
            transport="streamable-http",
            host=args.host,
            port=args.port,
            streamable_http_path=args.path,
            transport_security=_transport_security(args.host, args.port),
        )
        return

    mcp.run(transport="stdio")


def _write_pid_file(port: int) -> None:
    """记下 daemon PID，供 `proxy.py --stop` 手动停止（不搞自动空闲卸载）。"""
    path = daemon_pid_path(port)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        atexit.register(_remove_pid_file, path)
    except OSError as exc:  # noqa: BLE001 - PID 文件失败不该挡住服务
        logger.warning(f"无法写 daemon PID 文件 {path}: {exc}")


def _remove_pid_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


if __name__ == "__main__":
    main()
