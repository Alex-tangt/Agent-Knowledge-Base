"""`memory-agent` 统一 CLI 入口（#52 / ADR-0028）。

- `memory-agent install [...]` → 一键部署（`memory_agent.deploy`）。
- 无子命令 → 启动 MCP server（默认 stdio），保持既有行为向后兼容。

MCP 客户端（opencode）注册的是 `proxy.py`，不走本入口；这里只是人工运维便利。
"""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "install":
        from memory_agent.deploy import main as install_main
        return install_main(argv[1:])

    from memory_agent.mcp_server import main as server_main
    server_main(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
