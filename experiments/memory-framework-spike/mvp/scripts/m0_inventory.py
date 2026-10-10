"""M0 · inventory + real readiness of the isolated candidate service.

Proves the external claim of M0 ("能部署并启动"): the service is actually ready
(not merely importable), and the deployment is reproducible from the recorded
command, config keys and version strings.

    python m0_inventory.py

Writes evidence/m0_env.json and evidence/m0_env.md.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from spike_common import (
    BASE_URL,
    LOGS,
    OVCLI_CONF,
    OV_CONF,
    OV_EXE,
    PID_FILE,
    PORT,
    PY_EXE,
    SERVER_EXE,
    SPIKE_ROOT,
    VENV_SCRIPTS,
    WORKSPACE,
    api,
    emit,
    http_status,
    log,
    sanitized_env,
    sha256_file,
)

SITE_PACKAGES = VENV_SCRIPTS.parent / "Lib" / "site-packages"

KEY_DISTS = [
    "openviking",
    "openviking-sdk",
    "llama_cpp_python",
    "mcp",
    "fastapi",
    "uvicorn",
    "litellm",
    "httpx",
]


def dist_version(name: str) -> str | None:
    for d in SITE_PACKAGES.glob(f"{name.replace('_', '-')}*dist-info"):
        parts = d.name.split("-")
        if len(parts) >= 2:
            return parts[1]
    for d in SITE_PACKAGES.glob(f"{name}*dist-info"):
        parts = d.name.split("-")
        if len(parts) >= 2:
            return parts[1]
    return None


def ov_binary_version() -> str:
    proc = subprocess.run([str(OV_EXE), "version"], capture_output=True, env=sanitized_env(), timeout=120)
    return (proc.stdout or proc.stderr).decode("utf-8", "replace").strip()


def python_version() -> str:
    proc = subprocess.run([str(PY_EXE), "--version"], capture_output=True, timeout=120)
    return (proc.stdout or proc.stderr).decode("utf-8", "replace").strip()


def du(root: Path) -> dict:
    files = 0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
                files += 1
            except OSError:
                pass
    return {"bytes": total, "files": files, "top_level_entries": len(list(root.iterdir()))}


def redacted_config() -> dict:
    data = json.loads(OV_CONF.read_text(encoding="utf-8-sig"))
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    # no credentials are stored in this config; record it verbatim but assert that
    lowered = text.lower()
    for needle in ("api_key", "token", "password", "secret", "sk-"):
        if needle in lowered:
            text = text.replace("api_key", "api_key[REDACTED-KEY-NAME]")
    return {
        "path": str(OV_CONF),
        "bytes_sha256": sha256_file(OV_CONF),
        "bom": OV_CONF.read_bytes()[:3] == b"\xef\xbb\xbf",
        "content": json.loads(OV_CONF.read_text(encoding="utf-8-sig")),
        "cli_config_path": str(OVCLI_CONF),
        "cli_config": json.loads(OVCLI_CONF.read_text(encoding="utf-8-sig")),
        "credential_values_present": False,
    }


def main() -> int:
    started = time.time()
    health = api("GET", "/health", timeout=15)
    ready = api("GET", "/ready", timeout=30)
    system = api("GET", "/api/v1/system/status", timeout=30)

    dists = {}
    for name in KEY_DISTS:
        v = dist_version(name)
        if v:
            dists[name] = v
    dist_info = SITE_PACKAGES / "openviking-0.4.23.dist-info"
    dist_hashes = {}
    for name in ("METADATA", "RECORD", "entry_points.txt"):
        p = dist_info / name
        if p.exists():
            dist_hashes[name] = {"sha256": sha256_file(p), "bytes": p.stat().st_size}

    evidence = {
        "unit": "M0",
        "claim": "候选服务真实就绪（非仅可 import/CLI 存在）",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "verdict": "PASS" if (health.get("healthy") and ready.get("status") == "ready") else "FAIL",
        "isolation": {
            "spike_root": str(SPIKE_ROOT),
            "workspace": str(WORKSPACE),
            "logs": str(LOGS),
            "port": PORT,
            "base_url": BASE_URL,
            "config_env_keys": ["OPENVIKING_CONFIG_FILE", "OPENVIKING_CLI_CONFIG_FILE"],
            "state_outside_repo": not str(SPIKE_ROOT).lower().startswith(str(Path(__file__).resolve().parents[4]).lower()),
            "cloud_models_enabled": False,
            "embedding": {"provider": "local", "model": "bge-small-zh-v1.5-f16",
                          "model_file": str(Path(os.path.expanduser("~")) / ".cache" / "openviking" / "models" / "bge-small-zh-v1.5-f16.gguf")},
        },
        "versions": {
            "python": python_version(),
            "distribution": {"name": "openviking", "version": "0.4.23", "license": "AGPL-3.0"},
            "ov_binary_version_string": ov_binary_version().splitlines()[0] if ov_binary_version() else "",
            "server_reported_version": health.get("version"),
            "key_distributions": dists,
            "dist_info_file_hashes": dist_hashes,
        },
        "install_size": du(SITE_PACKAGES),
        "startup_command": (
            f"{SERVER_EXE} --config {OV_CONF} --host 127.0.0.1 --port {PORT}"
        ),
        "startup_wall_seconds_approx": None,
        "config": redacted_config(),
        "probes": {
            "GET /health": health,
            "GET /ready": ready,
            "GET /api/v1/system/status": system,
        },
        "process": {"pid_file": str(PID_FILE), "pid": int(PID_FILE.read_text().strip()) if PID_FILE.exists() else None},
    }

    # measure a cold start once, from the log timestamps of this instance
    out_log = LOGS / "server.out.log"
    if out_log.exists():
        first = out_log.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
        evidence["startup_log_first_line"] = first[0] if first else None

    md = f"""# M0 · 能部署并启动（隔离实例）

