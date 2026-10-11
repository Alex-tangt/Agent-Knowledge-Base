"""Deployment boundaries: protected secrets, owned processes and serialized model."""

import json
import os
from contextlib import contextmanager
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time

import pytest

from memory_base import credentials, deploy
from memory_base.embed import EmbeddingState


def test_upstream_config_uses_external_embedder_and_no_secret(tmp_path):
    config = {"ports": {"embed": 17996, "upstream": 17886}}
    path = deploy.write_upstream_config(tmp_path, config)
    raw = path.read_text(encoding="utf-8")
    value = json.loads(raw)
    assert value["embedding"]["dense"]["provider"] == "openai"
    assert value["embedding"]["dense"]["api_base"] == "http://127.0.0.1:17996/v1"
    assert value["server"]["auth_mode"] == "api_key"
    assert value["server"]["root_api_key"] == "$MEMORY_BASE_ROOT_KEY"
    assert value["encryption"]["api_key_hashing"]["enabled"] is True
    assert "root-secret" not in raw
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_fresh_runtime_switch_preserves_deployment_home_and_existing_model(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    runtime.parent.mkdir(parents=True)
    runtime.write_text("test runtime")
    model = tmp_path / "embedding.gguf"
    model.write_bytes(b"GGUFmodel")
    (tmp_path / "deployment.json").write_text(json.dumps({"python": "/old/experimental/python", "model": str(model)}))
    monkeypatch.setattr(deploy, "_versions", lambda _: deploy.PINS)
    monkeypatch.setattr(deploy.subprocess, "run", lambda *_a, **_k: None)
    monkeypatch.setattr(deploy, "_ensure_secret", lambda *_: "existing")
    config = deploy.install(tmp_path, fresh_runtime=True)
    assert config["python"] == str(runtime.resolve())
    assert config["model"] == str(model.resolve())
    assert json.loads((tmp_path / "openviking.conf").read_text())["storage"]["workspace"] == str(tmp_path / "data")


def test_secrets_are_env_override_and_home_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv(credentials.env_name("host-codex"), "process-secret")
    assert credentials.get_secret("host-codex", tmp_path) == "process-secret"
    assert credentials._target("host-codex", tmp_path) != credentials._target("host-codex", tmp_path / "other")


def test_non_windows_never_writes_plaintext_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials.os, "name", "posix")
    with pytest.raises(RuntimeError, match="MEMORY_BASE_SECRET_HOST_CODEX"):
        credentials.set_secret("host-codex", "secret", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_spawn_refuses_unowned_healthy_service(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "owned_process", lambda _: False)
    monkeypatch.setattr(deploy, "_port_busy", lambda _: True)
    monkeypatch.setattr(deploy, "_health", lambda *_: {"status": "ok"})
    monkeypatch.setattr(deploy.subprocess, "Popen", lambda *_a, **_k: pytest.fail("must not spawn"))
    with pytest.raises(RuntimeError, match="unowned service"):
        deploy._spawn("upstream", tmp_path, {"ports": {"upstream": 17886}}, {}, 1)


def test_stop_refuses_reused_pid_without_killing(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "load_config", lambda _: {"ports": {"upstream": 17886}})
    monkeypatch.setattr(deploy, "_records", lambda _: {"upstream": {"pid": 42, "port": 17886}})
    monkeypatch.setattr(deploy, "owned_process", lambda _: False)
    monkeypatch.setattr(deploy, "_port_busy", lambda _: True)
    monkeypatch.setattr(deploy.subprocess, "run", lambda *_a, **_k: pytest.fail("must not kill"))
    with pytest.raises(RuntimeError, match="ownership no longer matches"):
        deploy.stop(tmp_path, ("upstream",))


def test_ownership_binds_pid_creation_and_exact_command(monkeypatch):
    record = {"pid": 42, "created": "first", "command": "owned-command"}
    monkeypatch.setattr(deploy, "_process_info", lambda _: {"created": "later", "command": "owned-command"})
    assert not deploy.owned_process(record)
    monkeypatch.setattr(deploy, "_process_info", lambda _: {"created": "first", "command": "different"})
    assert not deploy.owned_process(record)
    monkeypatch.setattr(deploy, "_process_info", lambda _: {"created": "first", "command": "owned-command"})
    assert deploy.owned_process(record)


def _command_text(command):
    return subprocess.list2cmdline(command) if os.name == "nt" else " ".join(command)


def test_component_ownership_binds_real_command_to_home_with_shared_runtime(tmp_path, monkeypatch):
    left, right = tmp_path / "left", tmp_path / "right"
    config = {"python": sys.executable, "ports": {"gateway": 17888}}
    command = _command_text(deploy._component_command("gateway", left, config))
    record = {"pid": 42, "created": "now", "command": command, "port": 17888, "home": str(right)}
    monkeypatch.setattr(deploy, "_process_info", lambda _: {"created": "now", "command": command})
    assert deploy.owned_component("gateway", left, config, record)
    assert not deploy.owned_component("gateway", right, config, record)
    record["command"] += " --other"
    assert not deploy.owned_component("gateway", left, config, record)


def test_legacy_component_ownership_requires_home_local_runtime(tmp_path, monkeypatch):
    config = {"python": str(tmp_path / "runtime" / "python"), "ports": {"gateway": 17888}}
    command = _command_text(deploy._component_command("gateway", tmp_path, config, legacy=True))
    record = {"pid": 42, "created": "now", "command": command, "port": 17888}
    monkeypatch.setattr(deploy, "_process_info", lambda _: {"created": "now", "command": record["command"]})
    assert deploy.owned_component("gateway", tmp_path, config, record)
    config["python"] = sys.executable
    record["command"] = _command_text(deploy._component_command("gateway", tmp_path, config, legacy=True))
    assert not deploy.owned_component("gateway", tmp_path, config, record)


def test_component_wrapper_sets_home_and_preserves_module_arguments(tmp_path):
    package = tmp_path / "memory_base"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "embed.py").write_text("import json,os,sys;print(json.dumps([os.environ['MEMORY_BASE_HOME'],sys.argv]))")
    config = {"python": sys.executable, "ports": {"embed": 17996}, "model": "/test/model.gguf"}
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    result = subprocess.run(deploy._component_command("embed", tmp_path, config), cwd=tmp_path,
                            env=env, capture_output=True, text=True, check=True)
    home, arguments = json.loads(result.stdout)
    assert home == str(tmp_path)
    assert arguments[1:] == ["--port", "17996", "--model", "/test/model.gguf"]


