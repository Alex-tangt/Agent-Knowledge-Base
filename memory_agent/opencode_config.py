"""opencode 侧配置：MCP 注册 + skill 落位（#52 / ADR-0028 D4）。

**零第三方依赖**（只 stdlib）——安装器在 venv/deps 还没装好的 **bootstrap 阶段**
也要能预览/写入 opencode 注册，所以这里不能 import `memory_agent.settings`
（它链到 python-dotenv）。

职责：

- `opencode_config_path` / `opencode_skill_dir`：`~/.config/opencode/...` 路径。
- `default_proxy_command`：注册用的命令——本仓 `venv` 解释器 + 本仓 `proxy.py`。
- `merge_opencode_config` + `write_opencode_registration`：**幂等 + 备份 + `--dry-run`**
  地写 `mcp.memory-agent`（ADR-0028 D4）；已有文件非法 JSON 时拒绝覆盖。
- `install_skill` / `skill_status`：把随包的 `skill/SKILL.md` 落位到全局 skills。

`connect.py`（第二消费者安装器）复用本模块，保留同名属性以免破坏现有调用。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time

#: opencode 全局配置目录（相对用户 home）。
OPENCODE_CONFIG_SUBDIR = os.path.join(".config", "opencode")
#: skill 落位目录名（ADR-0012）。
OPENCODE_SKILL_DIRNAME = "memory-agent"
#: opencode `local` MCP 的调用超时（毫秒）。
DEFAULT_OPENCODE_TIMEOUT_MS = 20000

#: 注册名（opencode `mcp` 与 DeepTutor `servers` 用同一个名字）。
SERVER_NAME = "memory-agent"


# --------------------------------------------------------------------- 路径

def opencode_root(home: str | None = None) -> str:
    base = os.path.abspath(os.path.expanduser(home)) if home else os.path.expanduser("~")
    return os.path.join(base, OPENCODE_CONFIG_SUBDIR)


def opencode_config_path(home: str | None = None) -> str:
    return os.path.join(opencode_root(home), "opencode.json")


def opencode_skill_dir(home: str | None = None) -> str:
    return os.path.join(opencode_root(home), "skills", OPENCODE_SKILL_DIRNAME)


def default_proxy_command() -> list[str]:
    """opencode 该注册的代理命令：优先本仓 `venv` 解释器 + 本仓 `proxy.py`。"""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    for candidate in (os.path.join(root, "venv", "Scripts", "python.exe"),
                      os.path.join(root, "venv", "bin", "python")):
        if os.path.isfile(candidate):
            return [candidate, os.path.join(here, "proxy.py")]
    return [sys.executable, os.path.join(here, "proxy.py")]


# --------------------------------------------------------------------- 注册

def build_opencode_entry() -> dict:
    """opencode `mcp.memory-agent` 的条目（type=local → 本仓 proxy.py）。"""
    return {
        "type": "local",
        "command": default_proxy_command(),
        "enabled": True,
        "timeout": DEFAULT_OPENCODE_TIMEOUT_MS,
    }


def merge_opencode_config(existing: object | None, name: str,
                          entry: dict, *, namespace: str = "mcp") -> tuple[dict, bool]:
    """把 `name` 合并进 opencode 的 `{"<namespace>": {...}}` 配置。

    保留其它 MCP 服务、其它顶层键；已有同名单条**只覆盖我们写的键**（用户额外字段
    不丢）。返回 `(新配置, changed)`——`changed=False` 表示磁盘无需改动（幂等）。
    """
    config = dict(existing) if isinstance(existing, dict) else {}
    section = config.get(namespace)
    section = dict(section) if isinstance(section, dict) else {}
    previous = section.get(name)
    merged = {**(previous if isinstance(previous, dict) else {}), **entry}
    changed = previous != merged
    section[name] = merged
    config[namespace] = section
    return config, changed


def atomic_write_json(path: str, data) -> None:
    """原子落盘（临时文件 + os.replace）——写坏会让 opencode 丢全部服务。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".", suffix=".tmp", dir=os.path.dirname(path)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def _backup_file(path: str) -> str:
    """写前备份（`<path>.bak-<UTC 时间戳>`；撞名再加序号）。"""
    base = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}"
    candidate, index = base, 1
    while os.path.exists(candidate):
        candidate = f"{base}-{index}"
        index += 1
    shutil.copy2(path, candidate)
    return candidate


