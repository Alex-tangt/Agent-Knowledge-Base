"""Frozen-code native acceptance. No pip, production writes, or raw body output."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

ROOT = Path(__file__).resolve().parents[2]
REPORTS = Path(__file__).resolve().parent
DAY = "2026-10-10"
ARTICLE_SCOPE = "captures/2026-10-10/article_summary"
QUERY = "石墨储能离子电池"
FROZEN = "d8a670905f93c86b36c6957b601fd8c2f02d0f45"
RUNTIME = Path(r"C:\Users\Tan\.agent-memory-base\runtime\Scripts\python.exe")
MODEL = Path(r"C:\Users\Tan\.cache\openviking\models\bge-small-zh-v1.5-f16.gguf")


def sha(value: bytes | str) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def inventory(root: Path) -> tuple[list[dict], list[str]]:
    """Independent observer; do not reuse the implementation's inventory helper."""
    files, directories = [], []
    for path in root.rglob("*"):
        require(not path.is_symlink() and not bool(path.lstat().st_file_attributes & 0x400),
                "INVENTORY_REPARSE_FORBIDDEN")
        name = path.relative_to(root).as_posix()
        if path.is_dir():
            directories.append(name)
        elif path.is_file():
            files.append({"path": name, "size": path.stat().st_size, "sha256": file_hash(path)})
        else:
            raise AssertionError("INVENTORY_SPECIAL_FILE_FORBIDDEN")
    return sorted(files, key=lambda item: item["path"]), sorted(directories)


def require(condition: bool, code: str):
    if not condition:
        raise AssertionError(code)


def git(*args) -> str:
    result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def free_ports(count: int) -> list[int]:
    sockets = [socket.socket() for _ in range(count)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def http(url: str, method="GET", body=None, headers=None) -> tuple[int, dict]:
    request = urllib.request.Request(url, method=method, headers=headers or {},
                                     data=None if body is None else canonical(body).encode("utf-8"))
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=180) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def run_registry_hash() -> str:
    import winreg
    values = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            index = 0
            while True:
                try:
                    values.append(winreg.EnumValue(key, index))
                except OSError as error:
                    if error.winerror == 259:
                        break
                    raise
                index += 1
    except FileNotFoundError:
        pass
    return sha(canonical(sorted(values)))


def windows_argv(command: str) -> list[str]:
    parser = ctypes.WinDLL("shell32", use_last_error=True).CommandLineToArgvW
    parser.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    parser.restype = ctypes.POINTER(ctypes.c_wchar_p)
    count = ctypes.c_int()
    values = parser(command, ctypes.byref(count))
    require(bool(values), "COMMAND_PARSE_FAILED")
    try:
        return [values[index] for index in range(count.value)]
    finally:
        free = ctypes.WinDLL("kernel32").LocalFree
        free.argtypes = [ctypes.c_void_p]
        free(values)