def test_upstream_health_rejects_dev_mode_and_wrong_version(monkeypatch):
    config = {"ports": {"upstream": 17886}}
    payload = {"healthy": True, "version": "0.4.23", "auth_mode": "dev"}
    monkeypatch.setattr(deploy, "_request", lambda *_a, **_k: (200, payload))
    assert deploy._health("upstream", config) is None
    payload["auth_mode"] = "api_key"
    payload["version"] = "0.5.0"
    assert deploy._health("upstream", config) is None
    payload["version"] = "0.4.23"
    assert deploy._health("upstream", config) == payload


def test_gateway_env_has_separate_host_identities_and_never_root_key(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "get_secret", lambda name, _: "upstream-secret" if name == "upstream-key" else None)
    monkeypatch.setattr(deploy, "_ensure_secret", lambda name, _: "token-" + name)
    env = deploy.gateway_env(tmp_path, {"ports": {"upstream": 17886, "gateway": 17888}})
    identities = json.loads(env["MEMORY_BASE_TOKENS"])
    assert {entry["principal"] for entry in identities.values()} == set(deploy.PROFILES)
    assert identities["token-host-reader"]["role"] == "reader"
    assert identities["token-host-agent-bubble"]["role"] == "writer"
    assert env["MEMORY_BASE_OV_ACCOUNT"] == "personal"
    assert env["MEMORY_BASE_OV_USER"] == "gateway"
    assert deploy.ROOT_KEY_ENV not in env


