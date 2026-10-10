# -*- coding: utf-8 -*-
"""隔离实例与写入守卫（复核线专用：端口 8783，数据根在仓库外）。

一切写入都必须经过 `assert_writable()`。它同时充当静态自检的一条断言
（见 verify_checks.run_static）：判定标准本身也要证明"写域不外溢"。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import verify_criteria as C

# ---------------------------------------------------------------------------
# 路径与隔离面
# ---------------------------------------------------------------------------

MAIN_TREE = Path(os.environ.get("OV_MAIN_TREE", r"D:\python_work\work2026-4\Agent-Knowledge-Base"))
VENV_OV = MAIN_TREE / "venv-ov" / "Scripts"
PYTHON_EXE = VENV_OV / "python.exe"
SERVER_EXE = VENV_OV / "openviking-server.exe"
OV_EXE = VENV_OV / "ov.exe"

VERIFY_MODULE_DIR = Path(__file__).resolve().parent
VERIFY_ROOT = Path(
    os.environ.get("OV_VERIFY_ROOT", str(Path.home() / ".openviking-mvp-verify"))
).resolve()

SERVER_CONF = VERIFY_ROOT / "ov.conf"
CLI_CONF = VERIFY_ROOT / "ovcli.conf"
WORKSPACE = VERIFY_ROOT / "data"
LOGDIR = VERIFY_ROOT / "logs"
EVIDENCE_DIR = VERIFY_MODULE_DIR / "evidence"
PID_FILE = VERIFY_ROOT / "server.pid"
SERVER_LOG = LOGDIR / "openviking-server.log"

HOST = C.ISOLATION["host"]
PORT = int(C.ISOLATION["port"])
BASE_URL = f"http://{HOST}:{PORT}"

FORBIDDEN_PATHS = [
    Path.home() / ".config" / "opencode" / "knowledge",
    Path.home() / ".config" / "opencode" / "opencode.json",
    Path.home() / ".openviking",
    MAIN_TREE / "memory_agent" / "vector_db",
]
FORBIDDEN_PORTS = set(C.ISOLATION["forbidden_ports"])


def assert_writable(path: Path) -> Path:
    """只允许写入 verify_root 或本模块目录；拒绝禁止路径与其它端口的数据根。"""
    p = Path(path).resolve()
    allowed_roots = [VERIFY_ROOT, VERIFY_MODULE_DIR]
    if not any(_is_under(p, r) for r in allowed_roots):
        raise PermissionError(f"write outside allowed verify roots: {p}")
    for forbidden in FORBIDDEN_PATHS:
        if _is_under(p, forbidden.resolve()):
            raise PermissionError(f"write to forbidden path: {p}")
    if PORT in FORBIDDEN_PORTS:
        raise PermissionError("isolation port collides with a forbidden port")
    return p


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def ensure_dirs() -> None:
    for d in (VERIFY_ROOT, WORKSPACE, LOGDIR, EVIDENCE_DIR, VERIFY_ROOT / "source-tree"):
        assert_writable(d)
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 哈希 / 落盘
# ---------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def first_divergence(a: bytes, b: bytes) -> Optional[int]:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return None if len(a) == len(b) else min(len(a), len(b))


def write_json(path: Path, obj: Any) -> Path:
    p = assert_writable(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return p


def write_text(path: Path, text: str) -> Path:
    p = assert_writable(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 配置文件（必须无 BOM）
# ---------------------------------------------------------------------------

def build_conf(workspace: Path) -> Dict[str, Any]:
    """隔离配置：本机已有缓存的 local embedding，无 vlm 段（不启用云模型）。"""
    return {
        "default_account": "default",
        "default_user": "default",
        "storage": {"workspace": str(workspace)},
        "embedding": {
            "dense": {"provider": "local", "model": "bge-small-zh-v1.5-f16"},
            "text_source": "content_only",
        },
        # 不写 vlm / query_planner：避免任何云模型与意图规划调用
        "memory": {"extraction_enabled": False, "session_skill_extraction_enabled": False},
        "retrieval": {"enable_intent": False},
        "auto_generate_l0": False,
        "auto_generate_l1": False,
        "allow_private_networks": False,
        "server": {
            "host": HOST,
            "port": PORT,
            "auth_mode": "dev",
            "workers": 1,
        },
    }


def build_cli_conf() -> Dict[str, Any]:
    return {"url": BASE_URL, "account": "default", "user": "default", "timeout": C.CALL_TIMEOUT_S}


def has_bom(path: Path) -> bool:
    return path.read_bytes()[:3] == b"\xef\xbb\xbf"


def write_conf_files() -> Dict[str, str]:
    """写 ov.conf / ovcli.conf（UTF-8 无 BOM，JSON 文本）。"""
    ensure_dirs()
    ov = assert_writable(SERVER_CONF)
    cli = assert_writable(CLI_CONF)
    ov.write_text(json.dumps(build_conf(WORKSPACE), ensure_ascii=False, indent=2), encoding="utf-8")
    cli.write_text(json.dumps(build_cli_conf(), ensure_ascii=False, indent=2), encoding="utf-8")
    if has_bom(ov) or has_bom(cli):
        raise RuntimeError("配置文件带 BOM —— 必须无 BOM")
    return {
        "ov.conf": sha256_bytes(ov.read_bytes()),
        "ovcli.conf": sha256_bytes(cli.read_bytes()),
        "ov.conf_path": str(ov),
        "ovcli.conf_path": str(cli),
    }


def child_env() -> Dict[str, str]:
    env = dict(os.environ)
    env["OPENVIKING_CONFIG_FILE"] = str(SERVER_CONF)
    env["OPENVIKING_CLI_CONFIG_FILE"] = str(CLI_CONF)
    # 已知坑②：宿主 NO_PROXY 含带方括号的 IPv6 → 内置客户端报 Invalid port。
    # 按进程去掉方括号项（只影响本次隔离实例）。
    if "NO_PROXY" in env:
        kept = [s for s in env["NO_PROXY"].split(",") if s.strip() and "[" not in s and "]" not in s]
        env["NO_PROXY"] = ",".join(kept)
    if "no_proxy" in env:
        kept = [s for s in env["no_proxy"].split(",") if s.strip() and "[" not in s and "]" not in s]
        env["no_proxy"] = ",".join(kept)
    return env


# ---------------------------------------------------------------------------
# 端口 / 进程
# ---------------------------------------------------------------------------

def port_listening_pid(port: int = PORT) -> Optional[int]:
    """返回监听 127.0.0.1:<port> 的 PID（Windows netstat）。"""
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=30
        ).stdout
    except Exception:
        return None
    pat = re.compile(rf"^\s*TCP\s+{re.escape(HOST)}:{port}\s+\S+\s+LISTENING\s+(\d+)", re.M)
    m = pat.search(out)
    return int(m.group(1)) if m else None


def port_open(port: int = PORT, timeout: float = 1.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((HOST, port)) == 0


def process_cmdline(pid: int) -> str:
    """取进程命令行（用于"这个 PID 到底是不是我们的实例"的所有权校验）。"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
            capture_output=True, text=True, timeout=60, errors="replace",
        )
        return (r.stdout or "").strip()
    except Exception as exc:
        return f"<cmdline unavailable: {exc}>"


