"""Exercise only the R2 home, its native services and newly scoped vault keys."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request

from memory_base import deploy
from memory_base.credentials import get_secret


def clean_env():
    return {key: value for key, value in os.environ.items()
            if not key.upper().startswith("MEMORY_") and key.upper() not in {"PYTHONPATH", "PYTHONHOME"}}


async def mcp(home, gateway, uri, digest, log):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    parameters = StdioServerParameters(command=sys.executable, args=[
        "-m", "memory_base.mcp_stdio", "--profile", "reader", "--home", str(home),
        "--url", gateway], env=clean_env(), cwd=os.environ["TEMP"])
    with log.open("w", encoding="utf-8") as err:
        async with stdio_client(parameters, errlog=err) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                listed = await client.list_tools()
                names = sorted(tool.name for tool in listed.tools)
                assert names == ["memory_add", "memory_get", "memory_list", "memory_search"]
                assert all(tool.inputSchema.get("additionalProperties") is False for tool in listed.tools)
                response = await client.call_tool("memory_get", {"uri": uri, "expected_revision": digest})
                assert not response.isError, "Native MCP read failed"
                result = response.structuredContent or json.loads(next(block.text for block in response.content
                                                                         if block.type == "text"))
                assert hashlib.sha256(result["content"].encode()).hexdigest() == digest
                response = await client.call_tool("memory_list", {"scope": "captures", "limit": 1})
                assert not response.isError, "Native MCP list failed"
                result = response.structuredContent or json.loads(next(block.text for block in response.content
                                                                         if block.type == "text"))
                assert any(item["uri"] == uri and item["revision"] == digest for item in result["items"])
    return {"tools": names, "initialized": True, "schema_closed": True,
            "get_hash_matches": True, "list_contains_original": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    args = parser.parse_args()
    home, store = args.home.resolve(), args.store.resolve()
    assert home.is_relative_to(store), "Native probe home escaped this run's store"
    config = deploy.load_config(home)
    assert Path(config["python"]).resolve() == Path(sys.executable).resolve()
    cli = Path(sys.executable).parent / "memory-base.exe"
    report = {"home": str(home), "ports": config["ports"], "checks": {},
              "boundary": "New isolated native OpenViking; only synthetic fixture and local GGUF embedding. No LLM generation."}
    records = {}

    def save():
        (store / "native.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def manager(command):
        output = subprocess.run([str(cli), command, "--home", str(home), "--timeout", "300"],
                                cwd=os.environ["TEMP"], env=clean_env(), capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        (store / ("native-" + command + ".log")).write_text(output.stdout + output.stderr, encoding="utf-8")
        report[command + "_exit_code"] = output.returncode
        save()
        assert output.returncode == 0, "Native manager " + command + " failed; see preserved log"
        return json.loads(output.stdout)

    def request(route, body, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(gateway + "/api/memory/" + route,
                                     data=json.dumps(body, ensure_ascii=False).encode(), headers=headers, method="POST")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=180) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    gateway = f"http://127.0.0.1:{config['ports']['gateway']}"
    try:
        status = manager("start")
        assert status["status"] == "ready"
        records = deploy._records(home)
        assert set(records) == set(deploy.COMPONENTS)
        assert all(deploy.owned_component(component, home, config, record) for component, record in records.items())
        (store / "processes-started.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        report["checks"]["three_owned_native_services_ready"] = True
        writer, reader = get_secret("host-codex", home), get_secret("host-reader", home)
        assert writer and reader, "New home credentials were not provisioned"
        body = {"request_id": "wheel-r2-native-2026-10-12", "title": "独立 wheel R2 原生验收记录",
                "content": "# 独立 wheel 验收\r\n\r\n  合成 Unicode 原文，保留缩进。\r\n第二行。\n",
                "kind": "note", "observed_on": "2026-10-12"}
        code, _ = request("get", {"uri": "viking://resources/captures/" + "0" * 64 + "/content.md"})
        assert code == 401, "Anonymous request was not refused"
        code, denied = request("add", body, reader)
        assert code == 403 and denied["error"]["code"] == "WRITE_FORBIDDEN"
        code, result = request("add", body, writer)
        assert code == 200 and result["status"] == "created", "Native append failed"
        digest = hashlib.sha256(body["content"].encode()).hexdigest()
        uri = result["uri"]
        assert result["content_hash"] == digest
        report.update({"uri": uri, "content_hash": digest, "index": result["index"]})
        code, result = request("get", {"uri": uri, "expected_revision": digest}, reader)
        assert code == 200 and result["content"] == body["content"] and result["revision"] == digest
        code, result = request("list", {"scope": "captures", "limit": 1}, reader)
        assert code == 200 and result["completeness"]["total"] == 1
        assert any(item["uri"] == uri and item["revision"] == digest for item in result["items"])
        code, result = request("search", {"query": body["title"], "scope": "captures", "limit": 20}, reader)
        assert code == 200 and any(item["uri"] == uri for item in result["items"]), "Native search missed synthetic note"
        report["checks"].update({"anonymous_401": True, "reader_403": True, "synthetic_unicode_crlf_append": True,
                                 "reader_exact_hash_revision": True, "native_list": True, "native_search": True})
        report["mcp"] = asyncio.run(mcp(home, gateway, uri, digest, store / "native-mcp.stderr.log"))
        report["checks"]["native_stdio_four_tools_get_list"] = True
        report["overall"] = "PASS"
    except Exception as error:
        report["overall"] = "FAIL"
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        # Stop only this manager's exact owned process records, never by name.
        try:
            manager("stop")
            assert all(not deploy.owned_process(record) for record in records.values())
            assert all(not deploy._port_busy(port) for port in config["ports"].values())
            report["checks"]["owned_process_cleanup_no_residual"] = True
        except Exception as error:
            report["overall"] = "FAIL"
            report["cleanup_failure"] = {"type": type(error).__name__, "message": str(error)}
        save()
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["overall"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
