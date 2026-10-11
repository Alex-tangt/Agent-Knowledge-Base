"""Cold backup invariants, exercised entirely in temporary deployments."""

import json
from pathlib import Path

import pytest

from memory_base import backup, deploy


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    config = {"schema": 1, "python": str(home / "runtime" / "python"),
              "source": "/target/source", "model": "/target/model.gguf", "model_sha256": "a" * 64,
              "ports": {"embed": 17996, "upstream": 17886, "gateway": 17888}, "versions": deploy.PINS}
    deploy._write_json(home / "deployment.json", config)
    events = []
    monkeypatch.setattr(deploy, "_records", lambda _: {})
    monkeypatch.setattr(deploy, "_port_busy", lambda _: False)
    monkeypatch.setattr(deploy, "stop", lambda _, components: events.append(("stop", components)))
    monkeypatch.setattr(deploy, "start", lambda _, components, timeout: events.append(("start", components)))
    return home, config, events


def fill(home):
    entries = {"accounts/personal/resources/captures/a/content.md": "原文\n".encode(),
               "accounts/personal/context/collection.json": b'{"context":"kept"}',
               "accounts/personal/acl/permissions.json": b'{"reader":"read"}',
               "_system/identity/user-key-hashes.json": b'{"gateway":"argon2-hash"}',
               "_system/queue/queue.db": bytes(range(256))}
    for relative, content in entries.items():
        path = home / "data" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (home / "data" / "empty").mkdir()
    for relative in ("logs/gateway.err.log", "runtime/private-config", "processes.json", "credentials.json"):
        path = home / relative
        path.parent.mkdir(exist_ok=True)
        path.write_text("must-not-travel-secret")
    return entries


def target(tmp_path, config):
    home = tmp_path / "restore-target"
    home.mkdir()
    config = {**config, "python": str(home / "runtime" / "python"), "source": "/new/source",
              "model": "/new/model.gguf", "ports": {"embed": 27996, "upstream": 27886, "gateway": 27888}}
    deploy._write_json(home / "deployment.json", config)
    deploy._write_json(home / "openviking.conf", {"target": "configuration"})
    return home


def test_full_tree_round_trip_preserves_acl_context_identity_and_empty_directories(deployment, tmp_path):
    home, config, events = deployment
    entries = fill(home)
    source = tmp_path / "source"
    source.mkdir()
    registry = {"schema": 1, "projects": [{"name": "kb", "root": str(source)}]}
    deploy._write_json(home / "sources.json", registry)
    destination = tmp_path / "snapshot"
    result = backup.create_backup(home, destination)
    assert result["status"] == "backed_up"
    assert {p.name for p in destination.iterdir()} == {"data", "sources.json", "manifest.json"}
    assert "must-not-travel-secret" not in (destination / "manifest.json").read_text()
    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["versions"] == deploy.PINS
    assert all(set(item) == {"path", "size", "sha256"} for item in manifest["files"])
    restored = target(tmp_path, config)
    independent_config = (restored / "deployment.json").read_bytes()
    independent_upstream_config = (restored / "openviking.conf").read_bytes()
    result = backup.restore_backup(restored, destination)
    assert result["status"] == "restored"
    assert backup._inventory(home / "data") == backup._inventory(restored / "data")
    for relative, content in entries.items():
        assert (restored / "data" / relative).read_bytes() == content
    assert (restored / "sources.json").read_bytes() == (home / "sources.json").read_bytes()
    assert (restored / "deployment.json").read_bytes() == independent_config
    assert (restored / "openviking.conf").read_bytes() == independent_upstream_config
    assert not (restored / "processes.json").exists()
    state = json.loads((restored / "restore-state.json").read_text())
    assert state["identity_reissue_pending"] is True
    assert state["manifest_sha256"] == backup._hash(destination / "manifest.json")
    assert events == [("stop", ()), ("stop", ())]


def test_backup_missing_data_never_stops_services(deployment, tmp_path):
    home, _, events = deployment
    with pytest.raises(ValueError, match="data does not exist"):
        backup.create_backup(home, tmp_path / "snapshot")
    assert events == []


@pytest.mark.parametrize("relative", ["data/recursive", "runtime/copy", "data", ""])
def test_backup_destination_cannot_overlap_home(deployment, relative):
    home, _, events = deployment
    fill(home)
    with pytest.raises(ValueError, match="outside"):
        backup.create_backup(home, home / relative)
    assert events == []


