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
    assert deploy._use_cpu_torch(SimpleNamespace(cpu_torch=None)) is True
    monkeypatch.setattr(deploy.sys, "platform", "darwin")
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
    assert oc.opencode_agent_dir(home) == os.path.join(
        home, ".config", "opencode", "agents")
    assert oc.opencode_plugin_dir(home) == os.path.join(
        home, ".config", "opencode", "plugins")
    assert oc.opencode_package_json(home) == os.path.join(
        home, ".config", "opencode", "package.json")


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


# --------------------------------------------------------------------- agent (#60)

def test_install_agent_roundtrip(tmp_path):
    source = tmp_path / "memory-research.md"
    source.write_text("---\nmode: subagent\n---\nhi", encoding="utf-8")
    dest = str(tmp_path / "agents" / "memory-research.md")
    assert oc.install_agent(str(source), dest, dry_run=True) == "created"
    assert not os.path.exists(dest)
    assert oc.install_agent(str(source), dest) == "created"
    assert oc.agent_status(dest) is True
    assert oc.install_agent(str(source), dest) == "unchanged"
    source.write_text("changed", encoding="utf-8")
    assert oc.install_agent(str(source), dest) == "updated"
    assert open(dest, encoding="utf-8").read() == "changed"


def test_install_agent_missing_source(tmp_path):
    assert oc.install_agent(
        str(tmp_path / "nope.md"), str(tmp_path / "dest.md")) == "missing-source"


def test_uninstall_agent_roundtrip(tmp_path):
    dest = str(tmp_path / "agents" / "memory-research.md")
    assert oc.uninstall_agent(dest) == "absent"
    os.makedirs(os.path.dirname(dest))
    with open(dest, "w", encoding="utf-8") as handle:
        handle.write("x")
    assert oc.uninstall_agent(dest, dry_run=True) == "would-remove"
    assert os.path.isfile(dest)
    assert oc.uninstall_agent(dest) == "removed"
    assert not os.path.exists(dest)


# --------------------------------------------------------------------- plugin (#61)

def test_install_plugin_roundtrip(tmp_path):
    source = tmp_path / "memory-research.js"
    source.write_text("export const P = async () => ({ tool: {} })", encoding="utf-8")
    dest = str(tmp_path / "plugins" / "memory-research.js")
    assert oc.install_plugin(str(source), dest, dry_run=True) == "created"
    assert not os.path.exists(dest)
    assert oc.install_plugin(str(source), dest) == "created"
    assert oc.plugin_status(dest) is True
    assert oc.install_plugin(str(source), dest) == "unchanged"
    source.write_text("changed", encoding="utf-8")
    assert oc.install_plugin(str(source), dest) == "updated"
    assert open(dest, encoding="utf-8").read() == "changed"


def test_install_plugin_missing_source(tmp_path):
    assert oc.install_plugin(
        str(tmp_path / "nope.js"), str(tmp_path / "dest.js")) == "missing-source"


def test_uninstall_plugin_roundtrip(tmp_path):
    dest = str(tmp_path / "plugins" / "memory-research.js")
    assert oc.uninstall_plugin(dest) == "absent"
    os.makedirs(os.path.dirname(dest))
    with open(dest, "w", encoding="utf-8") as handle:
        handle.write("x")
    assert oc.uninstall_plugin(dest, dry_run=True) == "would-remove"
    assert os.path.isfile(dest)
    assert oc.uninstall_plugin(dest) == "removed"
    assert not os.path.exists(dest)


def test_merge_package_dependency_adds_but_never_overwrites():
    existing = {"dependencies": {"zod": "^3"}, "name": "opencode-config"}
    merged, changed = oc.merge_package_dependency(existing, oc.PLUGIN_DEP_NAME, "^1.18.0")
    assert changed is True
    assert merged["name"] == "opencode-config"
    assert merged["dependencies"]["zod"] == "^3"
    assert merged["dependencies"][oc.PLUGIN_DEP_NAME] == "^1.18.0"
    # 已存在（哪怕版本不同）→ 不覆盖、视为无需改动
    theirs = {"dependencies": {oc.PLUGIN_DEP_NAME: "1.17.18"}}
    kept, changed2 = oc.merge_package_dependency(theirs, oc.PLUGIN_DEP_NAME, "^1.18.0")
    assert changed2 is False
    assert kept["dependencies"][oc.PLUGIN_DEP_NAME] == "1.17.18"
    # 同 spec 重复合并 → 幂等
    again, changed_again = oc.merge_package_dependency(merged, oc.PLUGIN_DEP_NAME, "^1.18.0")
    assert changed_again is False
    assert again == merged


