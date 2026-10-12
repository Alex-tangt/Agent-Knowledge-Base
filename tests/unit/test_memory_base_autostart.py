"""Logon startup ownership and quoting, with no real registry modifications."""

import ctypes
import json
from pathlib import Path
import sys

import pytest

from memory_base import autostart


@pytest.fixture
def tmp_path(tmp_path_factory):
    # Run commands repeat the home path; keep ordinary test homes within its
    # real 260-character limit regardless of the test function's long name.
    return tmp_path_factory.mktemp("autostart")


class FakeRegistry:
    def __init__(self):
        self.values = {"OtherApplication": ("other.exe --keep", autostart.REG_SZ)}
        self.mutations = []

    def read(self, name):
        return self.values.get(name)

    def write(self, name, command):
        self.values[name] = (command, autostart.REG_SZ)
        self.mutations.append(("write", name))

    def delete(self, name):
        del self.values[name]
        self.mutations.append(("delete", name))


@pytest.fixture
def registry(monkeypatch):
    registry = FakeRegistry()
    monkeypatch.setattr(autostart, "_require_windows", lambda: None)
    monkeypatch.setattr(autostart, "_registry", lambda: registry)
    return registry


def installed(home: Path, runtime: Path | None = None) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    python = (runtime or home / "runtime" / "Scripts") / "python.exe"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"fake-python")
    python.with_name("pythonw.exe").write_bytes(b"fake-pythonw")
    (home / "deployment.json").write_text(json.dumps({"python": str(python.resolve())}), encoding="utf-8")
    return python


def test_enable_disable_are_idempotent_and_preserve_other_values(tmp_path, registry):
    python = installed(tmp_path)
    first = autostart.enable(tmp_path, python)
    assert first["enabled"] and first["changed"] and not first["conflict"]
    assert first["runtime_available"]
    assert autostart.enable(tmp_path, python) == {**first, "changed": False}
    assert autostart.status(tmp_path) == {**first, "changed": False}
    disabled = autostart.disable(tmp_path)
    assert not disabled["enabled"] and disabled["changed"]
    assert autostart.disable(tmp_path) == {**disabled, "changed": False}
    assert registry.values == {"OtherApplication": ("other.exe --keep", autostart.REG_SZ)}
    assert registry.mutations == [("write", first["name"]), ("delete", first["name"])]


def test_two_homes_with_shared_runtime_have_separate_names_and_commands(tmp_path, registry):
    first_home, second_home = tmp_path / "first", tmp_path / "second"
    shared = tmp_path / "shared runtime" / "Scripts"
    python = installed(first_home, shared)
    installed(second_home, shared)
    first = autostart.enable(first_home, python)
    second = autostart.enable(second_home, python)
    assert first["name"] != second["name"]
    assert first["command"] != second["command"]
    autostart.disable(first_home)
    assert autostart.status(second_home)["enabled"]
    assert second["name"] in registry.values


def test_equivalent_home_paths_use_the_same_registration(tmp_path, registry):
    python = installed(tmp_path)
    first = autostart.enable(tmp_path, python)
    second = autostart.enable(tmp_path / "runtime" / "..", python)
    assert second["name"] == first["name"]
    assert not second["changed"]


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows command-line parser")
def test_spaces_and_unicode_roundtrip_through_windows_parser(tmp_path, registry):
    home = tmp_path / "个人 memory home"
    python = installed(home)
    command = autostart.enable(home, python)["command"]
    parser = ctypes.WinDLL("shell32", use_last_error=True).CommandLineToArgvW
    parser.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    parser.restype = ctypes.POINTER(ctypes.c_wchar_p)
    count = ctypes.c_int()
    argv = parser(command, ctypes.byref(count))
    assert argv
    try:
        assert [argv[i] for i in range(count.value)] == [str(python.with_name("pythonw.exe").resolve()),
                                                      "-m", "memory_base", "start", "--home", str(home.resolve())]
    finally:
        free = ctypes.WinDLL("kernel32").LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(argv)


