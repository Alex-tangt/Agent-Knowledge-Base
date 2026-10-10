"""Deployment boundaries: protected secrets, owned processes and serialized model."""

import json
import os
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