def test_write_package_dependency_creates_then_keeps_existing(tmp_path):
    path = str(tmp_path / "package.json")
    result = oc.write_package_dependency(path, oc.PLUGIN_DEP_NAME, "^1.18.0")
    assert result["status"] == "created" and result["written"] is True
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["dependencies"][oc.PLUGIN_DEP_NAME] == "^1.18.0"
    # 再写（不同 spec）→ 不覆盖、unchanged
    result2 = oc.write_package_dependency(path, oc.PLUGIN_DEP_NAME, "^2.0.0")
    assert result2["status"] == "unchanged" and result2["written"] is False
    assert json.loads(open(path, encoding="utf-8").read())[
        "dependencies"][oc.PLUGIN_DEP_NAME] == "^1.18.0"
    # 预存别的依赖 + 缺本依赖 → updated（补上、并备份）
    other = tmp_path / "other.json"
    with open(other, "w", encoding="utf-8") as handle:
        json.dump({"dependencies": {"zod": "^3"}}, handle)
    result3 = oc.write_package_dependency(str(other), oc.PLUGIN_DEP_NAME, "^1.18.0")
    assert result3["status"] == "updated" and result3["backup"]
    data3 = json.loads(open(other, encoding="utf-8").read())
    assert data3["dependencies"]["zod"] == "^3"
    assert data3["dependencies"][oc.PLUGIN_DEP_NAME] == "^1.18.0"


def test_write_package_dependency_dry_run_and_parse_error(tmp_path):
    path = str(tmp_path / "package.json")
    assert oc.write_package_dependency(
        path, oc.PLUGIN_DEP_NAME, "^1.18.0", dry_run=True)["status"] == "would-write"
    assert not os.path.exists(path)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert oc.write_package_dependency(
        str(bad), oc.PLUGIN_DEP_NAME, "^1.18.0")["status"] == "parse-error"
    assert bad.read_text(encoding="utf-8") == "{not json"


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


def test_cli_install_writes_agent(tmp_path):
    home = str(tmp_path / "home")
    rc = cli.main([
        "install", "--skip-deps", "--no-index", "--no-daemon",
        "--no-smoke", "--opencode-home", home, "--repo", deploy.REPO_ROOT,
    ])
    assert rc == 0
    agent = os.path.join(home, ".config", "opencode", "agents", "memory-research.md")
    assert os.path.isfile(agent)
    text = open(agent, encoding="utf-8").read()
    assert "mode: subagent" in text
    assert "model:" not in text  # 默认继承：不写 model


def test_cli_install_no_agent_flag(tmp_path):
    home = str(tmp_path / "home")
    rc = cli.main([
        "install", "--skip-deps", "--no-index", "--no-daemon", "--no-smoke",
        "--no-agent", "--opencode-home", home, "--repo", deploy.REPO_ROOT,
    ])
    assert rc == 0
    assert not os.path.exists(
        os.path.join(home, ".config", "opencode", "agents", "memory-research.md"))


def test_cli_install_writes_plugin_and_dep(tmp_path):
    home = str(tmp_path / "home")
    rc = cli.main([
        "install", "--skip-deps", "--no-index", "--no-daemon",
        "--no-smoke", "--opencode-home", home, "--repo", deploy.REPO_ROOT,
    ])
    assert rc == 0
    plugin = os.path.join(home, ".config", "opencode", "plugins", "memory-research.js")
    assert os.path.isfile(plugin)
    assert "memory_research" in open(plugin, encoding="utf-8").read()
    pkg = json.loads(open(os.path.join(home, ".config", "opencode", "package.json"),
                          encoding="utf-8").read())
    assert oc.PLUGIN_DEP_NAME in pkg["dependencies"]


def test_cli_install_no_plugin_flag(tmp_path):
    home = str(tmp_path / "home")
    rc = cli.main([
        "install", "--skip-deps", "--no-index", "--no-daemon", "--no-smoke",
        "--no-plugin", "--opencode-home", home, "--repo", deploy.REPO_ROOT,
    ])
    assert rc == 0
    assert not os.path.exists(
        os.path.join(home, ".config", "opencode", "plugins", "memory-research.js"))
    assert not os.path.exists(
        os.path.join(home, ".config", "opencode", "package.json"))


def test_cli_uninstall_removes_plugin_keeps_shared_dep(tmp_path):
    home = str(tmp_path / "home")
    assert cli.main([
        "install", "--skip-deps", "--no-index", "--no-daemon", "--no-smoke",
        "--opencode-home", home, "--repo", deploy.REPO_ROOT,
    ]) == 0
    plugin = os.path.join(home, ".config", "opencode", "plugins", "memory-research.js")
    pkg = os.path.join(home, ".config", "opencode", "package.json")
    assert os.path.isfile(plugin) and os.path.isfile(pkg)
    assert cli.main([
        "uninstall", "--no-daemon", "--opencode-home", home, "--repo", deploy.REPO_ROOT,
    ]) == 0
    assert not os.path.exists(plugin)
    # 依赖是共享的：卸载保留、且内容不变
    assert os.path.isfile(pkg)
    assert oc.PLUGIN_DEP_NAME in json.loads(
        open(pkg, encoding="utf-8").read())["dependencies"]