@pytest.mark.parametrize("entry", [("unrelated.exe --credentials secret", 1), ("anything", 2)])
def test_same_name_foreign_command_or_type_is_not_overwritten_or_deleted(tmp_path, registry, entry):
    python = installed(tmp_path)
    name = autostart.status(tmp_path)["name"]
    registry.values[name] = entry
    state = autostart.status(tmp_path)
    assert state["conflict"] and not state["enabled"]
    assert entry[0] not in json.dumps(state)
    with pytest.raises(RuntimeError, match="conflicts"):
        autostart.enable(tmp_path, python)
    with pytest.raises(RuntimeError, match="ownership"):
        autostart.disable(tmp_path)
    assert registry.values[name] == entry
    assert not registry.mutations


def test_enable_rejects_python_from_another_installation(tmp_path, registry):
    expected = installed(tmp_path / "one")
    other = installed(tmp_path / "two")
    with pytest.raises(RuntimeError, match="does not match"):
        autostart.enable(tmp_path / "one", other)
    assert not registry.mutations
    assert expected.exists()


@pytest.mark.parametrize("filename", ["python.exe", "pythonw.exe"])
def test_enable_rejects_missing_runtime_file(tmp_path, registry, filename):
    python = installed(tmp_path)
    python.with_name(filename).unlink()
    with pytest.raises(RuntimeError, match="missing"):
        autostart.enable(tmp_path, python)
    assert not registry.mutations
    assert not autostart.status(tmp_path)["runtime_available"]


def test_owned_registration_can_be_removed_after_runtime_disappears(tmp_path, registry):
    python = installed(tmp_path)
    registration = autostart.enable(tmp_path, python)
    python.unlink()
    python.with_name("pythonw.exe").unlink()
    state = autostart.status(tmp_path)
    assert state["enabled"] and not state["runtime_available"] and "missing" in state["error"]
    assert not state["conflict"]
    assert autostart.disable(tmp_path)["changed"]
    assert registration["name"] not in registry.values


@pytest.mark.parametrize("config", [None, "{malformed", '"wrong-shape"', '{"python":"relative/python.exe"}'])
def test_unverifiable_installation_reports_existing_value_and_refuses_removal(tmp_path, registry, config):
    python = installed(tmp_path)
    registration = autostart.enable(tmp_path, python)
    path = tmp_path / "deployment.json"
    if config is None:
        path.unlink()
    else:
        path.write_text(config, encoding="utf-8")
    state = autostart.status(tmp_path)
    assert state["conflict"] and not state["enabled"] and state["error"]
    assert state["command"] is None
    with pytest.raises(RuntimeError, match="ownership"):
        autostart.disable(tmp_path)
    assert registration["name"] in registry.values
    assert len(registry.mutations) == 1


def test_disable_missing_entry_is_idempotent_even_without_installation(tmp_path, registry):
    assert not autostart.disable(tmp_path)["changed"]
    assert not registry.mutations


def test_too_long_command_is_rejected_before_registration(tmp_path, registry):
    home = tmp_path / ("long-home-" * 10)
    python = installed(home)
    with pytest.raises(RuntimeError, match="260 characters"):
        autostart.enable(home, python)
    assert not registry.mutations
    assert "260 characters" in autostart.status(home)["error"]


@pytest.mark.parametrize("operation", ["status", "enable", "disable"])
def test_non_windows_is_explicitly_unsupported_without_opening_registry(tmp_path, monkeypatch, operation):
    monkeypatch.setattr(autostart.sys, "platform", "linux")
    monkeypatch.setattr(autostart, "_registry", lambda: pytest.fail("must not open registry"))
    with pytest.raises(RuntimeError, match="only on Windows"):
        if operation == "enable":
            autostart.enable(tmp_path, tmp_path / "python.exe")
        else:
            getattr(autostart, operation)(tmp_path)
