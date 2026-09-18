"""#52 部署纯函数单测：opencode 注册写入、路径、平台分支、install 编排（不碰 daemon）。

只覆盖可离线验证的逻辑：全部写进 tmp_path（fake opencode home），不触发 pip / 模型 /
daemon。真机端到端在 WSL final test（#53）。
"""
import json
import os
from types import SimpleNamespace

from memory_agent import cli, deploy
from memory_agent import opencode_config as oc
from memory_agent import proxy


# --------------------------------------------------------------------- 路径 / 平台

def test_venv_python_name_matches_platform():
    path = deploy.venv_python(os.path.join("X", "repo"))
    if os.name == "nt":
        assert path.endswith(os.path.join("Scripts", "python.exe"))
    else:
        assert path.endswith(os.path.join("bin", "python"))


def test_same_interpreter_false_for_missing(tmp_path):
    assert deploy._same_interpreter(str(tmp_path / "nope")) is False


def test_use_cpu_torch_precedence(monkeypatch):
    monkeypatch.setattr(deploy.sys, "platform", "linux")
    assert deploy._use_cpu_torch(SimpleNamespace(cpu_torch=None)) is True
    monkeypatch.setattr(deploy.sys, "platform", "win32")
    assert deploy._use_cpu_torch(SimpleNamespace(cpu_torch=None)) is False
    assert deploy._use_cpu_torch(SimpleNamespace(cpu_torch=True)) is True
    assert deploy._use_cpu_torch(SimpleNamespace(cpu_torch=False)) is False


def test_daemon_popen_kwargs_posix_uses_new_session():
    kwargs = proxy._daemon_popen_kwargs("posix")
    assert kwargs["start_new_session"] is True
    assert "creationflags" not in kwargs


def test_daemon_popen_kwargs_windows_uses_creationflags():
    kwargs = proxy._daemon_popen_kwargs("nt")
    assert "creationflags" in kwargs
    assert "start_new_session" not in kwargs


# --------------------------------------------------------------------- 注册合并

def test_merge_opencode_config_preserves_other_keys_and_is_idempotent():
    entry = oc.build_opencode_entry()
    existing = {
        "mcp": {"other": {"type": "remote", "url": "https://example.com"}},
        "theme": "dark",
    }
    merged, changed = oc.merge_opencode_config(existing, oc.SERVER_NAME, entry)
    assert changed is True
    assert merged["theme"] == "dark"
    assert merged["mcp"]["other"] == existing["mcp"]["other"]
    assert merged["mcp"][oc.SERVER_NAME]["type"] == "local"
    assert any(part.endswith("proxy.py") for part in merged["mcp"][oc.SERVER_NAME]["command"])

    again, changed_again = oc.merge_opencode_config(merged, oc.SERVER_NAME, entry)
    assert changed_again is False
    assert again == merged


def test_merge_opencode_config_custom_namespace():
    merged, changed = oc.merge_opencode_config(None, "memory-agent", {"x": 1},
                                               namespace="servers")
    assert changed is True
    assert merged == {"servers": {"memory-agent": {"x": 1}}}


# --------------------------------------------------------------------- 写注册文件

def test_write_opencode_registration_creates(tmp_path):
    path = str(tmp_path / ".config" / "opencode" / "opencode.json")
    result = oc.write_opencode_registration(path)
    assert result["status"] == "created" and result["written"] is True
    assert result["backup"] is None
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["mcp"][oc.SERVER_NAME]["enabled"] is True


def test_write_opencode_registration_dry_run_writes_nothing(tmp_path):
    path = str(tmp_path / ".config" / "opencode" / "opencode.json")
    result = oc.write_opencode_registration(path, dry_run=True)
    assert result["status"] == "would-write"
    assert result["written"] is False
    assert not os.path.exists(path)


def test_write_opencode_registration_idempotent(tmp_path):
    path = str(tmp_path / "opencode.json")
    oc.write_opencode_registration(path)
    before = open(path, encoding="utf-8").read()
    result = oc.write_opencode_registration(path)
    assert result["status"] == "unchanged"
    assert open(path, encoding="utf-8").read() == before


def test_write_opencode_registration_backs_up_on_update(tmp_path):
    path = str(tmp_path / "opencode.json")
    stale = {"mcp": {oc.SERVER_NAME: {"type": "local", "command": ["python", "old.py"],
                                      "enabled": True}},
             "keep": {"me": True}}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(stale, handle)

    result = oc.write_opencode_registration(path)
    assert result["status"] == "updated"
    assert result["backup"] and os.path.isfile(result["backup"])
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["keep"] == {"me": True}
    assert any(part.endswith("proxy.py") for part in data["mcp"][oc.SERVER_NAME]["command"])


def test_write_opencode_registration_refuses_invalid_json(tmp_path):
    path = tmp_path / "opencode.json"
    path.write_text("{not json", encoding="utf-8")
    result = oc.write_opencode_registration(str(path))
    assert result["status"] == "parse-error"
    assert result["written"] is False
    assert path.read_text(encoding="utf-8") == "{not json"


def test_paths_under_home(tmp_path):
    home = str(tmp_path / "home")
    assert oc.opencode_config_path(home) == os.path.join(
        home, ".config", "opencode", "opencode.json")
    assert oc.opencode_skill_dir(home) == os.path.join(
        home, ".config", "opencode", "skills", "memory-agent")


def test_skill_install_roundtrip(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "SKILL.md").write_text("hello", encoding="utf-8")
    dest = str(tmp_path / "dest")
    assert oc.install_skill(str(source), dest, dry_run=True) == "created"
    assert not os.path.exists(dest)
    assert oc.install_skill(str(source), dest) == "created"
    assert oc.skill_status(dest) is True
    assert oc.install_skill(str(source), dest) == "unchanged"


# --------------------------------------------------------------------- install 编排

def test_cli_install_dry_run_is_nondestructive(tmp_path):
    home = str(tmp_path / "home")
    rc = cli.main([
        "install", "--dry-run", "--skip-deps", "--no-index", "--no-daemon",
        "--no-smoke", "--opencode-home", home, "--repo", deploy.REPO_ROOT,
    ])
    assert rc == 0
    assert not os.path.exists(os.path.join(home, ".config", "opencode", "opencode.json"))


def test_cli_install_writes_opencode_and_skill(tmp_path):
    home = str(tmp_path / "home")
    rc = cli.main([
        "install", "--skip-deps", "--no-index", "--no-daemon",
        "--no-smoke", "--opencode-home", home, "--repo", deploy.REPO_ROOT,
    ])
    assert rc == 0
    config = os.path.join(home, ".config", "opencode", "opencode.json")
    data = json.loads(open(config, encoding="utf-8").read())
    assert data["mcp"][oc.SERVER_NAME]["type"] == "local"
    skill = os.path.join(home, ".config", "opencode", "skills", "memory-agent", "SKILL.md")
    assert os.path.isfile(skill)


def test_cli_install_rejects_non_repo(tmp_path):
    rc = cli.main([
        "install", "--dry-run", "--skip-deps", "--no-index", "--no-daemon",
        "--no-smoke", "--opencode-home", str(tmp_path / "home"),
        "--repo", str(tmp_path),
    ])
    assert rc == 2