def test_cli_install_rejects_non_repo(tmp_path):
    rc = cli.main([
        "install", "--dry-run", "--skip-deps", "--no-index", "--no-daemon",
        "--no-smoke", "--opencode-home", str(tmp_path / "home"),
        "--repo", str(tmp_path),
    ])
    assert rc == 2


# --------------------------------------------------------------------- 卸载

def test_remove_opencode_registration_absent(tmp_path):
    path = str(tmp_path / "opencode.json")
    result = oc.remove_opencode_registration(path)
    assert result["status"] == "absent" and result["removed"] is False


def test_remove_opencode_registration_removes_and_keeps_others(tmp_path):
    path = str(tmp_path / "opencode.json")
    data = {"mcp": {oc.SERVER_NAME: {"type": "local"}, "other": {"type": "remote"}},
            "theme": "dark"}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)

    result = oc.remove_opencode_registration(path)
    assert result["status"] == "removed" and result["removed"] is True
    assert result["backup"] and os.path.isfile(result["backup"])
    after = json.loads(open(path, encoding="utf-8").read())
    assert oc.SERVER_NAME not in after["mcp"]
    assert after["mcp"]["other"] == {"type": "remote"}
    assert after["theme"] == "dark"
    assert oc.remove_opencode_registration(path)["status"] == "absent"


def test_remove_opencode_registration_dry_run_writes_nothing(tmp_path):
    path = str(tmp_path / "opencode.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"mcp": {oc.SERVER_NAME: {"type": "local"}}}, handle)
    before = open(path, encoding="utf-8").read()
    result = oc.remove_opencode_registration(path, dry_run=True)
    assert result["status"] == "would-remove" and result["removed"] is False
    assert open(path, encoding="utf-8").read() == before


def test_remove_opencode_registration_refuses_invalid_json(tmp_path):
    path = tmp_path / "opencode.json"
    path.write_text("{not json", encoding="utf-8")
    result = oc.remove_opencode_registration(str(path))
    assert result["status"] == "parse-error" and result["removed"] is False
    assert path.read_text(encoding="utf-8") == "{not json"


def test_uninstall_skill_roundtrip(tmp_path):
    dest = str(tmp_path / "skills" / "memory-agent")
    assert oc.uninstall_skill(dest) == "absent"
    os.makedirs(dest)
    with open(os.path.join(dest, "SKILL.md"), "w", encoding="utf-8") as handle:
        handle.write("x")
    assert oc.uninstall_skill(dest, dry_run=True) == "would-remove"
    assert os.path.isdir(dest)
    assert oc.uninstall_skill(dest) == "removed"
    assert not os.path.exists(dest)


def test_cli_uninstall_removes_registration_and_skill(tmp_path):
    home = str(tmp_path / "home")
    assert cli.main([
        "install", "--skip-deps", "--no-index", "--no-daemon", "--no-smoke",
        "--opencode-home", home, "--repo", deploy.REPO_ROOT,
    ]) == 0
    config = os.path.join(home, ".config", "opencode", "opencode.json")
    assert oc.SERVER_NAME in json.loads(open(config, encoding="utf-8").read())["mcp"]

    assert cli.main([
        "uninstall", "--dry-run", "--no-daemon", "--opencode-home", home,
        "--repo", deploy.REPO_ROOT,
    ]) == 0
    assert oc.SERVER_NAME in json.loads(open(config, encoding="utf-8").read())["mcp"]

    assert cli.main([
        "uninstall", "--no-daemon", "--opencode-home", home, "--repo", deploy.REPO_ROOT,
    ]) == 0
    assert oc.SERVER_NAME not in json.loads(open(config, encoding="utf-8").read())["mcp"]
    skill = os.path.join(home, ".config", "opencode", "skills", "memory-agent")
    assert not os.path.exists(skill)

    assert cli.main([
        "uninstall", "--no-daemon", "--opencode-home", home, "--repo", deploy.REPO_ROOT,
    ]) == 0


def test_cli_uninstall_removes_agent(tmp_path):
    home = str(tmp_path / "home")
    assert cli.main([
        "install", "--skip-deps", "--no-index", "--no-daemon", "--no-smoke",
        "--opencode-home", home, "--repo", deploy.REPO_ROOT,
    ]) == 0
    agent = os.path.join(home, ".config", "opencode", "agents", "memory-research.md")
    assert os.path.isfile(agent)

    assert cli.main([
        "uninstall", "--dry-run", "--no-daemon", "--opencode-home", home,
        "--repo", deploy.REPO_ROOT,
    ]) == 0
    assert os.path.isfile(agent)

    assert cli.main([
        "uninstall", "--no-daemon", "--opencode-home", home, "--repo", deploy.REPO_ROOT,
    ]) == 0
    assert not os.path.exists(agent)


def test_cli_uninstall_all_steps_skipped():
    assert cli.main(["uninstall", "--dry-run", "--no-daemon",
                     "--no-register", "--no-skill", "--repo", deploy.REPO_ROOT]) == 0