def write_opencode_registration(config_path: str, *,
                                dry_run: bool = False) -> dict:
    """把 `mcp.memory-agent` 写进 opencode 配置（#52 / ADR-0028 D4）。

    - **幂等**：已在且形状一致 → `status=unchanged`，不碰文件、不备份。
    - **备份**：文件存在且需改动时，先写 `<path>.bak-<时间戳>` 再原子替换。
    - **`--dry-run` 不写文件**（`status=would-write`）。
    - 已有文件**非法 JSON 时拒绝覆盖**（`status=parse-error`），绝不吞掉用户配置。
    返回 `{status, written, backup, path, entry}`。
    """
    entry = build_opencode_entry()
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as handle:
                existing = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {"status": "parse-error", "written": False, "backup": None,
                    "path": config_path, "entry": entry}
    else:
        existing = None

    config, changed = merge_opencode_config(existing, SERVER_NAME, entry)
    if not changed:
        return {"status": "unchanged", "written": False, "backup": None,
                "path": config_path, "entry": entry}
    if dry_run:
        return {"status": "would-write", "written": False, "backup": None,
                "path": config_path, "entry": entry}

    backup = _backup_file(config_path) if os.path.isfile(config_path) else None
    atomic_write_json(config_path, config)
    return {"status": "created" if existing is None else "updated",
            "written": True, "backup": backup, "path": config_path, "entry": entry}


# --------------------------------------------------------------------- skill

def skill_status(skill_dir: str) -> bool:
    return os.path.isfile(os.path.join(skill_dir, "SKILL.md"))


def install_skill(source_dir: str, dest_dir: str, *, dry_run: bool = False) -> str:
    """把随包发布的 skill 落位到全局 skills 目录（幂等，差分才写）。

    返回 `created` / `updated` / `unchanged` / `missing-source`。
    """
    source_file = os.path.join(source_dir, "SKILL.md")
    if not os.path.isfile(source_file):
        return "missing-source"
    dest_file = os.path.join(dest_dir, "SKILL.md")
    if os.path.isfile(dest_file):
        with open(source_file, "r", encoding="utf-8") as handle:
            new_text = handle.read()
        with open(dest_file, "r", encoding="utf-8") as handle:
            if handle.read() == new_text:
                return "unchanged"
        action = "updated"
    else:
        action = "created"
    if not dry_run:
        os.makedirs(dest_dir, exist_ok=True)
        shutil.copyfile(source_file, dest_file)
    return action


def uninstall_skill(dest_dir: str, *, dry_run: bool = False) -> str:
    """移除落位的 skill 目录（幂等）。返回 `absent` / `would-remove` / `removed`。"""
    if not os.path.isdir(dest_dir):
        return "absent"
    if dry_run:
        return "would-remove"
    shutil.rmtree(dest_dir, ignore_errors=True)
    return "removed"


# --------------------------------------------------------------------- 卸载注册

def remove_opencode_registration(config_path: str, *, dry_run: bool = False) -> dict:
    """从 opencode 配置移除 `mcp.memory-agent`（幂等 + 备份 + `--dry-run`）。

    - **幂等**：文件不存在 / 没有该条目 → `status=absent`，不碰文件、不备份。
    - **备份**：文件需改动时先写 `<path>.bak-<时间戳>` 再原子替换。
    - **`--dry-run` 不写文件**（`status=would-remove`）。
    - 已有文件**非法 JSON 时拒绝改动**（`status=parse-error`），绝不吞掉用户配置。
    其它 MCP 服务与顶层键原样保留。
    返回 `{status, removed, backup, path}`。
    """
    if not os.path.isfile(config_path):
        return {"status": "absent", "removed": False, "backup": None, "path": config_path}
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            existing = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"status": "parse-error", "removed": False, "backup": None, "path": config_path}
    section = existing.get("mcp") if isinstance(existing, dict) else None
    if not isinstance(section, dict) or SERVER_NAME not in section:
        return {"status": "absent", "removed": False, "backup": None, "path": config_path}
    if dry_run:
        return {"status": "would-remove", "removed": False, "backup": None, "path": config_path}

    backup = _backup_file(config_path)
    del section[SERVER_NAME]
    atomic_write_json(config_path, existing)
    return {"status": "removed", "removed": True, "backup": backup, "path": config_path}


__all__ = [
    "OPENCODE_CONFIG_SUBDIR", "OPENCODE_SKILL_DIRNAME", "DEFAULT_OPENCODE_TIMEOUT_MS",
    "SERVER_NAME", "opencode_root", "opencode_config_path", "opencode_skill_dir",
    "default_proxy_command", "build_opencode_entry", "merge_opencode_config",
    "atomic_write_json", "write_opencode_registration", "skill_status", "install_skill",
    "uninstall_skill", "remove_opencode_registration",
]
