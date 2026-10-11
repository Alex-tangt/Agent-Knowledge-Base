"""Probe only the installed wheel; launch from a repository-independent CWD."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout
import importlib
import importlib.metadata as metadata
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch


def identity() -> dict:
    return {"python": sys.version, "executable": sys.executable,
            "prefix": sys.prefix, "base_prefix": sys.base_prefix,
            "sitepackages": sysconfig.get_path("purelib"), "sys_path": sys.path,
            "cwd": str(Path.cwd()), "pythonpath": os.environ.get("PYTHONPATH"),
            "distribution": str(metadata.distribution("agent-memory-base")._path)}


def imports(expected_site: Path, modules: list[str]) -> dict:
    origins = {}
    for name in modules:
        spec = importlib.util.find_spec(name)
        assert spec and spec.origin
        origin = Path(spec.origin).resolve()
        assert origin.is_relative_to(expected_site), (name, str(origin))
        if name != "memory_base.__main__":
            module = importlib.import_module(name)
            assert Path(module.__file__).resolve() == origin
            mode = "imported"
        else:
            # Importing __main__ intentionally calls argparse and exits.
            with patch.object(sys, "argv", ["memory-base", "--help"]), redirect_stdout(io.StringIO()):
                try:
                    importlib.import_module(name)
                except SystemExit as error:
                    assert error.code == 0
                else:
                    raise AssertionError("__main__ did not execute its CLI")
            mode = "imported_with_help_and_expected_exit_0"
        origins[name] = {"file": str(origin), "mode": mode}
    blocked = [name for name in sys.modules
               if name.split(".")[0] in {"memory_agent", "ragcore", "legal_web", "experiments"}]
    assert not blocked, blocked
    providers = {}
    for name in ("fastapi", "pydantic", "mcp"):
        module = importlib.import_module(name)
        providers[name] = {"version": metadata.version(name), "file": module.__file__}
    return {"identity": identity(), "modules": origins,
            "loaded_excluded_namespaces": blocked, "dependency_providers": providers}


def install_boundary(store: Path, formal_python: Path) -> dict:
    from memory_base import deploy

    model = store / "fixture.gguf"
    model.write_bytes(b"GGUFpackage-acceptance-synthetic-fixture")
    observations = {}
    for name, chosen, fresh in (("same_interpreter", sys.executable, False),
                                ("other_existing_interpreter", str(formal_python), False),
                                ("fresh_runtime", None, True)):
        home = store / name
        calls = []

        def record(command, **kwargs):
            calls.append([str(item) for item in command])
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(deploy.subprocess, "run", record), \
                patch.object(deploy, "_versions", lambda python: dict(deploy.PINS)), \
                patch.object(deploy, "_ensure_secret", lambda secret, home: "synthetic-not-persisted"):
            try:
                config = deploy.install(home, chosen, str(model),
                                        ports={"upstream": 29861, "gateway": 29862, "embed": 29863},
                                        fresh_runtime=fresh)
                outcome = {"status": "configured_with_subprocess_and_credentials_stubbed",
                           "python": config["python"], "source": config["source"]}
            except RuntimeError as error:
                outcome = {"status": "rejected", "error": str(error)}
        assert all("-e" not in command for command in calls)
        assert any(command[1:5] == ["-m", "pip", "install", "--no-compile"] for command in calls)
        if name == "same_interpreter":
            assert outcome["status"].startswith("configured")
            assert Path(outcome["source"]) == Path(sysconfig.get_path("purelib"))
        else:
            assert outcome == {"status": "rejected", "error":
                               "Installing into a new runtime requires the source checkout; this package was installed as a wheel"}
        observations[name] = {"outcome": outcome, "subprocess_calls_not_executed": calls}
    return {"identity": identity(), "observations": observations,
            "boundary": "No pip, venv, model download or credential operations were executed by this probe."}


async def protocol(store: Path) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            authenticated = self.headers.get("Authorization") == "Bearer package-fixture-only"
            received.append({"path": self.path, "arguments": body,
                             "synthetic_auth_matched": authenticated})
            response = json.dumps({"status": "fixture", "route": self.path,
                                   "arguments": body}).encode()
            self.send_response(200 if authenticated else 401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    stub = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=stub.serve_forever, daemon=True)
    worker.start()
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("MEMORY_") and key.upper() not in {"PYTHONPATH", "PYTHONHOME"}}
    env.update({"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
                "MEMORY_BASE_TOKEN": "package-fixture-only", "MEMORY_BASE_HOME": str(store / "mcp-home")})
    params = StdioServerParameters(command=sys.executable,
                                  args=["-m", "memory_base.mcp_stdio", "--url",
                                        f"http://127.0.0.1:{stub.server_port}"], env=env, cwd=str(Path.cwd()))
    fixtures = {
        "memory_add": {"request_id": "package-86-fixture", "content": "Synthetic wheel acceptance note.",
                       "title": "Package fixture", "kind": "note", "scope": "captures"},
        "memory_search": {"query": "Synthetic wheel acceptance", "limit": 3, "scope": "captures"},
        "memory_get": {"uri": "viking://resources/captures/fixture.md", "scope": "captures"},
        "memory_list": {"scope": "captures", "limit": 10},
    }
    try:
        with (store / "mcp.stderr.log").open("w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    listing = await session.list_tools()
                    names = sorted(tool.name for tool in listing.tools)
                    assert names == sorted(fixtures), names
                    schemas = {tool.name: tool.inputSchema for tool in listing.tools}
                    assert all(schema.get("additionalProperties") is False for schema in schemas.values())
                    responses = {}
                    for name, arguments in fixtures.items():
                        response = await session.call_tool(name, arguments)
                        assert not response.isError, response
                        payload = json.loads(next(item.text for item in response.content if item.type == "text"))
                        route = "/api/memory/" + name.removeprefix("memory_")
                        assert payload["route"] == route
                        assert all(payload["arguments"][key] == value for key, value in arguments.items())
                        responses[name] = payload
        assert len(received) == 4 and all(item["synthetic_auth_matched"] for item in received)
        return {"identity": identity(), "server_info": initialized.serverInfo.model_dump(mode="json"),
                "protocol_version": initialized.protocolVersion, "tool_names": names,
                "tool_schemas": schemas, "calls": responses, "http_received": received,
                "boundary": "Real MCP stdio server and HTTP forwarding; synthetic HTTP responses, no OpenViking data or real credentials."}
    finally:
        stub.shutdown()
        stub.server_close()
        worker.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("imports", "install-boundary", "mcp"))
    parser.add_argument("--site", type=Path)
    parser.add_argument("--modules", nargs="*")
    parser.add_argument("--store", type=Path)
    parser.add_argument("--formal-python", type=Path)
    args = parser.parse_args()
    if args.mode == "imports":
        result = imports(args.site.resolve(), args.modules)
    elif args.mode == "install-boundary":
        result = install_boundary(args.store, args.formal_python)
    else:
        result = asyncio.run(asyncio.wait_for(protocol(args.store), timeout=45))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