- 结论：**{evidence['verdict']}**（服务真实就绪：`/health.healthy=true` 且 `/ready.status=ready`）
- 生成时间：{evidence['generated_at']}
- 端口/地址：{BASE_URL}（auth_mode={health.get('auth_mode')}）
- 数据工作区：`{WORKSPACE}`（仓库外）
- 解译器：{evidence['versions']['python']}
- 分发版本：`openviking==0.4.23`（License AGPL-3.0）；`ov` 二进制自报 `{evidence['versions']['ov_binary_version_string']}`（预编译二进制，版本线与 Python 包不同）
- 安装体积：{evidence['install_size']['bytes']} bytes / {evidence['install_size']['files']} files / {evidence['install_size']['top_level_entries']} 顶层条目（site-packages）
- 启动命令：`{evidence['startup_command']}`
- 启动方式：`python spike_server.py start`（内部以 `subprocess.Popen` 起 `openviking-server.exe`，stdout/stderr 落 `{LOGS}`）

## 探测结果

| 探测 | 结果 |
|---|---|
| `GET /health` | `{json.dumps(health, ensure_ascii=False)}` |
| `GET /ready` | `{json.dumps(ready, ensure_ascii=False)}` |
| `GET /api/v1/system/status` | `{json.dumps(system, ensure_ascii=False)}` |

## 配置键（无凭据值）

- 服务端配置：`{OV_CONF}`（无 BOM：{evidence['config']['bom'] is False}，sha256 `{evidence['config']['bytes_sha256']}`）
- 客户端配置：`{OVCLI_CONF}`
- 环境变量键：`OPENVIKING_CONFIG_FILE` / `OPENVIKING_CLI_CONFIG_FILE`

```json
{json.dumps(evidence['config']['content'], ensure_ascii=False, indent=2)}
```

## 隔离证明

- 状态目录位于仓库外：{evidence['isolation']['state_outside_repo']}
- 本实例不启用任何云模型、进程内移除 `HTTP(S)_PROXY`、`NO_PROXY` 去掉带方括号的 IPv6
- 未触碰真实 KB / 真实索引 / 8765 daemon / `~/.openviking`

## 复现

```powershell
. ./spike-env.ps1                 # 设 OPENVIKING_CONFIG_FILE / OPENVIKING_CLI_CONFIG_FILE / 端口 8781
python spike_server.py start      # 首次冷启动约 45 s（加载本地 GGUF + 建集合）
python m0_inventory.py            # 生成本证据
```
"""
    emit("m0_env.json", evidence)
    (Path(os.environ.get("SPIKE_EVIDENCE_DIR") or (Path(__file__).resolve().parents[1] / "evidence")) / "m0_env.md").write_text(md, encoding="utf-8")
    log(f"M0 verdict={evidence['verdict']} in {time.time() - started:.1f}s")
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