class Acceptance:
    def __init__(self, args):
        from memory_base import autostart, backup, credentials, deploy
        from memory_base.upstream import OpenVikingClient
        self.autostart, self.backup, self.credentials, self.deploy = autostart, backup, credentials, deploy
        self.client_type = OpenVikingClient
        self.args = args
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        self.c_root = Path.home() / ".memory-base-acceptance" / self.run_id
        self.d_root = Path(r"D:\python_work\work2026-4\memory-base-recovery-assets") / self.run_id
        self.source, self.target = self.c_root / "source", self.d_root / "target"
        self.snapshot, self.damaged = self.d_root / "snapshot", self.d_root / "damaged-copy"
        self.racing_snapshot = self.d_root / "cold-copy-race"
        self.fixture = self.c_root / "project"
        self.report = {"schema": 1, "run_id": self.run_id, "frozen_code": args.frozen,
                       "script_commit": git("rev-parse", "HEAD"), "script_sha256": file_hash(Path(__file__)),
                       "homes": [str(self.source), str(self.target)], "snapshot": str(self.snapshot),
                       "checks": [], "process_history": [], "limitations": ["shared installed runtime; no fresh dependency installation",
                                                      "logical volumes only; physical disks not inferred",
                                                      "queue integrity and file coverage, not pending-task semantics",
                                                      "2 MiB per-file source limit tested; no scan file-count limit implemented or tested",
                                                      "manual pythonw command execution, not actual logon trigger"]}
        self.report_path = REPORTS / ("results-" + self.run_id + ".json")
        self.allowed_body = "# 验收原文\r\n\r\n石墨储能离子电池项目安排周五测试。\r\nUnicode：中文与 🧭。\r\n"
        self.original = {"request_id": "mb85-allowed", "content": self.allowed_body,
                         "title": "原文验收", "kind": "article_summary", "observed_on": DAY}
        self.registered = False
        self.manual = None
        self.manual_record = None

    def save(self):
        self.report_path.write_text(json.dumps(self.report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def check(self, name, operation):
        started = time.monotonic()
        try:
            evidence = operation() or {}
        except BaseException as error:
            code = str(error) if isinstance(error, AssertionError) else type(error).__name__
            self.report["checks"].append({"name": name, "passed": False, "failure": code,
                                          "seconds": round(time.monotonic() - started, 3)})
            self.save()
            print("CHECK_FAILED " + name + " " + code, flush=True)
            raise
        self.report["checks"].append({"name": name, "passed": True, "evidence": evidence,
                                      "seconds": round(time.monotonic() - started, 3)})
        self.save()
        print("CHECK_PASSED " + name, flush=True)

    def api(self, home, tool, body, profile="codex", expected=200):
        config = self.deploy.load_config(home)
        token = self.credentials.get_secret("host-" + profile, home) if profile else None
        headers = {"Authorization": "Bearer " + token} if token else {}
        status, payload = http(f"http://127.0.0.1:{config['ports']['gateway']}/api/memory/{tool}",
                               "POST", body, headers)
        require(status == expected, f"HTTP_{tool}_{status}_EXPECTED_{expected}")
        return payload

    def native(self, home, path, method="GET", body=None, *, user="gateway", key_name="upstream-key"):
        config = self.deploy.load_config(home)
        key = self.credentials.get_secret(key_name, home)
        require(bool(key), "NATIVE_CREDENTIAL_MISSING")
        return http(f"http://127.0.0.1:{config['ports']['upstream']}" + path, method, body,
                    {"X-API-Key": key, "X-OpenViking-Account": "personal", "X-OpenViking-User": user})

    def client(self, home):
        config = self.deploy.load_config(home)
        return self.client_type(f"http://127.0.0.1:{config['ports']['upstream']}",
                                self.credentials.get_secret("upstream-key", home))

    def lifecycle(self, home, operation):
        with self.deploy.lifecycle_lock(home):
            result = operation(home)
        self.record_components(home, operation.__name__)
        return result

    def record_components(self, home, phase):
        config = self.deploy.load_config(home)
        records = self.deploy._records(home)
        for component, record in records.items():
            require(component in self.deploy.COMPONENTS and
                    self.deploy.owned_component(component, home, config, record), "PROCESS_OWNERSHIP_FAILED")
        self.report["process_history"].append({"phase": phase, "home": str(home), "records": records})
        self.save()

    def running(self, home):
        config = self.deploy.load_config(home)
        return tuple(c for c in self.deploy.COMPONENTS
                     if self.deploy.owned_component(c, home, config, self.deploy._records(home).get(c)))

    def prepare(self):
        require(sys.platform == "win32", "WINDOWS_REQUIRED")
        require(sys.version_info[:2] == (3, 12) and Path(sys.executable).resolve() == RUNTIME.resolve(),
                "PINNED_PYTHON312_REQUIRED")
        require(self.args.frozen == FROZEN, "WRONG_PREREGISTERED_FROZEN_COMMIT")
        require(not any(name.upper().startswith("MEMORY_BASE_SECRET_") or name.upper() in
                        {"MEMORY_BASE_ROOT_KEY", "MEMORY_BASE_OV_KEY", "MEMORY_BASE_TOKENS", "MEMORY_BASE_TOKEN"}
                        for name in os.environ), "INHERITED_CREDENTIAL_ENV_FORBIDDEN")
        require(Path(self.deploy.__file__).resolve().is_relative_to(ROOT), "WRONG_SOURCE_IMPORT")
        require(not git("diff", self.args.frozen, "--", "memory_base"), "CODE_DIFFERS_FROM_FROZEN")
        require(not git("status", "--porcelain", "--", "memory_base"), "CODE_WORKTREE_DIRTY")
        require(not git("diff", "HEAD", "--", "experiments/memory-base-recovery/run_native.py",
                        "experiments/memory-base-recovery/README.md"), "PREREGISTRATION_WORKTREE_DIRTY")
        versions = {name: importlib.metadata.version(name) for name in self.deploy.PINS}
        require(versions == self.deploy.PINS, "RUNTIME_PINS_MISMATCH")
        model = Path(self.args.model).resolve(strict=True)
        require(model == MODEL.resolve(), "REGISTERED_MODEL_REQUIRED")
        with model.open("rb") as stream:
            require(stream.read(4) == b"GGUF", "GGUF_REQUIRED")
        model_digest = file_hash(model)
        require(self.source.drive.lower() != self.target.drive.lower(), "CROSS_VOLUME_REQUIRED")
        require(not self.c_root.exists() and not self.d_root.exists(), "FRESH_ASSETS_REQUIRED")
        self.c_root.mkdir(parents=True)
        self.d_root.mkdir(parents=True)
        ports = free_ports(6)
        for home, selected in [(self.source, ports[:3]), (self.target, ports[3:])]:
            home.mkdir()
            config = {"schema": 1, "python": str(Path(sys.executable).resolve()), "model": str(model),
                      "ports": dict(zip(self.deploy.COMPONENTS, selected)), "source": str(ROOT),
                      "account": "personal", "user": "gateway", "versions": versions,
                      "model_sha256": model_digest}
            self.deploy._write_json(home / "deployment.json", config)
            self.deploy.write_upstream_config(home, config)
            for name in ("root-key", *["host-" + profile for profile in self.deploy.PROFILES]):
                self.deploy._ensure_secret(name, home)
        (self.target / "data").mkdir()
        require(not any((self.target / "data").iterdir()), "TARGET_NOT_EMPTY")
        namespaces = [self.credentials._target("root-key", h).split("/")[1] for h in (self.source, self.target)]
        require(len(set(namespaces)) == 2, "VAULT_NAMESPACE_NOT_DISTINCT")
        for name in ("root-key", *["host-" + p for p in self.deploy.PROFILES]):
            require(self.credentials.get_secret(name, self.source) != self.credentials.get_secret(name, self.target),
                    "FRESH_VAULT_KEYS_NOT_DISTINCT")
        self.report.update(versions=versions, python_version=".".join(map(str, sys.version_info[:3])),
                           model_sha256=model_digest, vault_namespaces=namespaces,
                           initialization="verified shared runtime; install-shaped local configuration and new vault credentials")
        self.fixture.mkdir()
        (self.fixture / "docs").mkdir()
        (self.fixture / "README.md").write_bytes(b"# fixture\r\nOriginal source.\r\n")
        (self.fixture / "docs" / "gone.md").write_bytes(b"# removable\n")
        subprocess.run(["git", "init", "--quiet", str(self.fixture)], check=True)
        subprocess.run(["git", "-C", str(self.fixture), "add", "--", "README.md", "docs/gone.md"], check=True)
        subprocess.run(["git", "-C", str(self.fixture), "-c", "user.name=Memory Acceptance", "-c", "user.email=acceptance@invalid.local", "commit", "--quiet", "-m", "fixture seed"], check=True)
        self.deploy.source_add(self.source, "fixture", str(self.fixture))
        access = {"schema": 1, "profiles": {
            "reader": {"read_scopes": [ARTICLE_SCOPE, "projects/fixture"], "write_scopes": []},
            "dsh": {"read_scopes": [ARTICLE_SCOPE], "write_scopes": [ARTICLE_SCOPE]}}}
        self.deploy._write_json(self.source / "access.json", access)
        state = self.lifecycle(self.source, self.deploy.start)
        require(state["status"] == "ready", "SOURCE_NOT_READY")
        return {"ports": [self.deploy.load_config(h)["ports"] for h in (self.source, self.target)],
                "source_import": True, "empty_target_data": True, "distinct_vault_namespaces": True}

    def captures(self):
        self.allowed = self.api(self.source, "add", self.original)
        require(self.allowed["path"].startswith(ARTICLE_SCOPE + "/"), "OBSERVED_DAY_NOT_PATH")
        read = self.api(self.source, "get", {"uri": self.allowed["uri"]}, "reader")
        require(sha(read["content"]) == sha(self.allowed_body) == read["revision"], "ORIGINAL_HASH_MISMATCH")
        replay = self.api(self.source, "add", {**self.original, "scope": "captures/" + DAY})
        require(replay["status"] == "exists" and replay["uri"] == self.allowed["uri"], "SCOPE_REPLAY_CHANGED_IDENTITY")
        self.api(self.source, "add", {**self.original, "observed_on": "2026-10-11"}, expected=409)
        self.api(self.source, "get", {"uri": self.allowed["uri"]}, profile=None, expected=401)
        self.api(self.source, "add", self.original, "reader", expected=403)
        dsh = self.api(self.source, "add", {**self.original, "request_id": "mb85-dsh", "content": "# 第二宿主\r\n跨宿主共享读取。\r\n"}, "dsh")
        self.api(self.source, "get", {"uri": dsh["uri"]})
        self.dsh_uri = dsh["uri"]
        self.outsiders = []
        for index in range(3):
            self.outsiders.append(self.api(self.source, "add", {"request_id": "mb85-outside-" + str(index), "title": "范围外", "content": QUERY, "observed_on": "2026-10-11"}))
        self.api(self.source, "add", {**self.original, "request_id": "mb85-write-outside", "kind": "note"}, "dsh", expected=403)
        return {"uri": self.allowed["uri"], "path": self.allowed["path"], "original_sha256": sha(self.allowed_body), "profiles": ["codex", "dsh", "reader"], "outside_count": len(self.outsiders)}

    def legacy(self):
        request = {"request_id": "mb85-legacy", "content": "# 旧 capture\r\n兼容原文。\r\n", "title": "旧序列化", "kind": "note", "source_url": None, "source_id": None, "conversation_id": None}
        principal = "codex"
        capture_id = sha(canonical({"principal": principal, "request_id": request["request_id"]}))
        self.legacy_uri = "viking://resources/captures/" + capture_id + "/content.md"
        metadata = {"schema_version": 1, "principal": principal, "request_hash": sha(canonical({"principal": principal, **request})), "content_hash": sha(request["content"]), "created_at": "2026-10-10T00:00:00+00:00", **{key: value for key, value in request.items() if key != "content"}}
        self.client(self.source).create(self.legacy_uri.replace("content.md", "capture.json"), canonical(metadata))
        self.client(self.source).create(self.legacy_uri, request["content"], capture=True)
        replay = self.api(self.source, "add", request)
        require(replay["status"] == "exists" and replay["uri"] == self.legacy_uri, "LEGACY_REPLAY_FAILED")
        self.api(self.source, "get", {"uri": self.legacy_uri}, "reader", expected=403)
        return {"legacy_uri": self.legacy_uri, "legacy_request_sha256": metadata["request_hash"], "replay": replay["status"]}

    def scopes(self, home):
        forbidden = {entry["uri"] for entry in self.outsiders} | {self.legacy_uri}
        for uri in forbidden:
            self.api(home, "get", {"uri": uri}, "reader", expected=403)
        self.api(home, "list", {"scope": "captures/2026-10-11"}, "reader", expected=403)
        listed = self.api(home, "list", {"scope": "captures"}, "reader")
        require(not forbidden.intersection(entry["uri"] for entry in listed["items"]), "LIST_SCOPE_LEAK")
        require({self.allowed["uri"], self.dsh_uri}.issubset(entry["uri"] for entry in listed["items"]), "LIST_MISSING_ALLOWED_ORIGINAL")
        native_top1 = self.client(home).find(QUERY, 1, "viking://resources/captures")
        require(len(native_top1) == 1 and native_top1[0]["uri"] in forbidden, "UNRESTRICTED_NATIVE_CONTROL_NOT_OUTSIDE")
        filtered_top1 = self.client(home).find(QUERY, 1, "viking://resources/captures", allowed_uris=[self.allowed["uri"], self.dsh_uri])
        require(len(filtered_top1) == 1 and filtered_top1[0]["uri"] == self.allowed["uri"], "NATIVE_FILTER_CONTROL_FAILED")
        searched = self.api(home, "search", {"query": QUERY, "limit": 1, "scope": "captures"}, "reader")
        require(len(searched["items"]) == 1 and searched["items"][0]["uri"] == self.allowed["uri"], "NATIVE_PREFILTER_TOP1_FAILED")
        return {"list_count": len(listed["items"]), "top1_uri": searched["items"][0]["uri"],
                "native_unrestricted_top1_uri": native_top1[0]["uri"], "native_filtered_top1_uri": filtered_top1[0]["uri"],
                "effective_scope": searched["effective_scope"]}

    def sources(self):
        old = self.api(self.source, "get", {"uri": "projects/fixture/README.md"}, "reader")
        body = "# changed\r\nmb85sourcechanged 中文原始字节。\r\n".encode("utf-8")
        (self.fixture / "README.md").write_bytes(body)
        read = self.api(self.source, "get", {"uri": "projects/fixture/README.md"}, "reader")
        require(read["revision"] == sha(body) == sha(read["content"]), "LIVE_REVISION_MISMATCH")
        self.api(self.source, "get", {"uri": "projects/fixture/README.md", "expected_revision": old["revision"]}, "reader", expected=409)
        (self.fixture / "docs" / "new.md").write_bytes(b"# mb85newsource\r\nnew item\r\n")
        (self.fixture / "docs" / "gone.md").unlink()
        listed = self.api(self.source, "list", {"scope": "projects/fixture"}, "reader")
        paths = {entry["path"] for entry in listed["items"]}
        require("projects/fixture/docs/new.md" in paths and "projects/fixture/docs/gone.md" not in paths, "LIVE_ADD_DELETE_NOT_REFLECTED")
        searched = self.api(self.source, "search", {"query": "mb85sourcechanged", "scope": "projects/fixture"}, "reader")
        require(any(entry["revision"] == sha(body) for entry in searched["items"]), "LIVE_SEARCH_NOT_UPDATED")
        status = subprocess.run(["git", "-C", str(self.fixture), "status", "--porcelain"], capture_output=True, text=True, check=True).stdout
        require(bool(status), "FIXTURE_NOT_DIRTY")
        offline = self.fixture.with_name("project-offline")
        require(self.fixture.resolve().is_relative_to(self.c_root.resolve()) and
                offline.parent.resolve() == self.c_root.resolve() and not offline.exists(), "OFFLINE_MOVE_BOUNDARY")
        self.fixture.rename(offline)
        try:
            self.api(self.source, "get", {"uri": "projects/fixture/README.md"}, "reader", expected=503)
            self.api(self.source, "search", {"query": "source", "scope": "projects/fixture"}, "reader", expected=503)
        finally:
            require(offline.resolve().is_relative_to(self.c_root.resolve()) and
                    self.fixture.parent.resolve() == self.c_root.resolve(), "ONLINE_MOVE_BOUNDARY")
            offline.rename(self.fixture)
        self.api(self.source, "get", {"uri": "projects/fixture/README.md"}, "reader")
        oversized = self.fixture / "docs" / "oversized.md"
        oversized.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        try:
            self.api(self.source, "list", {"scope": "projects/fixture"}, "reader", expected=413)
            self.api(self.source, "search", {"query": "source", "scope": "projects/fixture"}, "reader", expected=413)
        finally:
            oversized.unlink()
        return {"live_revision": read["revision"], "source_files": sorted(paths), "uncommitted_changes": True, "offline_status": 503, "size_limit_status": 413}

    def junction(self):
        outside = self.c_root / "outside"
        outside.mkdir()
        (outside / "secret.md").write_bytes(b"# outside boundary\n")
        link = self.fixture / "docs" / "escaped"
        result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
        require(result.returncode == 0 and bool(link.lstat().st_file_attributes & 0x400), "JUNCTION_CREATION_FAILED")
        try:
            self.api(self.source, "get", {"uri": "projects/fixture/docs/escaped/secret.md"}, "reader", expected=403)
            self.api(self.source, "list", {"scope": "projects/fixture"}, "reader", expected=403)
        finally:
            require(link.parent.resolve().is_relative_to(self.fixture.resolve()) and bool(link.lstat().st_file_attributes & 0x400), "JUNCTION_CLEANUP_BOUNDARY")
            os.rmdir(link)
        require((outside / "secret.md").is_file(), "JUNCTION_TARGET_CHANGED")
        return {"actual_windows_reparse": True, "outside_target_intact": True}

    def native_state(self):
        self.private_uri = "viking://resources/native-proof/private.md"
        self.client(self.source).create(self.private_uri, "# Native ACL proof\nRestricted content.\n")
        code, user = self.native(self.source, "/api/v1/admin/accounts/personal/users", "POST", {"user_id": "probe-reader", "role": "user"}, key_name="root-key")
        require(code == 200 and "user_key" in user["result"], "NATIVE_USER_CREATE_FAILED")
        self.credentials.set_secret("native-probe-key", user["result"]["user_key"], self.source)
        code, _ = self.native(self.source, "/api/v1/acl", "PUT", {"uri": self.private_uri, "acl_mode": "restricted", "entries": [{"principal": "user:gateway", "level": "manage"}]})
        require(code == 200, "NATIVE_ACL_SET_FAILED")
        self.assert_native_denied(self.source)
        code, acl = self.native(self.source, "/api/v1/acl?" + urllib.parse.urlencode({"uri": self.private_uri}))
        require(code == 200, "NATIVE_ACL_READ_FAILED")
        self.acl_hash = sha(canonical(acl["result"]))
        self.session_id = "mb85-session"
        code, _ = self.native(self.source, "/api/v1/sessions", "POST", {"session_id": self.session_id, "auto_commit_policy": None})
        require(code == 200, "SESSION_CREATE_FAILED")
        code, _ = self.native(self.source, "/api/v1/sessions/" + self.session_id + "/messages", "POST", {"role": "user", "content": "mb85 retained session context"})
        require(code == 200, "SESSION_MESSAGE_FAILED")
        self.context_hash = self.context_digest(self.source)
        self.identities_hash = self.identity_digest(self.source)
        return {"native_denial_status": 403, "acl_sha256": self.acl_hash, "session_context_sha256": self.context_hash,
                "native_identity_roster_sha256": self.identities_hash}

    def assert_native_denied(self, home):
        code, _ = self.native(home, "/api/v1/content/read?" + urllib.parse.urlencode({"uri": self.private_uri, "raw": "true"}), user="probe-reader", key_name="native-probe-key")
        require(code == 403, "NATIVE_ACL_DID_NOT_DENY")

    def context_digest(self, home):
        code, result = self.native(home, "/api/v1/sessions/" + self.session_id + "/context")
        require(code == 200 and "mb85 retained session context" in canonical(result["result"]), "SESSION_CONTEXT_NOT_RETAINED")
        return sha(canonical(result["result"]))

    def identity_digest(self, home):
        code, result = self.native(home, "/api/v1/admin/accounts/personal/users", key_name="root-key")
        require(code == 200 and isinstance(result["result"], list), "NATIVE_IDENTITY_ROSTER_FAILED")
        roster = sorted(({"user_id": item["user_id"], "role": item["role"]} for item in result["result"]),
                        key=lambda item: item["user_id"])
        require({"gateway", "probe-reader"}.issubset(item["user_id"] for item in roster), "NATIVE_IDENTITIES_MISSING")
        return sha(canonical(roster))

    @contextmanager
    def observe_cold(self, *, race=False):
        """Observe the real cold boundary, preserving all actual lifecycle/copy behavior."""
        original = self.backup._cold
        @contextmanager
        def observer(home, config, timeout):
            with original(home, config, timeout):
                require(not self.running(home), "COLD_OBSERVER_FOUND_RUNNING_COMPONENT")
                if race:
                    self.racing_snapshot.mkdir()
                else:
                    self.cold_files, self.cold_dirs = inventory(home / "data")
                yield
        self.backup._cold = observer
        try:
            yield
        finally:
            self.backup._cold = original

    def cold_backup(self):
        before = self.running(self.source)
        with self.observe_cold():
            self.backup.create_backup(self.source, self.snapshot)
        self.record_components(self.source, "backup_restarted")
        require(self.running(self.source) == before, "BACKUP_DID_NOT_RESTORE_RUNNING_SET")
        manifest = json.loads((self.snapshot / "manifest.json").read_text(encoding="utf-8"))
        actual = []
        for path in self.snapshot.rglob("*"):
            if path.is_file() and path != self.snapshot / "manifest.json":
                actual.append({"path": path.relative_to(self.snapshot).as_posix(), "size": path.stat().st_size, "sha256": file_hash(path)})
        require(sorted(actual, key=lambda item: item["path"]) == manifest["files"], "MANIFEST_FILES_NOT_COMPLETE")
        actual_dirs = sorted(path.relative_to(self.snapshot).as_posix() for path in self.snapshot.rglob("*") if path.is_dir())
        require(actual_dirs == manifest["directories"], "MANIFEST_DIRS_NOT_COMPLETE")
        copied_data = [{**item, "path": item["path"][5:]} for item in actual if item["path"].startswith("data/")]
        copied_dirs = sorted(name[5:] for name in actual_dirs if name.startswith("data/"))
        require(sorted(copied_data, key=lambda item: item["path"]) == self.cold_files and copied_dirs == self.cold_dirs,
                "COLD_SOURCE_NOT_COMPLETELY_BACKED_UP")
        for name in ("sources.json", "access.json"):
            require((self.snapshot / name).read_bytes() == (self.source / name).read_bytes(), "CONTROL_STATE_NOT_BACKED_UP")
        try:
            self.backup.create_backup(self.source, self.snapshot)
        except ValueError:
            pass
        else:
            raise AssertionError("EXISTING_SNAPSHOT_OVERWRITTEN")
        require(self.running(self.source) == before, "FAILED_BACKUP_CHANGED_RUNNING_SET")
        try:
            with self.observe_cold(race=True):
                self.backup.create_backup(self.source, self.racing_snapshot)
        except FileExistsError:
            pass
        else:
            raise AssertionError("COLD_RACE_DESTINATION_ACCEPTED")
        self.record_components(self.source, "failed_cold_backup_restarted")
        require(self.running(self.source) == before and not any(self.racing_snapshot.iterdir()), "COLD_FAILURE_DID_NOT_RESTORE_RUNNING_SET")
        queue_evidence = []
        for path in (self.snapshot / "data").rglob("*.db"):
            if "queue" not in path.as_posix().lower():
                continue
            with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as database:
                require(database.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "QUEUE_DB_INTEGRITY_FAILED")
                tables = database.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            queue_evidence.append({"path": path.relative_to(self.snapshot).as_posix(), "sha256": file_hash(path), "tables": len(tables), "integrity": "ok"})
        require(bool(queue_evidence), "ACTUAL_QUEUE_FILE_NOT_FOUND")
        return {"manifest_sha256": file_hash(self.snapshot / "manifest.json"), "file_count": len(actual),
                "directories": len(actual_dirs), "cold_source_inventory_sha256": sha(canonical([self.cold_files, self.cold_dirs])),
                "complete_cold_source_copy": True, "running_set_restored": list(before),
                "preflight_and_cold_failure_running_set_restored": True, "queue": queue_evidence}

    def damage_and_restore(self):
        shutil.copytree(self.snapshot, self.damaged)
        manifest = json.loads((self.damaged / "manifest.json").read_text(encoding="utf-8"))
        item = next(item for item in manifest["files"] if item["path"].startswith("data/") and item["size"] > 0)
        with (self.damaged / item["path"]).open("ab") as stream:
            stream.write(b"damage")
        target_config = (self.target / "deployment.json").read_bytes()
        try:
            self.backup.restore_backup(self.target, self.damaged)
        except ValueError:
            pass
        else:
            raise AssertionError("DAMAGED_SNAPSHOT_ACCEPTED")
        require(not any((self.target / "data").iterdir()), "DAMAGED_RESTORE_PUBLISHED_DATA")
        require(not (self.target / "sources.json").exists() and not (self.target / "restore-state.json").exists(), "DAMAGED_RESTORE_PUBLISHED_CONTROL")
        result = self.backup.restore_backup(self.target, self.snapshot)
        require((self.target / "deployment.json").read_bytes() == target_config, "TARGET_INSTALL_CONFIG_CHANGED")
        require(inventory(self.target / "data") == (self.cold_files, self.cold_dirs), "RESTORE_PUBLISHED_DATA_DIFFERS_FROM_SNAPSHOT")
        for name in ("sources.json", "access.json"):
            require((self.target / name).read_bytes() == (self.snapshot / name).read_bytes(), "RESTORE_CONTROL_DIFFERS_FROM_SNAPSHOT")
        state = self.lifecycle(self.target, self.deploy.start)
        require(state["status"] == "ready", "RESTORED_TARGET_NOT_READY")
        for name in ("root-key", "upstream-key", *["host-" + p for p in self.deploy.PROFILES]):
            require(self.credentials.get_secret(name, self.source) != self.credentials.get_secret(name, self.target), "TARGET_KEYS_NOT_REISSUED")
        require(not json.loads((self.target / "restore-state.json").read_text())["identity_reissue_pending"], "IDENTITY_REISSUE_STILL_PENDING")
        read = self.api(self.target, "get", {"uri": self.allowed["uri"]}, "reader")
        require(sha(read["content"]) == sha(self.allowed_body), "RESTORED_ORIGINAL_CHANGED")
        restored_scope = self.scopes(self.target)
        code, acl = self.native(self.target, "/api/v1/acl?" + urllib.parse.urlencode({"uri": self.private_uri}))
        require(code == 200 and sha(canonical(acl["result"])) == self.acl_hash, "RESTORED_ACL_CHANGED")
        code, user = self.native(self.target, "/api/v1/admin/accounts/personal/users/probe-reader/key", "POST", {}, key_name="root-key")
        require(code == 200, "RESTORED_NATIVE_KEY_REISSUE_FAILED")
        self.credentials.set_secret("native-probe-key", user["result"]["user_key"], self.target)
        self.assert_native_denied(self.target)
        require(self.context_digest(self.target) == self.context_hash, "RESTORED_SESSION_CONTEXT_CHANGED")
        require(self.identity_digest(self.target) == self.identities_hash, "RESTORED_NATIVE_IDENTITIES_CHANGED")
        project = self.api(self.target, "get", {"uri": "projects/fixture/README.md"}, "reader")
        require(project["revision"] == file_hash(self.fixture / "README.md"), "RESTORED_LIVE_SOURCE_UNREADABLE")
        require(file_hash(self.snapshot / "manifest.json") == self.report["checks"][-1]["evidence"]["manifest_sha256"], "ORIGINAL_SNAPSHOT_CHANGED")
        return {"target_config_preserved": True, "new_vault_keys": True, "restored_original_sha256": sha(read["content"]),
                "published_inventory_exact": True, "native_acl_preserved": True, "native_denial_status": 403,
                "native_identity_roster_sha256": self.identities_hash, "session_context_sha256": self.context_hash,
                "live_source_revision": project["revision"], "scope": restored_scope,
                "damaged_copy_rejected": True, "damaged_target_unpublished": True}

    async def mcp_checks(self):
        from mcp import ClientSession, StdioServerParameters
        import mcp.client.stdio as stdio_module
        from mcp.client.stdio import stdio_client
        config = self.deploy.load_config(self.target)
        parameters = StdioServerParameters(command=sys.executable, args=["-m", "memory_base.mcp_stdio", "--profile", "reader", "--home", str(self.target), "--url", f"http://127.0.0.1:{config['ports']['gateway']}"], env=self.deploy.clean_env({"PYTHONPATH": str(ROOT)}))
        original_spawn = stdio_module._create_platform_compatible_process
        async def observed_spawn(*args, **kwargs):
            process = await original_spawn(*args, **kwargs)
            actual = self.deploy._process_info(process.pid)
            record = {"pid": process.pid, **(actual or {})}
            self.mcp_process_owned = bool(actual and record["command"].strip() ==
                                          subprocess.list2cmdline([parameters.command, *parameters.args]))
            self.report["process_history"].append({"phase": "mcp_stdio", "home": str(self.target), "records": {"stdio": record}})
            self.save()
            return process
        stdio_module._create_platform_compatible_process = observed_spawn
        try:
            with (self.target / "logs" / "mcp-acceptance.err.log").open("a", encoding="utf-8") as errlog:
                async with stdio_client(parameters, errlog=errlog) as streams:
                    require(self.mcp_process_owned, "MCP_PROCESS_OWNERSHIP_FAILED")
                    async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=180)) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        require({tool.name for tool in tools.tools} == {"memory_add", "memory_get", "memory_search", "memory_list"}, "MCP_TOOL_LIST_WRONG")
                        def value(result):
                            structured = getattr(result, "structuredContent", None)
                            return structured if isinstance(structured, dict) else json.loads("".join(c.text for c in result.content if c.type == "text"))
                        def denied(result, code):
                            message = "".join(c.text for c in result.content if c.type == "text")
                            require(result.isError and "HTTP 403" in message and code in message, "MCP_EXPECTED_DENIAL_MISSING")
                        result = await session.call_tool("memory_get", {"uri": self.allowed["uri"]})
                        require(not result.isError and sha(value(result)["content"]) == sha(self.allowed_body), "MCP_READ_HASH_FAILED")
                        denied(await session.call_tool("memory_add", self.original), "WRITE_FORBIDDEN")
                        denied(await session.call_tool("memory_get", {"uri": self.outsiders[0]["uri"]}), "READ_SCOPE_FORBIDDEN")
                        denied(await session.call_tool("memory_list", {"scope": "captures/2026-10-11"}), "READ_SCOPE_FORBIDDEN")
                        result = await session.call_tool("memory_search", {"query": QUERY, "scope": "captures", "limit": 1})
                        require(not result.isError and value(result)["items"][0]["uri"] == self.allowed["uri"], "MCP_SEARCH_SCOPE_FAILED")
                        result = await session.call_tool("memory_list", {"scope": ARTICLE_SCOPE})
                        require(not result.isError and {self.allowed["uri"], self.dsh_uri}.issubset(
                            entry["uri"] for entry in value(result)["items"]), "MCP_ALLOWED_LIST_MISSING")
        finally:
            stdio_module._create_platform_compatible_process = original_spawn
        return {"tools": sorted(tool.name for tool in tools.tools), "reader_hash": sha(self.allowed_body), "scope_denials": True}

    def autostart_command(self):
        self.registry_before = run_registry_hash()
        self.lifecycle(self.source, self.deploy.stop)
        first = self.autostart.enable(self.source, sys.executable)
        self.registered = True
        require(first["enabled"] and first["changed"], "AUTOSTART_ENABLE_FAILED")
        require(not self.autostart.enable(self.source, sys.executable)["changed"], "AUTOSTART_NOT_IDEMPOTENT")
        state = self.autostart.status(self.source)
        require(state["enabled"] and not state["conflict"], "AUTOSTART_STATUS_FAILED")
        self.manual = subprocess.Popen(windows_argv(state["command"]), cwd=self.source, env=self.deploy.clean_env({"PYTHONPATH": str(ROOT)}), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        actual = self.deploy._process_info(self.manual.pid)
        self.manual_record = {"pid": self.manual.pid, **(actual or {})}
        require(bool(actual) and actual["command"].strip() == state["command"], "PYTHONW_PROCESS_RECORD_MISMATCH")
        self.report["process_history"].append({"phase": "manual_autostart", "home": str(self.source), "records": {"launcher": self.manual_record}})
        self.save()
        self.manual.wait(timeout=180)
        require(self.manual.returncode == 0 and self.deploy.status(self.source)["status"] == "ready", "PYTHONW_START_COMMAND_FAILED")
        self.record_components(self.source, "manual_autostart_ready")
        require(self.autostart.disable(self.source)["changed"], "AUTOSTART_DISABLE_FAILED")
        self.registered = False
        require(not self.autostart.disable(self.source)["changed"], "AUTOSTART_DISABLE_NOT_IDEMPOTENT")
        require(run_registry_hash() == self.registry_before, "RUN_REGISTRY_BASELINE_CHANGED")
        return {"manual_pythonw_ready": True, "run_baseline_sha256": self.registry_before, "baseline_restored": True, "actual_logon_tested": False}

    def cleanup(self):
        errors = []
        if self.manual is not None and self.manual.poll() is None:
            try:
                require(self.deploy.owned_process(self.manual_record), "LAUNCHER_STOP_OWNERSHIP_MISMATCH")
                self.manual.terminate()
                self.manual.wait(timeout=15)
            except BaseException as error:
                errors.append({"action": "stop_launcher", "error": type(error).__name__})
        if self.registered:
            try:
                self.autostart.disable(self.source)
            except BaseException as error:
                errors.append({"action": "disable_autostart", "error": type(error).__name__})
        for home in (self.source, self.target):
            if (home / "deployment.json").exists():
                try:
                    self.lifecycle(home, self.deploy.stop)
                    require(not self.running(home), "OWNED_COMPONENT_STILL_RUNNING")
                except BaseException as error:
                    errors.append({"action": "stop", "home": str(home), "error": type(error).__name__})
        if hasattr(self, "registry_before") and run_registry_hash() != self.registry_before:
            errors.append({"action": "registry_baseline", "error": "RunBaselineChanged"})
        if "model_sha256" in self.report and file_hash(Path(self.args.model)) != self.report["model_sha256"]:
            errors.append({"action": "shared_model_readonly", "error": "ModelChanged"})
        self.report["cleanup"] = {"passed": not errors, "errors": errors, "assets_retained": True}
        self.report["passed"] = not errors and all(item["passed"] for item in self.report["checks"]) and len(self.report["checks"]) == 11
        self.save()

    def run(self):
        try:
            self.check("independent_deployments", self.prepare)
            self.check("captures_exact_http", self.captures)
            self.check("legacy_schema1_replay", self.legacy)
            self.check("native_prefilter_scope", lambda: self.scopes(self.source))
            self.check("live_sources", self.sources)
            self.check("windows_junction", self.junction)
            self.check("native_acl_and_session", self.native_state)
            self.check("verified_cold_backup", self.cold_backup)
            self.check("empty_target_restore", self.damage_and_restore)
            self.check("actual_mcp_stdio", lambda: asyncio.run(self.mcp_checks()))
            self.check("actual_autostart_command", self.autostart_command)
        except BaseException:
            pass
        finally:
            self.cleanup()
        print("RESULT " + str(self.report_path), flush=True)
        return 0 if self.report["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Start only after Lead has released frozen-code acceptance")
    parser.add_argument("--frozen", help="Full frozen implementation commit SHA")
    parser.add_argument("--model", help="Existing registered GGUF absolute path")
    args = parser.parse_args()
    if not args.execute or not args.frozen or not args.model:
        parser.error("execution requires --execute, --frozen and --model")
    if not re.fullmatch(r"[0-9a-f]{40}", args.frozen):
        parser.error("--frozen must be the full 40-character commit")
    return Acceptance(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
