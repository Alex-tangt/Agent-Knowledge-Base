"""stdio <-> HTTP MCP 代理：opencode 的 `local` 命令（issue #19）。

opencode 在 `type:"local"` 下每个会话都会运行本脚本并走 stdio 握手。本脚本：

1. **确保共享 daemon 在跑**：健康检查固定端点；不在就**后台拉起**（日志在子进程内重定向到
   文件，幂等——端口被占/已在跑不重复起），再轮询就绪。
2. 把本会话的 **stdio JSON-RPC 透明转发**到 daemon 的 streamable-HTTP 端点。

这样 N 个会话共享 daemon 里那一份嵌入模型（~3.9GB 只付一次），本脚本每会话仅几十 MB。

铁律：**stdout 是 stdio 协议通道**——所有诊断只写 stderr；不要 import ragcore（它的 logger
默认写 stdout）。所以这里只依赖 `mcp` 与轻量的 `memory_agent.settings`。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _HERE]
sys.path.insert(0, _ROOT)

from memory_agent.settings import (  # noqa: E402
    DAEMON_LOG,
    MCP_HTTP_HOST,
    MCP_HTTP_PATH,
    MCP_HTTP_PORT,
)

SERVER_SCRIPT = os.path.join(_HERE, "mcp_server.py")
DEFAULT_START_TIMEOUT = 30.0
DEFAULT_HEALTH_TIMEOUT = 1.0


def _log(message: str) -> None:
    print(f"[memory-agent proxy] {message}", file=sys.stderr, flush=True)


def _opener():
    """不走任何代理的 urllib opener（本机回环不该受 HTTP_PROXY 影响）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health_ok(host: str = MCP_HTTP_HOST, port: int = MCP_HTTP_PORT,
              timeout: float = DEFAULT_HEALTH_TIMEOUT) -> bool:
    try:
        with _opener().open(f"http://{host}:{port}/health", timeout=timeout) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001 - 连不上/超时/非 200 都视为未就绪
        return False


def _spawn_daemon(host: str, port: int, path: str, log_path: str) -> None:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    creationflags = 0
    for flag in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
        creationflags |= getattr(subprocess, flag, 0)
    command = [
        sys.executable, SERVER_SCRIPT,
        "--transport", "http", "--host", host, "--port", str(port), "--path", path,
    ]
    with open(log_path, "ab") as log:
        subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            cwd=_ROOT, creationflags=creationflags, close_fds=True,
        )


def ensure_daemon(host: str = MCP_HTTP_HOST, port: int = MCP_HTTP_PORT,
                  path: str = MCP_HTTP_PATH, timeout: float = DEFAULT_START_TIMEOUT,
                  log_path: str = DAEMON_LOG) -> bool:
    """幂等确保 daemon 在跑：已在跑直接返回 True；否则后台拉起并等就绪。"""
    if health_ok(host, port):
        _log(f"daemon already up at {host}:{port}")
        return True

    _log(f"starting daemon: {host}:{port} (log: {log_path})")
    _spawn_daemon(host, port, path, log_path)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if health_ok(host, port):
            _log("daemon ready")
            return True
        time.sleep(0.25)

    _log(f"daemon did not become ready within {timeout:.0f}s; see {log_path}")
    return False


async def _serve(host: str, port: int, path: str) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.shared._httpx_utils import create_mcp_http_client
    from mcp.types import CallToolResult, TextContent

    url = f"http://{host}:{port}{path}"
    http_client = create_mcp_http_client()
    try:  # 本机回环不走代理（否则 HTTP_PROXY 会把连接带去别处）
        http_client.trust_env = False
    except Exception:  # noqa: BLE001 - 老版本 httpx2 不支持就退回默认
        pass

    async with http_client:
        async with streamable_http_client(url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as upstream:
                await upstream.initialize()

                async def on_list_tools(_ctx, params):
                    return await upstream.list_tools(params=params)

                async def on_call_tool(_ctx, params):
                    try:
                        return await upstream.call_tool(params.name, params.arguments or {})
                    except Exception as exc:  # noqa: BLE001 - 转成清晰的工具错误
                        return CallToolResult(
                            content=[TextContent(
                                type="text",
                                text=f"memory-agent daemon 不可用：{exc}（检查 {DAEMON_LOG}）",
                            )],
                            is_error=True,
                        )

                server = Server(
                    "memory-agent", on_list_tools=on_list_tools, on_call_tool=on_call_tool,
                )
                async with stdio_server() as (srv_read, srv_write):
                    await server.run(
                        srv_read, srv_write, server.create_initialization_options()
                    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="memory-agent stdio<->HTTP proxy")
    parser.add_argument("--host", default=MCP_HTTP_HOST)
    parser.add_argument("--port", type=int, default=MCP_HTTP_PORT)
    parser.add_argument("--path", default=MCP_HTTP_PATH)
    parser.add_argument("--ensure", action="store_true",
                        help="只确保 daemon 在跑，不做转发（运维/测试用）")
    parser.add_argument("--status", action="store_true",
                        help="打印 daemon 健康状态后退出")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.status:
        ok = health_ok(args.host, args.port)
        print("ok" if ok else "down")
        return 0 if ok else 1

    if args.ensure:
        return 0 if ensure_daemon(args.host, args.port, args.path) else 1

    if not ensure_daemon(args.host, args.port, args.path):
        _log("拒绝在 daemon 不可用时转发（工具会全部失败）。")
        return 1

    import asyncio
    asyncio.run(_serve(args.host, args.port, args.path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
