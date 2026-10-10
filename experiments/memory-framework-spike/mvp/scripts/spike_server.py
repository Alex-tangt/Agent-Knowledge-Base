"""Server lifecycle for the isolated MVP spike instance (127.0.0.1:8781).

Usage:
    python spike_server.py start|stop|status|restart|logs

Isolation: config comes from %USERPROFILE%\\.openviking-mvp-spike\\ov.conf via
OPENVIKING_CONFIG_FILE, state lives in that directory's data\\ workspace, and the
process runs with all proxy variables removed (see spike_common.sanitized_env).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from spike_common import (
    BASE_URL,
    LOGS,
    OV_CONF,
    PID_FILE,
    PORT,
    SERVER_EXE,
    http_status,
    log,
    sanitized_env,
    wait_for_health,
)


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
    status, body = http_status("/health", timeout=5.0)
    if status == 200:
        log(f"already healthy: {body[:120].decode('utf-8', 'replace')}")
        return _pid() or 0

    stdout_path = LOGS / "server.out.log"
    stderr_path = LOGS / "server.err.log"
    LOGS.mkdir(parents=True, exist_ok=True)
    stdout = open(stdout_path, "ab")
    stderr = open(stderr_path, "ab")
    env = sanitized_env({"OPENVIKING_START_HOST": "127.0.0.1", "OPENVIKING_START_PORT": str(PORT)})
    proc = subprocess.Popen(
        [str(SERVER_EXE), "--config", str(OV_CONF), "--host", "127.0.0.1", "--port", str(PORT)],
        stdout=stdout,
        stderr=stderr,
        env=env,
        cwd=str(LOGS),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    log(f"started pid={proc.pid} -> {BASE_URL}")
    if wait:
        info = wait_for_health(timeout=timeout)
        log(f"healthy: {json.dumps(info, ensure_ascii=False)}")
    return proc.pid


def stop() -> None:
    pid = _pid()
    if not pid:
        log("no pid file")
        return
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
    # wait for the port to stop answering
    deadline = time.time() + 30
    while time.time() < deadline:
        status, _ = http_status("/health", timeout=2.0)
        if status == 0:
            break
        time.sleep(1)
    PID_FILE.unlink(missing_ok=True)
    log(f"stopped pid={pid}")


def status() -> int:
    pid = _pid()
    code, body = http_status("/health", timeout=5.0)
    print(json.dumps({
        "pid": pid,
        "pid_alive": _alive(pid),
        "health_status": code,
        "health_body": body.decode("utf-8", "replace"),
        "port": PORT,
        "base_url": BASE_URL,
    }, ensure_ascii=False, indent=2))
    return 0 if code == 200 else 1


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "start":
        start()
    elif cmd == "stop":
        stop()
    elif cmd == "restart":
        stop()
        start()
    elif cmd == "status":
        return status()
    elif cmd == "logs":
        for name in ("server.out.log", "server.err.log"):
            path = LOGS / name
            print(f"===== {path} =====")
            if path.exists():
                print(path.read_text(encoding="utf-8", errors="replace"))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
