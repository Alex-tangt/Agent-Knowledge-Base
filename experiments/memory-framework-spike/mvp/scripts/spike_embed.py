"""Lifecycle helper for the out-of-process embedding server (embed_server.py).

Keeps the model real (same GGUF) while moving it behind an HTTP boundary and
serializing every llama call -- the unsafe sharing that aborts the in-process
provider is what this removes.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from spike_common import LOGS, PY_EXE, SPIKE_ROOT, http_status, log, sanitized_env

HERE = Path(__file__).resolve().parent
MODEL = Path(os.path.expanduser("~")) / ".cache" / "openviking" / "models" / "bge-small-zh-v1.5-f16.gguf"
PORT = int(os.environ.get("SPIKE_EMBED_PORT", "8791"))
BASE = f"http://127.0.0.1:{PORT}"
PID_FILE = LOGS / "embed-server.pid"


def _pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def health() -> dict | None:
    status, body = http_status("/health", timeout=5.0, base=BASE)
    if status != 200:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


def health_ok() -> bool:
    return health() is not None


def start(port: int = PORT, timeout: float = 300.0) -> subprocess.Popen:
    if health_ok():
        log("embedding server already healthy")
        return None  # type: ignore[return-value]
    LOGS.mkdir(parents=True, exist_ok=True)
    out = open(LOGS / "embed-server.out.log", "ab")
    err = open(LOGS / "embed-server.err.log", "ab")
    proc = subprocess.Popen(
        [str(PY_EXE), str(HERE / "embed_server.py"), "--port", str(port)],
        stdout=out, stderr=err, cwd=str(HERE), env=sanitized_env(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    log(f"started embedding server pid={proc.pid} on {BASE}")
    wait_ready(timeout=timeout)
    return proc


def wait_ready(timeout: float = 300.0, interval: float = 1.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = health()
        if info:
            return info
        time.sleep(interval)
    raise TimeoutError("embedding server did not become ready")


def stop(proc: subprocess.Popen | None = None) -> None:
    pid = proc.pid if proc is not None else _pid()
    if not pid:
        return
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
    PID_FILE.unlink(missing_ok=True)
    log(f"stopped embedding server pid={pid}")
