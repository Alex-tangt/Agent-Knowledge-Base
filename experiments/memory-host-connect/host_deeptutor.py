#!/usr/bin/env python
r"""host_deeptutor.py -- host #2 (DeepTutor) joins the same service: config check + real MCP calls.

为什么用 DeepTutor 自身代码而不是裸 MCP 客户端：
  * `deeptutor.services.mcp.config.load_mcp_config()` 是 DeepTutor 真的配置加载器
    （pydantic `MCPConfig`，路径 = `<DEEPTUTOR_HOME>/data/user/settings/mcp.json`）；
  * `deeptutor.services.mcp.manager.MCPConnectionManager` / `probe_server` 是它真的
    MCP 连接与派发路径（streamableHttp 走官方 mcp SDK 的 streamable_http_client）。
  这样「接入」证据来自宿主自己的代码，而不是我们另写的客户端。

隔离：进程内先设 `DEEPTUTOR_HOME=<隔离 home>`，再 import deeptutor —— 它的
`get_runtime_home()` 只读该变量；用户真实 home（含明文云模型 key 的
model_catalog.json）完全不被读取、不被写。

用法（必须用 DeepTutor 所在解释器，3.12）：
    & "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" host_deeptutor.py `
        --home "$env:USERPROFILE\.openviking-mvp-host\hosts\deeptutor-home" `
        --json-out evidence\deeptutor\connect.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path

# ---- 关键：先定 home，再 import deeptutor ------------------------------------
def _resolve_home(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    raw = os.environ.get("DEEPTUTOR_MVP_HOME") or ""
    if raw.strip():
        return Path(raw).expanduser().resolve()
    return (Path(os.environ.get("USERPROFILE", Path.home()))
            / ".openviking-mvp-host" / "hosts" / "deeptutor-home").resolve()


_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]{8,})")


def redact(text: str) -> str:
    """证据落盘前统一脱敏（本实验配置本无密钥，双保险）。"""
    return _SECRET_RE.sub("<redacted>", text or "")


def sanitize_no_proxy() -> dict:
    """去掉 NO_PROXY 里 IPv6 的方括号（mvp-protocol「已定位的四个启动坑」②）。

    实测：宿主 NO_PROXY 含 `[::1]` 时，DeepTutor 走 httpx 建 client 会直接抛
    `InvalidURL: Invalid port: ':1]'` —— 连接根本没发出去。必须在 import httpx/发起连接
    **之前**收拾环境；opencode 侧同一坑在 PowerShell 层处理（host_opencode.ps1）。
    """
    changed: dict[str, tuple[str, str]] = {}
    for key in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(key)
        if not raw:
            continue
        parts = [p.strip().strip("[]") for p in raw.split(",") if p.strip()]
        fixed = ",".join(parts)
        if fixed != raw:
            os.environ[key] = fixed
            changed[key] = (raw, fixed)
    return {k: {"before": v[0], "after": v[1]} for k, v in changed.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description="DeepTutor → 隔离 OpenViking 服务接入核验")
    parser.add_argument("--home", default=None, help="隔离 DEEPTUTOR_HOME")
    parser.add_argument("--server", default="openviking-mvp", help="mcp.json 里的服务名")
    parser.add_argument("--json-out", default=None, help="证据 JSON 输出路径")
    parser.add_argument("--marker", default="M6-DEEPTUTOR", help="写入/检索用的唯一标记")
    parser.add_argument("--write-uri", default=None,
                        help="允许写入的 viking:// URI（默认 viking://resources/mvp-host-connect/deeptutor.md）")
    parser.add_argument("--dry-run", action="store_true", help="只核验配置，不连接、不调用")
    parser.add_argument("--keep-proxy-env", action="store_true",
                        help="不清理 NO_PROXY（诊断用；正常跑不要开）")
    parser.add_argument("--authn-mode", default="unknown",
                        help="task-1 实例的实际 authn 模式（事实字段，照实填；unknown 不等于已授权）")
    parser.add_argument("--peer-disk-path", default=None,
                        help="宿主①写的文件在实例 workspace 里的磁盘路径；给定时做逐字节比对")
    args = parser.parse_args()

    proxy_fix = {} if args.keep_proxy_env else sanitize_no_proxy()
    if proxy_fix:
        print(f"[INFO] sanitized proxy env (IPv6 brackets): {proxy_fix}")

    home = _resolve_home(args.home)
    os.environ["DEEPTUTOR_HOME"] = str(home)

    import deeptutor  # noqa: E402
    from deeptutor.services.mcp.config import load_mcp_config, mcp_config_path  # noqa: E402

    try:
        from importlib.metadata import version as _pkg_version
        dt_version = _pkg_version("deeptutor")
    except Exception:
        dt_version = getattr(deeptutor, "__version__", "unknown")

    report: dict = {
        "host": "deeptutor",
        # Lead decision (2026-10-10): the two hosts differ in driver. This one is driven by
        # DeepTutor's own MCP client path (probe_server / MCPConnectionManager), NOT by an LLM
        # choosing tools. The optional `deeptutor run chat` turn is separate additional evidence.
        "driver": "host MCP client path (DeepTutor's own probe_server / MCPConnectionManager)",
        "deeptutor_version": dt_version,
        "python": sys.version.split()[0],
        "deep_tutor_home": str(home),
        "mcp_config_path": str(mcp_config_path()),
        "server": args.server,
        "marker": args.marker,
        "proxy_env_sanitized": proxy_fix,
        "authn": {
            "mode": args.authn_mode,
            "note": "instance authn mode is a FACT supplied by task-1; 'unknown'/'no key' must "
                    "NOT be recorded as 'authorized'",
            "credential_headers": [],   # filled from config below (key names only, never values)
        },
        "steps": [],
    }

    def step(name: str, ok: bool | None, detail) -> None:
        report["steps"].append({"step": name, "ok": ok, "detail": detail})
        flag = {True: "PASS", False: "FAIL", None: "INFO"}[ok]
        # progress goes to stdout, not stderr: under Windows PowerShell 5.1 native stderr is
        # surfaced as an error record, which muddies the caller's exit-code reading.
        print(f"[{flag}] {name}: {detail}")

    # ---- 1. 宿主自己的配置加载器读到什么 ------------------------------------
    cfg_all = load_mcp_config()
    names = sorted(cfg_all.servers)
    if args.server not in cfg_all.servers:
        step("load_mcp_config", False, f"隔离配置里没有服务 {args.server}；现有：{names}")
        _dump(report, args.json_out)
        return 2
    cfg = cfg_all.servers[args.server]
    header_keys = sorted((cfg.headers or {}).keys())
    report["authn"]["credential_headers"] = [
        k for k in header_keys if "key" in k.lower() or "auth" in k.lower()
    ]
    # Which identity this host's config carries (fact, from the isolated mcp.json).
    # NOTE: MCPConnectionManager.ensure_started() re-reads mcp.json from disk, so an in-memory
    # header override would silently not apply -- identity changes must go through the config
    # file. Scope semantics are therefore measured separately by scope_probe.py over the HTTP API.
    report["identity"] = {
        "account": (cfg.headers or {}).get("X-OpenViking-Account", ""),
        "user": (cfg.headers or {}).get("X-OpenViking-User", ""),
        "source": "isolated mcp.json headers",
    }
    step("load_mcp_config", True, {
        "servers": names,
        "type": cfg.resolved_type(),
        "url": cfg.url,
        "enabled": cfg.enabled,
        "enabled_tools": cfg.enabled_tools,
        "disabled_tools": cfg.disabled_tools,
        "headers_keys": header_keys,  # 只记键名，不记值
    })

    # ---- 2. 白名单边界（配置即边界） ----------------------------------------
    read_ok = [t for t in ("search", "read", "find") if cfg.tool_allowed(t, f"{args.server}_{t}")]
    write_ok = cfg.tool_allowed("write", f"{args.server}_write")
    forbidden = {t: cfg.tool_allowed(t, f"{args.server}_{t}") for t in ("forget", "add_skill")}
    step("tool_whitelist", bool(read_ok) and write_ok and not any(forbidden.values()),
         {"read_allowed": read_ok, "write_allowed": write_ok, "forbidden_allowed": forbidden})

    if args.dry_run:
        _dump(report, args.json_out)
        return 0

    # ---- 3. 真连接 + tools/list ---------------------------------------------
    from deeptutor.services.mcp.manager import (  # noqa: E402
        SHARED_OWNER, MCPConnectionManager, probe_server,
    )

    async def run() -> int:
        probe = await probe_server(cfg)
        report["probe"] = {"ok": probe.get("ok"), "error": redact(str(probe.get("error", ""))),
                           "tool_count": len(probe.get("tools", [])),
                           "tools": [t["name"] for t in probe.get("tools", [])]}
        step("probe_server(list_tools)", bool(probe.get("ok")),
             f"ok={probe.get('ok')} tools={report['probe']['tool_count']} err={report['probe']['error']}")
        if not probe.get("ok"):
            return 3

        manager = MCPConnectionManager()
        await manager.ensure_started()
        status = manager.status(SHARED_OWNER)
        report["connection_status"] = status
        conn = next((r for r in status if r["name"] == args.server), None)
        step("manager.ensure_started", bool(conn and conn["status"] == "connected"),
             f"status={conn['status'] if conn else 'missing'} error={redact((conn or {}).get('error', ''))}")

        async def call(tool: str, arguments: dict, note: str) -> str:
            out = await manager.call_tool(SHARED_OWNER, args.server, tool, arguments,
                                          timeout=cfg.tool_timeout)
            report.setdefault("tool_calls", []).append({
                "tool": tool, "arguments": arguments, "note": note,
                "response": redact(out),
            })
            head = redact(out)[:400].replace("\n", " ")
            print(f"    -> {tool}({json.dumps(arguments, ensure_ascii=False)}) => {head}")
            return out

        # 3a. 读：共享检索（另一宿主写的条目也应可见）
        await call("search", {"query": args.marker, "limit": 5}, "shared read (search)")
        # 3b. 写：一次允许的内容写（实例文档明示可写域 = viking://resources/）
        uri = args.write_uri or "viking://resources/mvp-host-connect/deeptutor.md"
        content = (f"# M6 DeepTutor host evidence\n\nmarker={args.marker}\n"
                   f"written_by=deeptutor (isolated)\n")
        await call("write", {
            "uri": uri,
            "content": content,
            "mode": "replace",
            "wait": True,
        }, "allowed content write")
        # 3c. 原文读回 + 逐字节比对（M6 要求「写入一次 + 共享读取」；读回必须一致）
        #     NOTE: the read tool's parameter is `uris` (str | list[str]), not `uri` --
        #     passing `uri` fails pydantic validation (measured 2026-10-10).
        readback = await call("read", {"uris": uri}, "read-back own write")
        readback_ok = content.strip() in (readback or "")
        step("readback_matches_write", readback_ok,
             {"uri": uri, "written_chars": len(content), "readback_chars": len(readback or ""),
              "exact_substring": readback_ok})
        # 3d. 枚举 + 跨宿主读：opencode 写的条目（若已存在）
        await call("list", {"uri": "viking://resources/mvp-host-connect"}, "enumerate shared dir")
        peer_uri = "viking://resources/mvp-host-connect/opencode.md"
        peer = await call("read", {"uris": peer_uri}, "read peer host write (may be absent)")
        report["cross_host"] = {
            "peer_uri": peer_uri,
            "visible": ("NOT_FOUND" not in (peer or "")) and not (peer or "").startswith("Error executing tool"),
            "is_error": (peer or "").startswith("Error executing tool"),
        }
        # Byte-identity: what host #2 reads over MCP must equal the bytes host #1 wrote on disk.
        if args.peer_disk_path and Path(args.peer_disk_path).is_file():
            disk = Path(args.peer_disk_path).read_bytes()
            readback = (peer or "").encode("utf-8")
            report["cross_host"]["peer_disk_path"] = args.peer_disk_path
            report["cross_host"]["disk_bytes"] = len(disk)
            report["cross_host"]["mcp_read_bytes"] = len(readback)
            report["cross_host"]["disk_sha256"] = hashlib.sha256(disk).hexdigest()
            report["cross_host"]["mcp_read_sha256"] = hashlib.sha256(readback).hexdigest()
            report["cross_host"]["byte_identical"] = disk == readback
        step("cross_host_peer_read", report["cross_host"]["visible"],
             f"opencode.md visible to DeepTutor: {report['cross_host']['visible']} "
             f"(byte_identical={report['cross_host'].get('byte_identical', 'n/a')})")

        await manager.shutdown()
        ok = all(s["ok"] is not False for s in report["steps"])
        step("summary", ok, f"{sum(1 for s in report['steps'] if s['ok'] is True)} PASS / "
                            f"{sum(1 for s in report['steps'] if s['ok'] is False)} FAIL")
        return 0 if ok else 1

    rc = asyncio.run(run())
    _dump(report, args.json_out)
    return rc


def _dump(report: dict, json_out: str | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if json_out:
        path = Path(json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
        print(f"[evidence] {path}")


if __name__ == "__main__":
    raise SystemExit(main())