def test_backup_destination_cannot_overlap_source_root(deployment, tmp_path):
    home, _, events = deployment
    fill(home)
    source = tmp_path / "source"
    source.mkdir()
    deploy._write_json(home / "sources.json", {"schema": 1, "projects": [{"name": "kb", "root": str(source)}]})
    with pytest.raises(ValueError, match="outside"):
        backup.create_backup(home, source / "snapshot")
    assert events == []


def test_existing_empty_destination_is_never_overwritten(deployment, tmp_path):
    home, _, events = deployment
    fill(home)
    destination = tmp_path / "snapshot"
    destination.mkdir()
    with pytest.raises(ValueError, match="new directory"):
        backup.create_backup(home, destination)
    assert destination.is_dir() and list(destination.iterdir()) == []
    assert events == []


def test_backup_failure_restarts_only_original_running_components(deployment, tmp_path, monkeypatch):
    home, _, events = deployment
    fill(home)
    monkeypatch.setattr(backup, "_running", lambda *_: ("embed", "upstream"))
    monkeypatch.setattr(backup, "_copy_tree", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        backup.create_backup(home, tmp_path / "snapshot")
    assert events == [("stop", ("embed", "upstream")), ("start", ("embed", "upstream"))]
    assert not (tmp_path / "snapshot").exists()
    assert not list(tmp_path.glob(".memory-base-backup-*"))


def test_partial_stop_failure_still_restores_original_component_set(deployment, tmp_path, monkeypatch):
    home, _, events = deployment
    fill(home)
    monkeypatch.setattr(backup, "_running", lambda *_: ("embed", "gateway"))
    monkeypatch.setattr(deploy, "stop", lambda *_: (_ for _ in ()).throw(RuntimeError("partial stop")))
    with pytest.raises(RuntimeError, match="partial stop"):
        backup.create_backup(home, tmp_path / "snapshot")
    assert events == [("start", ("embed", "gateway"))]


def test_unowned_busy_port_rejected_before_any_stop(deployment, tmp_path, monkeypatch):
    home, config, events = deployment
    fill(home)
    monkeypatch.setattr(deploy, "_port_busy", lambda port: port == config["ports"]["upstream"])
    with pytest.raises(RuntimeError, match="ownership is not proven"):
        backup.create_backup(home, tmp_path / "snapshot")
    assert events == []


def test_backup_detects_copy_changes(deployment, tmp_path, monkeypatch):
    home, _, _ = deployment
    fill(home)
    copy = backup._copy_tree

    def changed(source, destination, files, directories):
        copy(source, destination, files, directories)
        (source / files[0]["path"]).write_bytes(b"changed externally")

    monkeypatch.setattr(backup, "_copy_tree", changed)
    with pytest.raises(RuntimeError, match="changed"):
        backup.create_backup(home, tmp_path / "snapshot")
    assert not (tmp_path / "snapshot").exists()


@pytest.mark.parametrize("name", ["../escape", "/absolute", "data/../../escape", "data\\escape", "data/C:/escape",
                                 "data/nul.txt", "data/trailing.", "data//empty", "data/\x01control"])
def test_restore_rejects_unsafe_manifest_paths(deployment, tmp_path, name):
    home, config, events = deployment
    fill(home)
    destination = tmp_path / "snapshot"
    backup.create_backup(home, destination)
    manifest = json.loads((destination / "manifest.json").read_text())
    manifest["files"][0]["path"] = name
    deploy._write_json(destination / "manifest.json", manifest)
    restored = target(tmp_path, config)
    events.clear()
    with pytest.raises(ValueError, match="Unsafe"):
        backup.restore_backup(restored, destination)
    assert not (restored / "data").exists()
    assert events == []


@pytest.mark.parametrize("damage", ["body", "missing", "extra", "duplicate", "version", "model", "schema-bool"])
def test_restore_rejects_damage_without_target_mutation(deployment, tmp_path, damage):
    home, config, events = deployment
    fill(home)
    destination = tmp_path / "snapshot"
    backup.create_backup(home, destination)
    manifest = json.loads((destination / "manifest.json").read_text())
    first = destination / manifest["files"][0]["path"]
    if damage == "body":
        first.write_bytes(b"corrupt")
    elif damage == "missing":
        first.unlink()
    elif damage == "extra":
        (destination / "credentials.json").write_text("injected")
    elif damage == "duplicate":
        manifest["files"].append(manifest["files"][0])
    elif damage == "version":
        manifest["versions"] = {"openviking": "new-version"}
    elif damage == "model":
        manifest["model_sha256"] = "b" * 64
    else:
        manifest["schema"] = True
    deploy._write_json(destination / "manifest.json", manifest)
    restored = target(tmp_path, config)
    events.clear()
    before = backup._inventory(restored)
    with pytest.raises(ValueError):
        backup.restore_backup(restored, destination)
    # lifecycle lock is allowed metadata; no data/config/source was published.
    assert [(p, b) for p, b in [(i["path"], i["sha256"]) for i in backup._inventory(restored)[0]] if p != "lifecycle.lock"] == [
        (i["path"], i["sha256"]) for i in before[0]]
    assert events == []


def test_restore_refuses_existing_data(deployment, tmp_path):
    home, config, _ = deployment
    fill(home)
    destination = tmp_path / "snapshot"
    backup.create_backup(home, destination)
    restored = target(tmp_path, config)
    (restored / "data").mkdir()
    existing = restored / "data" / "existing"
    existing.write_text("owner data")
    with pytest.raises(ValueError, match="empty target"):
        backup.restore_backup(restored, destination)
    assert existing.read_text() == "owner data"


def test_restore_publication_failure_rolls_back_empty_target(deployment, tmp_path, monkeypatch):
    home, config, _ = deployment
    fill(home)
    destination = tmp_path / "snapshot"
    backup.create_backup(home, destination)
    restored = target(tmp_path, config)
    (restored / "data").mkdir()
    link = backup.os.link

    def fail_state(path, target, **kwargs):
        if path.name == "restore-state.json":
            raise OSError("metadata disk error")
        return link(path, target, **kwargs)

    monkeypatch.setattr(backup.os, "link", fail_state)
    with pytest.raises(OSError, match="metadata disk error"):
        backup.restore_backup(restored, destination)
    assert list((restored / "data").iterdir()) == []
    assert not (restored / "restore-state.json").exists()
    assert not list(restored.glob(".restore-*"))


def test_restore_late_existing_control_file_is_preserved_on_rollback(deployment, tmp_path, monkeypatch):
    home, config, _ = deployment
    fill(home)
    destination = tmp_path / "snapshot"
    backup.create_backup(home, destination)
    restored = target(tmp_path, config)
    link = backup.os.link

    def racing_file(path, target, **kwargs):
        Path(target).write_text("new owner state")
        return link(path, target, **kwargs)

    monkeypatch.setattr(backup.os, "link", racing_file)
    with pytest.raises(FileExistsError):
        backup.restore_backup(restored, destination)
    assert (restored / "restore-state.json").read_text() == "new owner state"
    assert not (restored / "data").exists()


def test_cleanup_refuses_paths_outside_operation_parent(tmp_path):
    intended, foreign = tmp_path / "intended", tmp_path / "foreign"
    intended.mkdir()
    foreign.mkdir()
    marker = foreign / "owner-data"
    marker.write_text("keep")
    with pytest.raises(ValueError, match="outside"):
        backup._remove_created(foreign, intended)
    assert marker.read_text() == "keep"


def test_symlink_data_is_rejected_and_original_stopped_set_restarted(deployment, tmp_path, monkeypatch):
    home, _, events = deployment
    fill(home)
    foreign = tmp_path / "foreign"
    foreign.write_text("not copied")
    try:
        (home / "data" / "link").symlink_to(foreign)
    except OSError:
        pytest.skip("Creating symlinks is not permitted on this host")
    monkeypatch.setattr(backup, "_running", lambda *_: ("upstream",))
    with pytest.raises(ValueError, match="Symbolic links"):
        backup.create_backup(home, tmp_path / "snapshot")
    assert events[-1] == ("start", ("upstream",))


def test_missing_installed_target_is_rejected(deployment, tmp_path):
    home, _, _ = deployment
    fill(home)
    destination = tmp_path / "snapshot"
    backup.create_backup(home, destination)
    with pytest.raises(RuntimeError, match="not installed"):
        backup.restore_backup(tmp_path / "uninstalled", destination)


def test_cli_backup_and_restore_require_explicit_paths():
    with pytest.raises(SystemExit) as error:
        deploy.main(["backup"])
    assert error.value.code == 2
    with pytest.raises(SystemExit) as error:
        deploy.main(["restore"])
    assert error.value.code == 2
