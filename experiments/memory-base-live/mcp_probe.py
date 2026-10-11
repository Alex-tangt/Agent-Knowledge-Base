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


def failed(response) -> bool:
    return response.is_error if hasattr(response, "is_error") else response.isError


def payload(response, tool: str) -> dict:
    assert not failed(response), f"MCP {tool} failed"
    result = response.structured_content if hasattr(response, "structured_content") else response.structuredContent
    if result is None:
        result = json.loads(next(block.text for block in response.content if block.type == "text"))
    assert isinstance(result, dict), f"MCP {tool} returned an invalid payload"
    return result


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
            assert names == ["memory_add", "memory_get", "memory_list", "memory_search"]
            assert all(tool.annotations is not None for tool in listed.tools)
            annotations = {tool.name: tool.annotations.model_dump(by_alias=True, exclude_none=True)
                           for tool in listed.tools}
            for name, hints in annotations.items():
                assert hints == {"readOnlyHint": name != "memory_add", "destructiveHint": False,
                                 "idempotentHint": True, "openWorldHint": False}
            result = payload(await client.call_tool("memory_get", {
                "uri": args.uri, "expected_revision": args.expected_hash,
            }), "read")
            assert result["uri"] == args.uri
            assert result["revision"] == args.expected_hash, "MCP revision did not match"
            report = {"profile": args.profile, "tools": names, "uri": args.uri,
                      "content_hash": hashlib.sha256(result["content"].encode()).hexdigest(),
                      "annotations": annotations, "mcp_initialize": True,
                      "mcp_read": True, "mcp_expected_revision": True}
            assert report["content_hash"] == args.expected_hash, "MCP did not read the expected summary"
            cursor, cursors, originals, pages = None, set(), {}, 0
            while True:
                page = payload(await client.call_tool("memory_list", {
                    "scope": "captures", "limit": 1, "cursor": cursor,
                }), "list")
                assert page["completeness"]["enumeration"] == "complete", "MCP list was incomplete"
                pages += 1
                for item in page["items"]:
                    assert item["uri"] not in originals, "MCP list repeated an original"
                    originals[item["uri"]] = item["revision"]
                cursor = page["next_cursor"]
                if cursor is None:
                    assert len(originals) == page["completeness"]["total"], "MCP list lost originals"
                    break
                assert page["items"] and cursor not in cursors, "MCP list did not advance"
                cursors.add(cursor)
            assert originals.get(args.uri) == args.expected_hash, "MCP list missed the expected original"
            report.update({"mcp_list": True, "mcp_list_paginated": pages > 1,
                           "mcp_list_contains_original": True})
            if args.query is not None:
                search = payload(await client.call_tool("memory_search", {
                    "query": args.query, "scope": "captures", "limit": 20,
                }), "search")
                assert any(item["uri"] == args.uri for item in search["items"]), "MCP search missed the original"
                report.update({"mcp_search": True, "mcp_search_contains_original": True,
                               "mcp_search_existing_local_embedding": True,
                               "summary_generation_called": False})
            if args.profile == "reader":
                denied = await client.call_tool("memory_add", {
                    "request_id": "mcp-reader-acceptance", "content": "must not be stored",
                    "title": "Read-only acceptance", "kind": "note",
                })
                assert failed(denied), "MCP reader was allowed to write"
                assert any("WRITE_FORBIDDEN" in block.text for block in denied.content
                           if block.type == "text"), "MCP reader denial did not come from write authorization"
                report["mcp_reader_write_refused"] = True
            return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="reader")
    parser.add_argument("--home", type=Path, default=Path.home() / ".agent-memory-base")
    parser.add_argument("--uri", required=True)
    parser.add_argument("--expected-hash", required=True)
    parser.add_argument("--query", help="Optionally search captures using the existing local embedder; no summary generation")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(probe(args))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