def test_gateway_env_uses_narrow_or_denied_persisted_host_scope_rules(tmp_path, monkeypatch):
    deploy._write_json(tmp_path / "access.json", {"schema": 1, "profiles": {
        "codex": {"read_scopes": ["projects/kb/docs"], "write_scopes": []},
        "reader": {"read_scopes": []}, "agent-bubble": {"write_scopes": ["captures/2026-10-11"]}}})
    monkeypatch.setattr(deploy, "get_secret", lambda *_: "upstream-key")
    monkeypatch.setattr(deploy, "_ensure_secret", lambda name, _: name)
    env = deploy.gateway_env(tmp_path, {"ports": {"upstream": 17886, "gateway": 17888}})
    identities = json.loads(env["MEMORY_BASE_TOKENS"])
    assert identities["host-codex"]["read_scopes"] == ["projects/kb/docs"]
    assert identities["host-codex"]["write_scopes"] == []
    assert identities["host-reader"]["read_scopes"] == []
    assert identities["host-agent-bubble"]["write_scopes"] == ["captures/2026-10-11"]
    assert "read_scopes" not in identities["host-dsh"]


def test_gateway_env_rejects_bad_scope_policy_before_reading_any_secret(tmp_path, monkeypatch):
    deploy._write_json(tmp_path / "access.json", {"schema": True, "profiles": {}})
    monkeypatch.setattr(deploy, "get_secret", lambda *_: pytest.fail("must validate before credentials"))
    monkeypatch.setattr(deploy, "_ensure_secret", lambda *_: pytest.fail("must validate before credentials"))
    with pytest.raises(ValueError):
        deploy.gateway_env(tmp_path, {"ports": {"upstream": 17886, "gateway": 17888}})


def test_log_output_redacts_every_registered_credential(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "get_secret", lambda name, _: "secret-" + name)
    assert deploy.redact("root=secret-root-key host=secret-host-dsh", tmp_path) == "root=[REDACTED] host=[REDACTED]"


