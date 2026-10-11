"""Verified cold copies of the complete OpenViking workspace.

Application ACLs, identities, contexts and queue state live inside ``data`` and
travel together. Runtime configuration, logs, PID records and vault secrets do
not. The checksum manifest detects damage; it is not a signature of its author.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile

from memory_base import deploy

FORMAT = "memory-base-cold/v1"


def _path(path: str | Path) -> Path:
    path = Path(path).expanduser().absolute()
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or (candidate.exists() and
                getattr(candidate.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            raise ValueError("Symbolic links and junctions are not allowed in backup paths")
    return path.resolve()


def _overlaps(left: Path, right: Path) -> bool:
    return left.is_relative_to(right) or right.is_relative_to(left)


def _registry(home: Path) -> tuple[bytes | None, list[Path]]:
    path = _path(home / "sources.json")
    if not path.exists():
        return None, []
    raw = path.read_bytes()
    from memory_base.sources import validate_registry
    registry = validate_registry(json.loads(raw))
    return raw, [Path(project["root"]).expanduser().resolve() for project in registry["projects"]]


def _safe_name(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Unsafe backup entry name")
    parts = value.split("/")
    for part in parts:
        if (part in ("", ".", "..") or part[-1:] in (" ", ".") or
                any(ord(char) < 32 or char in ':<>|?*' for char in part) or
                re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)):
            raise ValueError("Unsafe backup entry name")
    if PurePosixPath(value).is_absolute():
        raise ValueError("Unsafe backup entry name")
    return value


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inventory(root: Path) -> tuple[list[dict], list[str]]:
    files, directories = [], []
    names = set()

    def visit(directory: Path):
        for entry in sorted(os.scandir(directory), key=lambda item: item.name):
            path = Path(entry.path)
            relative = _safe_name(path.relative_to(root).as_posix())
            folded = relative.casefold()
            if folded in names:
                raise ValueError("Backup entries collide on case-insensitive filesystems")
            names.add(folded)
            attributes = entry.stat(follow_symlinks=False)
            if entry.is_symlink() or getattr(attributes, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise ValueError("Symbolic links and junctions are not allowed in backup data")
            if stat.S_ISDIR(attributes.st_mode):
                directories.append(relative)
                visit(path)
            elif stat.S_ISREG(attributes.st_mode):
                files.append({"path": relative, "size": attributes.st_size, "sha256": _hash(path)})
            else:
                raise ValueError("Only regular files and directories can be backed up")

    visit(root)
    return sorted(files, key=lambda item: item["path"]), sorted(directories)


def _copy_tree(source: Path, destination: Path, files: list[dict], directories: list[str]) -> None:
    for relative in directories:
        (destination / relative).mkdir()
    for item in files:
        shutil.copy2(source / item["path"], destination / item["path"], follow_symlinks=False)


def _running(home: Path, config: dict) -> tuple[str, ...]:
    records = deploy._records(home)
    running = []
    for component in deploy.COMPONENTS:
        record = records.get(component)
        if deploy.owned_component(component, home, config, record):
            running.append(component)
        elif deploy._port_busy(config["ports"][component]) or deploy.owned_process(record):
            raise RuntimeError(f"Refusing cold copy: {component} process ownership is not proven for this home")
    return tuple(running)


@contextmanager
def _cold(home: Path, config: dict, timeout: float):
    running = _running(home, config)  # Check all ownership before stopping any.
    try:
        deploy.stop(home, running)
        if any(deploy._port_busy(config["ports"][component]) for component in deploy.COMPONENTS):
            raise RuntimeError("Service port became occupied during cold copy")
        yield
    finally:
        # Includes a partial stop failure. start() is idempotent for components
        # that are still running, and never adds an originally stopped one.
        if running:
            deploy.start(home, running, timeout)


def create_backup(home: Path, destination: Path, *, timeout: float = 180) -> dict:
    home, destination = _path(home), _path(destination)
    with deploy.lifecycle_lock(home):
        config = deploy.load_config(home)
        data = _path(home / "data")
        if not data.is_dir():
            raise ValueError("OpenViking data does not exist; nothing has been backed up")
        registry_raw, roots = _registry(home)
        if any(_overlaps(destination, root) for root in (home, *roots)):
            raise ValueError("Backup destination must be outside the deployment and registered source roots")
        if destination.exists() or not destination.parent.is_dir():
            raise ValueError("Backup destination must be a new directory with an existing parent")
        with _cold(home, config, timeout):
            with tempfile.TemporaryDirectory(prefix=".memory-base-backup-", dir=destination.parent) as temporary:
                staged = Path(temporary)
                (staged / "data").mkdir()
                files, directories = _inventory(data)
                _copy_tree(data, staged / "data", files, directories)
                if _inventory(data) != (files, directories) or _inventory(staged / "data") != (files, directories):
                    raise RuntimeError("Workspace changed or copy verification failed")
                if registry_raw is not None:
                    (staged / "sources.json").write_bytes(registry_raw)
                payload_files, payload_directories = _inventory(staged)
                manifest = {"schema": 1, "format": FORMAT,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "versions": config["versions"], "model_sha256": config["model_sha256"],
                            "files": payload_files, "directories": payload_directories,
                            "credentials": "excluded; workspace identity key hashes are retained"}
                deploy._write_json(staged / "manifest.json", manifest)
                # Exclusive mkdir never overwrites even an empty racing target.
                # Publish the manifest last: a partial copy is never accepted
                # as a complete backup. All payload renames stay on one volume.
                destination.mkdir()
                try:
                    for name in ("data", "sources.json", "manifest.json"):
                        if (staged / name).exists():
                            (staged / name).rename(destination / name)
                except BaseException:
                    shutil.rmtree(destination)
                    raise
                return {"status": "backed_up", "destination": str(destination),
                        "files": len(payload_files), "manifest_sha256": _hash(destination / "manifest.json")}


def _validate_backup(backup: Path, config: dict) -> tuple[str, list[dict], list[str]]:
    manifest_path = backup / "manifest.json"
    if not backup.is_dir() or not manifest_path.is_file():
        raise ValueError("Backup manifest is missing")
    _path(manifest_path)
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    if not isinstance(manifest, dict) or manifest.get("schema") != 1 or manifest.get("format") != FORMAT:
        raise ValueError("Unsupported backup manifest")
    if manifest.get("versions") != config["versions"] or config["versions"] != deploy.PINS:
        raise ValueError("Backup and installed runtime dependency versions must match the deployment pins")
    if manifest.get("model_sha256") != config["model_sha256"]:
        raise ValueError("Backup embedding model does not match the target deployment")
    files, directories = manifest.get("files"), manifest.get("directories")
    if not isinstance(files, list) or not isinstance(directories, list):
        raise ValueError("Malformed backup inventory")
    names = set()
    for value in [item.get("path") if isinstance(item, dict) else None for item in files] + directories:
        name = _safe_name(value)
        if name.casefold() in names:
            raise ValueError("Duplicate backup entry")
        names.add(name.casefold())
        if not (name == "data" or name.startswith("data/") or name == "sources.json"):
            raise ValueError("Unexpected backup payload")
    if "data" not in directories or "sources.json" in directories:
        raise ValueError("Backup data directory is missing or invalid")
    for item in files:
        if (set(item) != {"path", "size", "sha256"} or type(item["size"]) is not int or item["size"] < 0 or
                not isinstance(item["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", item["sha256"])):
            raise ValueError("Malformed backup file checksum")
    actual_files, actual_dirs = _inventory(backup)
    actual_files = [item for item in actual_files if item["path"] != "manifest.json"]
    if actual_files != sorted(files, key=lambda item: item["path"]) or actual_dirs != sorted(directories):
        raise ValueError("Backup inventory or checksum verification failed")
    _registry(backup)
    return hashlib.sha256(manifest_raw).hexdigest(), files, directories


def restore_backup(home: Path, backup: Path, *, timeout: float = 180) -> dict:
    home, backup = _path(home), _path(backup)
    if _overlaps(home, backup):
        raise ValueError("Restore deployment must be independent of its backup")
    with deploy.lifecycle_lock(home):
        config = deploy.load_config(home)
        data = _path(home / "data")
        if data.exists() and (not data.is_dir() or any(data.iterdir())):
            raise ValueError("Restore requires an empty target data directory")
        if (home / "sources.json").exists() or (home / "restore-state.json").exists():
            raise ValueError("Restore requires a target without existing source or restore state")
        manifest_hash, files, directories = _validate_backup(backup, config)
        _, roots = _registry(backup)
        if any(_overlaps(home, root) for root in roots):
            raise ValueError("Restore deployment cannot overlap a registered source root")
        with _cold(home, config, timeout):
            with tempfile.TemporaryDirectory(prefix=".restore-", dir=home) as temporary:
                staged = Path(temporary)
                _copy_tree(backup, staged, files, directories)
                if _inventory(staged) != (sorted(files, key=lambda item: item["path"]), sorted(directories)):
                    raise ValueError("Staged restore checksum verification failed")
                if _hash(backup / "manifest.json") != manifest_hash:
                    raise ValueError("Backup manifest changed during restore")
                state = {"schema": 1, "restored_at": datetime.now(timezone.utc).isoformat(),
                         "manifest_sha256": manifest_hash, "identity_reissue_pending": True}
                deploy._write_json(staged / "restore-state.json", state)
                # All verified payload stays on the target filesystem before
                # the directory rename. Roll back secondary metadata if any
                # part of publication fails; never remove preexisting data.
                published = []
                empty_data = data.exists()
                try:
                    if empty_data:
                        data.rmdir()
                    (staged / "data").rename(data)
                    published.append(data)
                    for name in ("sources.json", "restore-state.json"):
                        if (staged / name).exists():
                            (staged / name).rename(home / name)
                            published.append(home / name)
                except BaseException:
                    for path in reversed(published):
                        if path.is_dir():
                            shutil.rmtree(path)
                        else:
                            path.unlink()
                    if empty_data:
                        data.mkdir(exist_ok=True)
                    raise
    return {"status": "restored", "home": str(home), "files": len(files),
            "identity_reissue_pending": True,
            "sources": "registry retained; source availability is checked when accessed"}
