#!/usr/bin/env python
r"""host_server.py -- lifecycle for THIS line's isolated OpenViking instance (127.0.0.1:8782).

Deliberately independent of the spike line (task-1) so the two never share an instance:

  * my state:  %USERPROFILE%\.openviking-mvp-host\{ov.conf,ov-authz.conf,ovcli.conf,data,logs}
  * their state: %USERPROFILE%\.openviking-mvp-spike\ (8781) and the reviewer's 8783 - never touched
  * ports 8781 / 8783 / 8765 are asserted against before anything is started

Two profiles, same instance/port/workspace (the recipe task-1 handed over):
  * ``dev``    (default) : no auth_mode -> server auto-detects dev on localhost.
                           Fact: the dev plugin always resolves ROLE=root, so it CANNOT express a
                           read-only identity. Enough for M6 "two hosts share read + one write".
  * ``authz``           : auth_mode=api_key, root key expanded from the process environment
                           (``$OPENVIKING_MVP_HOST_ROOT_KEY`` placeholder in the conf, value never
                           written to disk by this script).

Host pitfalls neutralised here (mvp-protocol "four startup pitfalls"):
  * NO_PROXY with bracketed IPv6 -> strip brackets;
  * host exports NO_PROXY/no_proxy and HTTP(S)_PROXY in both spellings -> rebuild the env block
    case-insensitively and drop the proxies (this instance must not reach the network);
  * conf must be BOM-free -> written with encoding="utf-8" (no BOM).

Usage:
    python host_server.py start|stop|status|logs [--profile dev|authz]
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:  # console is cp936 on this host; keep our output readable when captured
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

VENV_SCRIPTS = Path(
    os.environ.get("HOST_VENV")
    or r"D:\python_work\work2026-4\Agent-Knowledge-Base\venv-ov\Scripts"
)
ROOT = Path(os.environ.get("HOST_ROOT") or (Path(os.path.expanduser("~")) / ".openviking-mvp-host"))
PORT = int(os.environ.get("HOST_PORT") or "8782")
BASE_URL = f"http://127.0.0.1:{PORT}"
WORKSPACE = ROOT / "data"
LOGS = ROOT / "logs"
SERVER_EXE = VENV_SCRIPTS / "openviking-server.exe"
PID_FILE = LOGS / "server.pid"
ROOT_KEY_ENV = "OPENVIKING_MVP_HOST_ROOT_KEY"
FORBIDDEN_PORTS = (8781, 8783, 8765)

CONF_DEV = ROOT / "ov.conf"
CONF_AUTHZ = ROOT / "ov-authz.conf"
OVCLI_CONF = ROOT / "ovcli.conf"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------ guardrails

def assert_not_foreign_port() -> None:
    """Refuse to touch an instance that belongs to another line."""
    if PORT in FORBIDDEN_PORTS:
        raise SystemExit(f"refusing to run on port {PORT}: belongs to another line "
                         f"({', '.join(map(str, FORBIDDEN_PORTS))})")
    # the workspace and logs must be inside MY root, not the spike root
    if "openviking-mvp-spike" in str(ROOT).lower():
        raise SystemExit(f"refusing to use the spike root: {ROOT}")


# ------------------------------------------------------------------- env / conf

def sanitized_env(extra: dict | None = None) -> dict:
    raw = dict(os.environ)
    if extra:
        raw.update(extra)
    out: dict[str, str] = {}
    for key, value in raw.items():
        out[key.upper()] = value
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        out.pop(key, None)
    out["NO_PROXY"] = "localhost,127.0.0.1,::1"
    out["OPENVIKING_CONFIG_FILE"] = str(active_conf())
    out["OPENVIKING_CLI_CONFIG_FILE"] = str(OVCLI_CONF)
    return out


PROFILE = "dev"


def active_conf() -> Path:
    return CONF_AUTHZ if PROFILE == "authz" else CONF_DEV


def write_conf_if_absent() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    base = {
        "default_account": "default",
        "default_user": "default",
        "storage": {"workspace": str(WORKSPACE)},
        "embedding": {"dense": {"provider": "local", "model": "bge-small-zh-v1.5-f16"},
                      "max_concurrent": 1},
        "server": {"host": "127.0.0.1", "port": PORT},
    }
    # BOM-free, and never overwrite an existing conf (the authz run may have written one)
    if not CONF_DEV.exists():
        CONF_DEV.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log(f"wrote {CONF_DEV}")
    if not CONF_AUTHZ.exists():
        authz = json.loads(json.dumps(base))
        authz["server"]["auth_mode"] = "api_key"
        authz["server"]["root_api_key"] = f"${ROOT_KEY_ENV}"
        CONF_AUTHZ.write_text(json.dumps(authz, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log(f"wrote {CONF_AUTHZ}")


def root_api_key() -> str:
    """Root key for the authz profile: process env only, never persisted by this script."""
    value = os.environ.get(ROOT_KEY_ENV)
    if value:
        return value
    return "host-" + secrets.token_hex(16)


def write_ovcli_conf(api_key: str | None = None) -> Path:
    data: dict = {"url": BASE_URL, "output": "json", "timeout": 120.0}
    if PROFILE == "authz":
        data["api_key"] = api_key or root_api_key()
        data["root_api_key"] = root_api_key()
    OVCLI_CONF.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return OVCLI_CONF


# ------------------------------------------------------------------------ http

def http_status(path: str, timeout: float = 5.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(BASE_URL + path, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:
        return 0, str(exc).encode("utf-8", "replace")


def wait_for_health(timeout: float = 300.0, interval: float = 2.0) -> dict:
    deadline = time.time() + timeout
    last: tuple[int, bytes] = (0, b"")
    while time.time() < deadline:
        last = http_status("/health", timeout=5.0)
        if last[0] == 200:
            return json.loads(last[1].decode("utf-8"))
        time.sleep(interval)
    raise TimeoutError(f"server not healthy: {last[0]} {last[1][:200]!r}")


# ------------------------------------------------------------------- lifecycle

def _pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                         capture_output=True, text=True)
    return str(pid) in out.stdout


def start(wait: bool = True, timeout: float = 300.0) -> int:
    assert_not_foreign_port()
    status, body = http_status("/health", timeout=5.0)
    if status == 200:
        log(f"already healthy: {body[:150].decode('utf-8', 'replace')}")
        return _pid() or 0
    write_conf_if_absent()
    stdout = open(LOGS / "server.out.log", "ab")
    stderr = open(LOGS / "server.err.log", "ab")
    env = sanitized_env({"OPENVIKING_START_HOST": "127.0.0.1", "OPENVIKING_START_PORT": str(PORT)})
    if PROFILE == "authz":
        env[ROOT_KEY_ENV] = root_api_key()
    proc = subprocess.Popen(
        [str(SERVER_EXE), "--config", str(active_conf()), "--host", "127.0.0.1", "--port", str(PORT)],
        stdout=stdout, stderr=stderr, env=env, cwd=str(LOGS),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    log(f"started pid={proc.pid} profile={PROFILE} conf={active_conf()} -> {BASE_URL}")
    if wait:
        log(f"healthy: {json.dumps(wait_for_health(timeout=timeout), ensure_ascii=False)}")
    return proc.pid


def stop() -> None:
    pid = _pid()
    if not pid:
        log("no pid file")
        return
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
    deadline = time.time() + 30
    while time.time() < deadline:
        if http_status("/health", timeout=2.0)[0] == 0:
            break
        time.sleep(1)
    PID_FILE.unlink(missing_ok=True)
    log(f"stopped pid={pid}")


def status() -> int:
    pid = _pid()
    code, body = http_status("/health", timeout=5.0)
    print(json.dumps({
        "pid": pid, "pid_alive": _alive(pid), "port": PORT, "base_url": BASE_URL,
        "health_status": code, "health_body": body.decode("utf-8", "replace"),
        "profile": PROFILE, "conf": str(active_conf()), "workspace": str(WORKSPACE),
    }, ensure_ascii=False, indent=2))
    return 0 if code == 200 else 1


def main(argv: list[str]) -> int:
    global PROFILE
    parser = argparse.ArgumentParser(description="isolated OpenViking instance for the host-connect line")
    parser.add_argument("cmd", nargs="?", default="status",
                        choices=["start", "stop", "restart", "status", "logs", "conf"])
    parser.add_argument("--profile", default=os.environ.get("HOST_PROFILE", "dev"),
                        choices=["dev", "authz"])
    args = parser.parse_args(argv[1:])
    PROFILE = args.profile.strip().lower()

    if args.cmd == "start":
        start()
    elif args.cmd == "stop":
        stop()
    elif args.cmd == "restart":
        stop()
        start()
    elif args.cmd == "status":
        return status()
    elif args.cmd == "conf":
        write_conf_if_absent()
        print(json.dumps({"dev": str(CONF_DEV), "authz": str(CONF_AUTHZ),
                          "active": str(active_conf()), "workspace": str(WORKSPACE)},
                         ensure_ascii=False, indent=2))
    elif args.cmd == "logs":
        for name in ("server.out.log", "server.err.log"):
            path = LOGS / name
            print(f"===== {path} =====")
            if path.exists():
                print(path.read_text(encoding="utf-8", errors="replace")[-8000:])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
