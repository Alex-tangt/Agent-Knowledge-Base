"""Shared helpers for the personal-memory-base MVP spike (M0-M5).

Isolation contract (see mvp-protocol.md):
  * all state lives under %USERPROFILE%\\.openviking-mvp-spike  (never in the repo)
  * the server runs on 127.0.0.1:8781 only
  * no cloud model provider; local GGUF embedding (bge-small-zh-v1.5-f16)
  * the real KB, the real index, the 8765 daemon and ~/.openviking are never touched

Every helper here is stdlib-only so it can run under the main repo venv, the
candidate venv-ov, or a plain system python.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:  # console on this host is cp936; keep our Chinese log lines readable in captured output
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ---------------------------------------------------------------- environment

SPIKE_ROOT = Path(os.environ.get("SPIKE_ROOT") or (Path(os.path.expanduser("~")) / ".openviking-mvp-spike"))
VENV_SCRIPTS = Path(
    os.environ.get("SPIKE_VENV")
    or r"D:\python_work\work2026-4\Agent-Knowledge-Base\venv-ov\Scripts"
)
PORT = int(os.environ.get("SPIKE_PORT") or "8781")
BASE_URL = os.environ.get("SPIKE_BASE_URL") or f"http://127.0.0.1:{PORT}"
WORKSPACE = Path(os.environ.get("SPIKE_WORKSPACE") or (SPIKE_ROOT / "data"))
LOGS = Path(os.environ.get("SPIKE_LOGS") or (SPIKE_ROOT / "logs"))
SERVER_EXE = VENV_SCRIPTS / "openviking-server.exe"
PY_EXE = VENV_SCRIPTS / "python.exe"
OV_EXE = VENV_SCRIPTS / "ov.exe"
OV_CONF = Path(os.environ.get("OPENVIKING_CONFIG_FILE") or (SPIKE_ROOT / "ov.conf"))
OVCLI_CONF = Path(os.environ.get("OPENVIKING_CLI_CONFIG_FILE") or (SPIKE_ROOT / "ovcli.conf"))
PID_FILE = LOGS / "server.pid"

REPO_ROOT = Path(__file__).resolve().parents[4]  # .../experiments/memory-framework-spike/mvp/scripts -> repo


def sanitized_env(extra: dict | None = None) -> dict:
    """Process env for our OpenViking processes.

    Two host pitfalls are neutralised here:

    1. ``NO_PROXY`` on this machine contains the bracketed IPv6 literal ``[::1]``.
       litellm/httpx then raise ``Invalid port: ':1]'`` while parsing it.
    2. The host exports both ``NO_PROXY`` and ``no_proxy`` (and both
       ``HTTP_PROXY``/``http_proxy``).  Windows env blocks are case-insensitive,
       so a duplicated key makes ``Start-Process`` / ``CreateProcess`` fail with
       "dictionary already contains key".  We rebuild the block case-insensitively
       and drop the proxies entirely: this instance must not reach the network.
    """
    raw = dict(os.environ)
    if extra:
        raw.update(extra)
    out: dict[str, str] = {}
    for key, value in raw.items():
        out[key.upper()] = value
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        out.pop(key, None)
    out["NO_PROXY"] = "localhost,127.0.0.1,::1"
    out["OPENVIKING_CONFIG_FILE"] = str(OV_CONF)
    out["OPENVIKING_CLI_CONFIG_FILE"] = str(OVCLI_CONF)
    return out


# ---------------------------------------------------------------------- hash


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def read_bytes(path: str | Path) -> bytes:
    return Path(path).read_bytes()


# ----------------------------------------------------------------------- api


class ApiError(Exception):
    def __init__(self, status: int, body: bytes, url: str):
        super().__init__(f"HTTP {status} for {url}")
        self.status = status
        self.body = body
        self.url = url

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8"))
        except Exception:
            return None


def api(method: str, path: str, *, params: dict | None = None, body: dict | None = None,
        headers: dict | None = None, timeout: float = 300.0, raw: bool = False):
    """Call the isolated server. Returns parsed JSON (or raw bytes when raw=True)."""
    url = BASE_URL + path
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    data = None
    hdrs = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json; charset=utf-8"
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            if raw:
                return payload
            return json.loads(payload.decode("utf-8")) if payload else None
    except urllib.error.HTTPError as exc:
        raise ApiError(exc.code, exc.read(), url) from None
    except urllib.error.URLError as exc:
        raise ApiError(0, str(exc.reason).encode("utf-8"), url) from None


def http_status(path: str, timeout: float = 10.0) -> tuple[int, bytes]:
    """Return (status, body) without raising, for failure-visibility tests."""
    url = BASE_URL + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        return 0, str(exc.reason).encode("utf-8")


def read_content(uri: str, *, raw: bool = True, offset: int = 0, limit: int = -1,
                 timeout: float = 120.0) -> bytes:
    """GET /api/v1/content/read and return the正文 as UTF-8 bytes."""
    payload = api(
        "GET",
        "/api/v1/content/read",
        params={"uri": uri, "raw": "true" if raw else "false", "offset": offset, "limit": limit},
        timeout=timeout,
    )
    text = payload["result"]
    return text.encode("utf-8")


def write_content(uri: str, content: str, *, mode: str = "replace", wait: bool = True,
                  timeout: float = 300.0) -> dict:
    return api(
        "POST",
        "/api/v1/content/write",
        body={"uri": uri, "content": content, "mode": mode, "wait": wait, "timeout": timeout},
        timeout=timeout + 30,
    )


# ------------------------------------------------------------- local artifacts


def disk_path_for_uri(uri: str) -> Path | None:
    """Map a viking:// URI to the on-disk file inside the isolated workspace.

    Layout observed in 0.4.23: <workspace>/viking/<account>/<scope-path>
    e.g. viking://user/default/resources/a/b.md
         -> <workspace>/viking/default/user/default/resources/a/b.md
    """
    assert uri.startswith("viking://")
    rel = uri[len("viking://"):]
    if rel.startswith("~/"):
        rel = "user/default/" + rel[2:]
    account = "default"
    candidate = WORKSPACE / "viking" / account / rel
    return candidate


# ------------------------------------------------------------ ov CLI wrapper


def ov(*args: str, timeout: float = 300.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(OV_EXE), *args],
        capture_output=True,
        env=sanitized_env(),
        timeout=timeout,
    )


def ov_json(*args: str, timeout: float = 300.0):
    proc = ov(*args, timeout=timeout)
    out = proc.stdout.decode("utf-8", "replace").strip()
    # the Rust CLI prints a "cmd: ..." echo line before the JSON payload
    line = out.splitlines()[-1] if out else ""
    try:
        return json.loads(line)
    except Exception:
        return {"_raw_stdout": out, "_raw_stderr": proc.stderr.decode("utf-8", "replace"),
                "_returncode": proc.returncode}


# ------------------------------------------------------------------- evidence


def emit(name: str, payload: dict) -> Path:
    out_dir = Path(os.environ.get("SPIKE_EVIDENCE_DIR") or (REPO_ROOT / "experiments" / "memory-framework-spike" / "mvp" / "evidence"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[evidence] wrote {path}")
    return path


def wait_for_health(timeout: float = 240.0, interval: float = 2.0) -> dict:
    deadline = time.time() + timeout
    last: tuple[int, bytes] = (0, b"")
    while time.time() < deadline:
        last = http_status("/health", timeout=5.0)
        if last[0] == 200:
            return json.loads(last[1].decode("utf-8"))
        time.sleep(interval)
    raise TimeoutError(f"server did not become healthy: {last[0]} {last[1][:200]!r}")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
