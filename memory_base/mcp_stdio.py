"""MCP tools forwarding to the running, authenticated memory gateway."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Literal

from mcp.types import ToolAnnotations

try:
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # The pinned OpenViking environment uses the MCP 1.x SDK.
    from mcp.server.fastmcp import FastMCP as MCPServer
    from mcp.server.fastmcp.exceptions import ToolError


_TOOL_ARGUMENTS = {
    "memory_add": {"request_id", "content", "title", "kind", "source_url", "source_id",
                   "conversation_id", "observed_on", "scope"},
    "memory_search": {"query", "limit", "scope"},
    "memory_get": {"uri", "expected_revision", "scope"},
    "memory_list": {"scope", "limit", "cursor"},
}


class MemoryMCPServer(MCPServer):
    async def list_tools(self):
        tools = await super().list_tools()
        for tool in tools:
            schema = tool.input_schema if hasattr(tool, "input_schema") else tool.inputSchema
            schema["additionalProperties"] = False
        return tools

    async def call_tool(self, name, arguments, *args, **kwargs):
        # Both supported SDK generations otherwise discard unknown keys before
        # invoking functions. Reject the original argument map at the boundary.
        fields = _TOOL_ARGUMENTS.get(name)
        if fields is not None and (not isinstance(arguments, dict) or set(arguments) - fields):
            raise ToolError("UNKNOWN_MEMORY_TOOL_ARGUMENT")
        return await super().call_tool(name, arguments, *args, **kwargs)


class GatewayClient:
    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self._token = token
        if not token:
            raise ValueError("Gateway host credential is required")

    def call(self, tool: str, arguments: dict) -> dict:
        request = urllib.request.Request(
            self.url + "/api/memory/" + tool,
            data=json.dumps(arguments, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + self._token, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=165) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Tool errors must be explicit, and never echo headers or credentials.
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                code = payload.get("error", {}).get("code") or "GATEWAY_REQUEST_REJECTED"
            except (ValueError, TypeError, AttributeError):
                code = "GATEWAY_REQUEST_REJECTED"
            raise ToolError(f"Memory gateway HTTP {exc.code}: {code}") from None
        except (OSError, ValueError, urllib.error.URLError):
            raise ToolError("Memory gateway unavailable; stored memory has not been searched") from None


def create_mcp(client: GatewayClient) -> MCPServer:
    server = MemoryMCPServer("personal-memory-base", instructions=(
        "Store original user-approved notes or article/video summaries with memory_add. "
        "Reuse the same request_id when retrying; altered content requires a new request_id. "
        "Search memory_search, then read the returned URI with memory_get. "
        "Use memory_list for complete original-file enumeration and path scopes. "
        "The default search scope is captures; projects/<name> queries live read-only Markdown. "
        "observed_on is the person's encounter date; leave it unset when unknown. "
        "Unavailable is an error, never evidence that no memory exists. "
        "This interface does not promote personal preferences to global preferences."
    ))

    @server.tool(annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False,
    ))
    def memory_add(request_id: str, content: str, title: str,
                   kind: Literal["note", "article_summary", "video_summary"] = "note",
                   source_url: str | None = None, source_id: str | None = None,
                   conversation_id: str | None = None, observed_on: str | None = None,
                   scope: str | None = None) -> dict[str, Any]:
        """Persist exact content, source trace and writer identity without rewriting it."""
        return client.call("add", {
            "request_id": request_id, "content": content, "title": title, "kind": kind,
            "source_url": source_url, "source_id": source_id, "conversation_id": conversation_id,
            "observed_on": observed_on, "scope": scope,
        })

    @server.tool(annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
    ))
    def memory_search(query: str, limit: int = 5, scope: str | None = None) -> dict[str, Any]:
        """Search captures or a live project path; methods and coverage are explicit."""
        return client.call("search", {"query": query, "limit": limit, "scope": scope})

    @server.tool(annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
    ))
    def memory_get(uri: str, expected_revision: str | None = None,
                   scope: str | None = None) -> dict[str, Any]:
        """Read an original capture/project; expected_revision rejects changed bytes."""
        return client.call("get", {"uri": uri, "expected_revision": expected_revision, "scope": scope})

    @server.tool(annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
    ))
    def memory_list(scope: str | None = None, limit: int = 100,
                    cursor: str | None = None) -> dict[str, Any]:
        """List authorized originals without vectors; follow next_cursor until null."""
        return client.call("list", {"scope": scope, "limit": limit, "cursor": cursor})

    return server


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    parser = argparse.ArgumentParser(description="Forward personal memory MCP tools to the shared gateway")
    parser.add_argument("--profile", default="codex")
    parser.add_argument("--home", type=Path, default=None)
    parser.add_argument("--url", default=os.environ.get("MEMORY_BASE_URL", "http://127.0.0.1:8788"))
    args = parser.parse_args(argv)
    token = os.environ.get("MEMORY_BASE_TOKEN")
    if not token:
        from memory_base.credentials import get_secret
        token = get_secret("host-" + args.profile, args.home)
    create_mcp(GatewayClient(args.url, token)).run(transport="stdio")


if __name__ == "__main__":
    main()
