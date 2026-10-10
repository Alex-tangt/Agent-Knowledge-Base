"""Exercise the installed stdio MCP transport without printing credentials."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def probe(args) -> dict:
    parameters = StdioServerParameters(command=sys.executable, args=[
        "-m", "memory_base.mcp_stdio", "--profile", args.profile,
        "--home", str(args.home),
    ])
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            listed = await client.list_tools()
            names = sorted(tool.name for tool in listed.tools)
            assert names == ["memory_add", "memory_get", "memory_search"]
            annotations = {tool.name: tool.annotations.model_dump(by_alias=True, exclude_none=True)
                           for tool in listed.tools}
            for name, hints in annotations.items():
                assert hints == {"readOnlyHint": name != "memory_add", "destructiveHint": False,
                                 "idempotentHint": True, "openWorldHint": False}
            response = await client.call_tool("memory_get", {"uri": args.uri})
            assert not response.isError, "MCP read failed"
            result = response.structuredContent
            if not result:
                result = json.loads(next(block.text for block in response.content if block.type == "text"))
            assert result["uri"] == args.uri
            report = {"profile": args.profile, "tools": names, "uri": args.uri,
                      "content_hash": hashlib.sha256(result["content"].encode()).hexdigest(),
                      "annotations": annotations, "mcp_read": True}
            assert report["content_hash"] == args.expected_hash, "MCP did not read the expected summary"
            if args.profile == "reader":
                denied = await client.call_tool("memory_add", {
                    "request_id": "mcp-reader-acceptance", "content": "must not be stored",
                    "title": "Read-only acceptance", "kind": "note",
                })
                assert denied.isError, "MCP reader was allowed to write"
                report["mcp_reader_write_refused"] = True
            return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="reader")
    parser.add_argument("--home", type=Path, default=Path.home() / ".agent-memory-base")
    parser.add_argument("--uri", required=True)
    parser.add_argument("--expected-hash", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(probe(args))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