def test_restart_reuses_upstream_identity_and_enables_acl(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(deploy, "_ensure_secret", lambda *_: "root")
    monkeypatch.setattr(deploy, "get_secret", lambda *_: "existing-upstream")
    monkeypatch.setattr(deploy, "set_secret", lambda *_: pytest.fail("existing key must not be replaced"))
    monkeypatch.setattr(deploy, "_api", lambda config, path, *args, **kwargs:
                        [{"account_id": "personal"}] if path == "/api/v1/admin/accounts" else
                        calls.append((path, args, kwargs)) or {})
    deploy.ensure_identity(tmp_path, {"ports": {"upstream": 17886}})
    assert len(calls) == 2
    assert calls[0][0].endswith("/settings")
    assert calls[0][1] == ("PATCH", {"acl": {"enabled": True}})
    assert calls[1][2]["key"] == "existing-upstream"


def test_restored_identity_is_reissued_even_when_target_vault_has_old_key(tmp_path, monkeypatch):
    calls, secrets = [], []
    deploy._write_json(tmp_path / "restore-state.json", {"identity_reissue_pending": True, "manifest_sha256": "a" * 64})
    monkeypatch.setattr(deploy, "_ensure_secret", lambda *_: "target-root")
    monkeypatch.setattr(deploy, "get_secret", lambda *_: "stale-target-key")
    monkeypatch.setattr(deploy, "set_secret", lambda *args: secrets.append(args))

    def api(config, path, *args, **kwargs):
        calls.append((path, args, kwargs))
        if path == "/api/v1/admin/accounts":
            return [{"account_id": "personal"}]
        if path.endswith("/key"):
            return {"user_key": "fresh-restored-key"}
        return {}

    monkeypatch.setattr(deploy, "_api", api)
    deploy.ensure_identity(tmp_path, {"ports": {"upstream": 17886}})
    assert secrets == [("upstream-key", "fresh-restored-key", tmp_path)]
    assert calls[-1][2]["key"] == "fresh-restored-key"
    assert json.loads((tmp_path / "restore-state.json").read_text())["identity_reissue_pending"] is False


def test_restored_identity_failure_keeps_reissue_marker(tmp_path, monkeypatch):
    deploy._write_json(tmp_path / "restore-state.json", {"identity_reissue_pending": True})
    monkeypatch.setattr(deploy, "_ensure_secret", lambda *_: "root")
    monkeypatch.setattr(deploy, "get_secret", lambda *_: "stale-key")
    monkeypatch.setattr(deploy, "_api", lambda *_a, **_k: [{"account_id": "personal"}] if _a[1].endswith("/accounts")
                        else (_ for _ in ()).throw(RuntimeError("regeneration failed")))
    with pytest.raises(RuntimeError, match="regeneration failed"):
        deploy.ensure_identity(tmp_path, {"ports": {"upstream": 17886}})
    assert json.loads((tmp_path / "restore-state.json").read_text())["identity_reissue_pending"] is True


def _installed_home(tmp_path):
    home = tmp_path / "home"
    deploy._write_json(home / "deployment.json", {"schema": 1})
    return home


def test_source_cli_registration_is_atomic_idempotent_and_never_writes_project(tmp_path):
    home = _installed_home(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    original = source / "README.md"
    original.write_bytes("权威原文\n".encode("utf-8"))
    before = original.stat()
    result = deploy.source_add(home, "kb", str(source))
    assert result["status"] == "registered"
    assert result["project"]["include"] == ["README.md", "docs/**/*.md"]
    registered = (home / "sources.json").read_bytes()
    assert deploy.source_add(home, "kb", str(source))["status"] == "unchanged"
    assert (home / "sources.json").read_bytes() == registered
    assert deploy.source_list(home)["projects"][0]["availability"] == "reachable"
    assert original.read_bytes() == "权威原文\n".encode("utf-8")
    assert original.stat().st_mtime_ns == before.st_mtime_ns
    assert [p.name for p in source.iterdir()] == ["README.md"]
    assert not (home / "sources.json.tmp").exists()


def test_source_replace_requires_explicit_switch_and_preserves_custom_patterns(tmp_path):
    home = _installed_home(tmp_path)
    source, second = tmp_path / "source", tmp_path / "second"
    source.mkdir()
    second.mkdir()
    deploy.source_add(home, "kb", str(source), include=["notes/**/*.md"], exclude=[])
    previous = (home / "sources.json").read_bytes()
    with pytest.raises(ValueError, match="--replace"):
        deploy.source_add(home, "kb", str(second))
    assert (home / "sources.json").read_bytes() == previous
    assert deploy.source_add(home, "kb", str(second), replace=True)["status"] == "replaced"
    project = deploy.source_list(home)["projects"][0]
    assert project["include"] == ["notes/**/*.md"] and project["exclude"] == []


def test_source_remove_changes_only_registry_and_missing_root_is_visible(tmp_path):
    home = _installed_home(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    deploy.source_add(home, "kb", str(source))
    source.rmdir()
    assert deploy.source_list(home)["projects"][0]["availability"] == "unavailable"
    assert deploy.source_remove(home, "kb")["status"] == "unregistered"
    assert deploy.source_remove(home, "kb")["status"] == "unchanged"
    assert deploy.source_list(home)["projects"] == []


def test_source_add_refuses_unavailable_or_home_overlapping_root(tmp_path):
    home = _installed_home(tmp_path)
    with pytest.raises(ValueError, match="existing readable"):
        deploy.source_add(home, "kb", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="independent"):
        deploy.source_add(home, "kb", str(home))
    assert not (home / "sources.json").exists()


def test_source_registry_invalid_does_not_get_overwritten(tmp_path):
    home = _installed_home(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (home / "sources.json").write_text('{"schema":1,"projects":[],"unknown":"do not erase"}')
    previous = (home / "sources.json").read_bytes()
    with pytest.raises(ValueError):
        deploy.source_add(home, "kb", str(source))
    assert (home / "sources.json").read_bytes() == previous


def test_access_set_preserves_other_side_and_profile_and_normalizes_scopes(tmp_path):
    home = _installed_home(tmp_path)
    deploy.access_set(home, "codex", read_scopes=["root", "projects/kb", "root"], write_scopes=[])
    deploy.access_set(home, "reader", read_scopes=["projects/kb/docs"])
    result = deploy.access_set(home, "codex", read_scopes=[])
    assert result["gateway_restart_required"] is True
    access = json.loads((home / "access.json").read_text())
    assert access["profiles"]["codex"] == {"read_scopes": [], "write_scopes": []}
    assert access["profiles"]["reader"] == {"read_scopes": ["projects/kb/docs"]}
    assert deploy.access_set(home, "codex", read_scopes=[])["status"] == "unchanged"
    assert not (home / "access.json.tmp").exists()


def test_access_set_rejects_unknown_profile_or_ambiguous_scope_without_mutation(tmp_path):
    home = _installed_home(tmp_path)
    for profile, scopes in (("unknown", ["root"]), ("codex", ["projects/../escape"]), ("codex", None)):
        with pytest.raises(ValueError):
            deploy.access_set(home, profile, read_scopes=scopes)
    assert not (home / "access.json").exists()


def test_access_cli_requires_profile_and_rejects_conflicting_grant_deny(tmp_path):
    for argv in (["access-set", "--deny-read"],
                 ["access-set", "--profile", "codex"],
                 ["access-set", "--profile", "codex", "--read-scope", "root", "--deny-read"]):
        with pytest.raises(SystemExit) as error:
            deploy.main(argv)
        assert error.value.code == 2


def test_autostart_cli_status_is_read_only_and_enable_disable_use_lifecycle_lock(tmp_path, monkeypatch, capsys):
    from memory_base import autostart
    home = _installed_home(tmp_path)
    events = []

    @contextmanager
    def lock(path):
        events.append(("lock", path))
        yield
        events.append(("unlock", path))

    monkeypatch.setattr(deploy, "lifecycle_lock", lock)
    monkeypatch.setattr(autostart, "status", lambda path: events.append(("status", path)) or {"enabled": False})
    monkeypatch.setattr(autostart, "enable", lambda path, python: events.append(("enable", path, python)) or {"enabled": True})
    monkeypatch.setattr(autostart, "disable", lambda path: events.append(("disable", path)) or {"enabled": False})
    monkeypatch.setattr(deploy, "load_config", lambda _: {"python": "installed-python"})
    assert deploy.main(["autostart", "--home", str(home)]) == 0
    assert events == [("status", home)]
    assert json.loads(capsys.readouterr().out) == {"enabled": False}
    events.clear()
    assert deploy.main(["autostart", "--home", str(home), "--mode", "enable"]) == 0
    assert events == [("lock", home), ("enable", home, "installed-python"), ("unlock", home)]
    assert json.loads(capsys.readouterr().out) == {"enabled": True}
    events.clear()
    monkeypatch.setattr(deploy, "load_config", lambda _: pytest.fail("disable must allow removed runtime"))
    assert deploy.main(["autostart", "--home", str(home), "--mode", "disable"]) == 0
    assert events == [("lock", home), ("disable", home), ("unlock", home)]


def test_llama_context_is_serialized_across_parallel_requests():
    class UnsafeModel:
        active = 0
        lock = threading.Lock()

        def create_embedding(self, texts):
            with self.lock:
                self.active += 1
                assert self.active == 1, "model entered concurrently"
            time.sleep(0.005)
            with self.lock:
                self.active -= 1
            return {"data": [{"embedding": [float(len(text))]} for text in texts]}

    state = EmbeddingState(UnsafeModel())
    with ThreadPoolExecutor(max_workers=8) as executor:
        outputs = list(executor.map(lambda i: state.embed([str(i)]), range(24)))
    assert outputs == [[[1.0]]] * 10 + [[[2.0]]] * 14
    assert state.calls == state.items == 24
