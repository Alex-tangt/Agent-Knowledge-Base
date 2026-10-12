"""Optional Windows logon startup owned by one memory-base state home.

Only the current user's named Run value is managed. The startup command carries
the installation home, never credentials, and uses its existing pythonw runtime.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_SZ = 1
# https://learn.microsoft.com/en-us/windows/win32/setupapi/run-and-runonce-registry-keys
MAX_COMMAND_CHARS = 260


class _WindowsRegistry:
    def __init__(self):
        import winreg
        self.api = winreg

    def read(self, name: str) -> tuple[str, int] | None:
        try:
            with self.api.OpenKey(self.api.HKEY_CURRENT_USER, RUN_KEY, 0,
                                  self.api.KEY_QUERY_VALUE) as key:
                return self.api.QueryValueEx(key, name)
        except FileNotFoundError:
            return None

    def write(self, name: str, command: str) -> None:
        with self.api.CreateKeyEx(self.api.HKEY_CURRENT_USER, RUN_KEY, 0,
                                 self.api.KEY_SET_VALUE) as key:
            self.api.SetValueEx(key, name, 0, self.api.REG_SZ, command)

    def delete(self, name: str) -> None:
        with self.api.OpenKey(self.api.HKEY_CURRENT_USER, RUN_KEY, 0,
                              self.api.KEY_SET_VALUE) as key:
            self.api.DeleteValue(key, name)


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Automatic startup is supported only on Windows")


def _registry():
    return _WindowsRegistry()


def _home(home: str | Path) -> Path:
    return Path(home).expanduser().resolve()


def _name(home: Path) -> str:
    namespace = hashlib.sha256(str(home).casefold().encode("utf-8")).hexdigest()[:16]
    return "AgentMemoryBase-" + namespace


def _configured_python(home: Path) -> Path:
    try:
        config = json.loads((home / "deployment.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError("Cannot verify automatic startup without a valid deployment.json") from error
    value = config.get("python") if isinstance(config, dict) else None
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise RuntimeError("deployment.json must identify the installed runtime Python")
    python = Path(value).resolve()
    if python.name.casefold() != "python.exe":
        raise RuntimeError("Automatic startup requires an installed Windows python.exe runtime")
    return python


def _command(home: Path, python: Path) -> str:
    return subprocess.list2cmdline([str(python.with_name("pythonw.exe")), "-m",
                                   "memory_base", "start", "--home", str(home)])


def _command_too_long(command: str) -> bool:
    return len(command.encode("utf-16-le")) // 2 > MAX_COMMAND_CHARS


def _inspect(home: Path, registry, python: Path | None = None) -> dict:
    name = _name(home)
    existing = registry.read(name)
    result = {"supported": True, "home": str(home), "name": name, "command": None,
              "enabled": False, "conflict": existing is not None,
              "runtime_available": False, "changed": False}
    try:
        python = python or _configured_python(home)
    except RuntimeError as error:
        result["error"] = str(error)
        return result
    command = _command(home, python)
    result["command"] = command
    result["runtime_available"] = python.is_file() and python.with_name("pythonw.exe").is_file()
    result["enabled"] = existing == (command, REG_SZ)
    result["conflict"] = existing is not None and not result["enabled"]
    if _command_too_long(command):
        result["error"] = "Automatic startup command exceeds the Windows Run limit of 260 characters"
    elif not result["runtime_available"]:
        result["error"] = "Installed runtime python.exe or pythonw.exe is missing"
    return result


def status(home: str | Path) -> dict:
    """Report the owned registration and missing runtime without mutating it."""
    _require_windows()
    return _inspect(_home(home), _registry())


def enable(home: str | Path, python: str | Path) -> dict:
    """Enable logon startup; a differently owned same-name value is a conflict."""
    _require_windows()
    home = _home(home)
    expected = _configured_python(home)
    if str(Path(python).expanduser().resolve()).casefold() != str(expected).casefold():
        raise RuntimeError("Selected Python does not match the installed runtime in deployment.json")
    if not expected.is_file() or not expected.with_name("pythonw.exe").is_file():
        raise RuntimeError("Installed runtime python.exe or pythonw.exe is missing")
    registry = _registry()
    result = _inspect(home, registry, expected)
    if _command_too_long(result["command"]):
        raise RuntimeError(result["error"])
    if result["conflict"]:
        raise RuntimeError("Automatic startup value conflicts with another command; it was not modified")
    if not result["enabled"]:
        registry.write(result["name"], result["command"])
        result.update(enabled=True, changed=True)
    return result


def disable(home: str | Path) -> dict:
    """Remove only an exactly owned command, even after runtime files disappear."""
    _require_windows()
    registry = _registry()
    result = _inspect(_home(home), registry)
    if result["conflict"]:
        raise RuntimeError("Cannot prove ownership of automatic startup value; it was not modified")
    if result["enabled"]:
        registry.delete(result["name"])
        result.update(enabled=False, changed=True)
    return result
