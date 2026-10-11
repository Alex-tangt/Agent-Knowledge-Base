"""Deployment boundaries: protected secrets, owned processes and serialized model."""

import json
import os
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
