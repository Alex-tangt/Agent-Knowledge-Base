"""#41 一步安装（connect）纯函数单测：路径解析 / 幂等合并 / 条目形状 / 核验。

不写用户配置、不起 daemon、不碰真实 DeepTutor——只覆盖可独立验证的纯逻辑。
"""
import json
import os

from memory_agent import connect


# --------------------------------------------------------------------- 路径解析

def test_resolve_deeptutor_home_precedence(tmp_path):
    env = {"DEEPTUTOR_HOME": str(tmp_path / "env-home")}
    explicit = str(tmp_path / "explicit-home")

    assert connect.resolve_deeptutor_home(explicit, env, cwd=str(tmp_path)) == \
        os.path.abspath(explicit)
    assert connect.resolve_deeptutor_home(None, env, cwd=str(tmp_path)) == \
        os.path.abspath(str(tmp_path / "env-home"))
    assert connect.resolve_deeptutor_home(
        None, {}, cwd=str(tmp_path)) == os.path.abspath(str(tmp_path))


def test_resolve_deeptutor_home_blank_env_falls_back_to_cwd(tmp_path):
    assert connect.resolve_deeptutor_home(
        "  ", {"DEEPTUTOR_HOME": "  "}, cwd=str(tmp_path)) == os.path.abspath(str(tmp_path))


def test_deeptutor_mcp_config_path(tmp_path):
    path = connect.deeptutor_mcp_config_path(str(tmp_path))
    assert path == os.path.join(str(tmp_path), "data", "user", "settings", "mcp.json")


def test_daemon_url_forces_leading_slash():
    assert connect.daemon_url("127.0.0.1", 8765, "mcp") == "http://127.0.0.1:8765/mcp"
    assert connect.daemon_url("127.0.0.1", 8765, "/mcp") == "http://127.0.0.1:8765/mcp"


# --------------------------------------------------------------------- 条目形状

def test_build_server_entry_is_readonly_streamablehttp():
    entry = connect.build_server_entry("http://127.0.0.1:8765/mcp")
    assert entry["type"] == "streamableHttp"
    assert entry["url"] == "http://127.0.0.1:8765/mcp"
    assert entry["enabled"] is True
    assert entry["enabled_tools"] == list(connect.READONLY_TOOLS)
    # 只读边界（D17）：写 / 维护工具不得进入白名单。
    for forbidden in ("memory_add", "memory_supersede", "memory_archive", "memory_reindex"):
        assert forbidden not in entry["enabled_tools"]


# --------------------------------------------------------------------- 幂等合并

def test_merge_mcp_config_preserves_other_servers_and_keys():
    existing = {
        "servers": {"other": {"type": "sse", "url": "https://example.com/sse"}},
        "some_other_key": {"keep": True},
    }
    entry = connect.build_server_entry("http://127.0.0.1:8765/mcp")
    merged, changed = connect.merge_mcp_config(existing, "memory-agent", entry)

    assert changed is True
    assert merged["servers"]["other"] == existing["servers"]["other"]
    assert merged["some_other_key"] == {"keep": True}
    assert merged["servers"]["memory-agent"] == entry


def test_merge_mcp_config_is_idempotent():
    entry = connect.build_server_entry("http://127.0.0.1:8765/mcp")
    first, changed_first = connect.merge_mcp_config(None, "memory-agent", entry)
    assert changed_first is True

    second, changed_second = connect.merge_mcp_config(first, "memory-agent", entry)
    assert changed_second is False
    assert second == first


def test_merge_mcp_config_keeps_user_extras_and_skips_rewrite():
    entry = connect.build_server_entry("http://127.0.0.1:8765/mcp")
    existing = {"servers": {"memory-agent": {**entry, "catalog_entry": "custom"}}}
    merged, changed = connect.merge_mcp_config(existing, "memory-agent", entry)

    assert changed is False
    assert merged["servers"]["memory-agent"]["catalog_entry"] == "custom"


def test_merge_mcp_config_overrides_changed_url():
    entry = connect.build_server_entry("http://127.0.0.1:9999/mcp")
    existing = {"servers": {"memory-agent": connect.build_server_entry(
        "http://127.0.0.1:8765/mcp")}}
    merged, changed = connect.merge_mcp_config(existing, "memory-agent", entry)

    assert changed is True
    assert merged["servers"]["memory-agent"]["url"] == "http://127.0.0.1:9999/mcp"


def test_merge_mcp_config_ignores_non_dict_existing():
    merged, changed = connect.merge_mcp_config(["not", "a", "dict"], "memory-agent",
                                               {"type": "streamableHttp"})
    assert changed is True
    assert merged == {"servers": {"memory-agent": {"type": "streamableHttp"}}}


# --------------------------------------------------------------------- 读写

def test_read_json_object_missing_and_invalid(tmp_path):
    assert connect.read_json_object(str(tmp_path / "nope.json")) is None

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert connect.read_json_object(str(bad)) is None


def test_atomic_write_json_roundtrip(tmp_path):
    path = str(tmp_path / "nested" / "mcp.json")
    payload = {"servers": {"memory-agent": {"type": "streamableHttp"}}}
    connect.atomic_write_json(path, payload)

    assert json.loads(open(path, encoding="utf-8").read()) == payload
    assert not [n for n in os.listdir(os.path.dirname(path)) if n.endswith(".tmp")]


# --------------------------------------------------------------------- 核验

def test_opencode_registration_detects_proxy(tmp_path):
    good = {"mcp": {connect.SERVER_NAME: {
        "type": "local", "command": ["python", "D:/x/proxy.py"],
        "enabled": True, "timeout": 20000,
    }}}
    result = connect.opencode_registration(good)
    assert result["ok"] is True

    assert connect.opencode_registration(None)["ok"] is False
    assert connect.opencode_registration({})["ok"] is False
    assert connect.opencode_registration({"mcp": {connect.SERVER_NAME: {
        "type": "local", "command": ["python", "server.py"], "enabled": True,
    }}})["ok"] is False
    assert connect.opencode_registration({"mcp": {connect.SERVER_NAME: {
        "type": "local", "command": ["python", "proxy.py"], "enabled": False,
    }}})["ok"] is False


def test_opencode_patch_shape_is_mergeable():
    result = connect.opencode_registration(None)
    entry = result["patch"]["mcp"][connect.SERVER_NAME]
    assert entry["type"] == "local"
    assert any(part.endswith("proxy.py") for part in entry["command"])
    assert entry["enabled"] is True


def test_skill_status_and_install(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "SKILL.md").write_text("hello", encoding="utf-8")
    dest = tmp_path / "dest"

    assert connect.skill_status(str(dest)) is False
    assert connect.install_skill(str(source), str(dest), dry_run=True) == "created"
    assert connect.skill_status(str(dest)) is False  # dry-run 不落盘

    assert connect.install_skill(str(source), str(dest)) == "created"
    assert connect.skill_status(str(dest)) is True
    assert connect.install_skill(str(source), str(dest)) == "unchanged"

    (source / "SKILL.md").write_text("hello v2", encoding="utf-8")
    assert connect.install_skill(str(source), str(dest)) == "updated"
    assert (dest / "SKILL.md").read_text(encoding="utf-8") == "hello v2"


def test_skill_missing_source(tmp_path):
    assert connect.install_skill(str(tmp_path / "nope"), str(tmp_path / "dest")) == \
        "missing-source"