def owns_instance(pid: int) -> Dict[str, Any]:
    """该 PID 是否属于本线隔离实例：命令行必须同时提到本线端口或本线 conf。"""
    cmd = process_cmdline(pid)
    return {
        "pid": pid,
        "cmdline": cmd[:600],
        "owns": (f"--port {PORT}" in cmd) or (str(SERVER_CONF) in cmd)
        or (f"port={PORT}" in cmd) or (f":{PORT}" in cmd),
    }


def wait_listener_pid(timeout: int = C.SERVER_READY_TIMEOUT_S) -> Optional[int]:
    """等待并返回真正监听 8783 的 PID。

    注意：pip 生成的 openviking-server.exe 是个 launcher，它会再拉起子进程；
    Popen 拿到的是 launcher 的 PID，而监听端口的是子进程 —— 两者不是同一个 PID。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        pid = port_listening_pid()
        if pid is not None:
            return pid
        time.sleep(1)
    return None


def start_server() -> Dict[str, Any]:
    """启动隔离实例；只在本线端口/根上操作。返回 launcher/监听 PID 与日志路径。"""
    ensure_dirs()
    if port_open():
        return {
            "started": False,
            "reason": "port_already_open",
            "pid": port_listening_pid(),
            "note": "端口已被占用；按纪律不接管别人的实例，停止操作",
        }
    if not SERVER_EXE.exists():
        raise FileNotFoundError(f"缺少隔离实例可执行文件: {SERVER_EXE}")
    logf = open(assert_writable(SERVER_LOG), "a", encoding="utf-8")
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    proc = subprocess.Popen(
        [str(SERVER_EXE), "--config", str(SERVER_CONF), "--host", HOST, "--port", str(PORT)],
        cwd=str(VERIFY_ROOT),
        env=child_env(),
        stdout=logf,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    assert_writable(PID_FILE).write_text(str(proc.pid), encoding="utf-8")
    listener = wait_listener_pid()
    return {
        "started": True,
        "pid": proc.pid,
        "launcher_pid": proc.pid,
        "listener_pid": listener,
        "pid_match": listener == proc.pid,
        "log": str(SERVER_LOG),
        "cmd": str(SERVER_EXE),
    }


def stop_server(
    expected_pid: Optional[int] = None,
    listener_pid: Optional[int] = None,
) -> Dict[str, Any]:
    """停本线隔离实例。

    所有权判据（不靠 PID 相等，因为 launcher ≠ 监听进程）：
    当前 8783 的监听进程命令行必须提到本线端口或本线 conf；否则拒绝停止。
    """
    launcher = expected_pid
    if launcher is None and PID_FILE.exists():
        launcher = int(PID_FILE.read_text(encoding="utf-8").strip() or "0") or None
    owner = port_listening_pid()
    result: Dict[str, Any] = {
        "expected_launcher_pid": launcher,
        "recorded_listener_pid": listener_pid,
        "port_owner_pid": owner,
    }
    if owner is None:
        result.update({"stopped": True, "note": "端口本来就未监听"})
        return result
    ownership = owns_instance(owner)
    result["ownership"] = ownership
    if not ownership["owns"]:
        result.update(
            {"stopped": False, "note": "8783 的监听进程命令行不含本线端口/conf → 拒绝停止（可能是别人的实例）"}
        )
        return result

    killed: List[int] = []
    for pid in [owner, launcher]:
        if pid and pid not in killed:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, text=True)
            killed.append(pid)
    deadline = time.time() + C.SERVER_STOP_TIMEOUT_S
    while time.time() < deadline:
        if not port_open():
            result.update({"stopped": True, "killed_pids": killed})
            return result
        still = port_listening_pid()
        if still and still not in killed and owns_instance(still)["owns"]:
            subprocess.run(["taskkill", "/PID", str(still), "/T", "/F"],
                           capture_output=True, text=True)
            killed.append(still)
        time.sleep(1)
    result.update({"stopped": False, "killed_pids": killed, "note": "超时后端口仍监听"})
    return result


__all__ = [
    "MAIN_TREE", "VENV_OV", "PYTHON_EXE", "SERVER_EXE", "OV_EXE",
    "VERIFY_MODULE_DIR", "VERIFY_ROOT", "SERVER_CONF", "CLI_CONF", "WORKSPACE",
    "LOGDIR", "EVIDENCE_DIR", "PID_FILE", "SERVER_LOG", "HOST", "PORT", "BASE_URL",
    "assert_writable", "ensure_dirs", "sha256_bytes", "sha256_text", "first_divergence",
    "write_json", "write_text", "build_conf", "build_cli_conf", "has_bom",
    "write_conf_files", "child_env", "port_listening_pid", "port_open",
    "process_cmdline", "owns_instance", "wait_listener_pid",
    "start_server", "stop_server",
]
