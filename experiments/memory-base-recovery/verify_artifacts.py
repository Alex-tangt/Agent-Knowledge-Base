"""Post-hoc, WAL-aware verification on a retained snapshot's independent copy."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import uuid

sys.dont_write_bytecode = True
REPORTS = Path(__file__).resolve().parent
ASSETS = Path(r"D:\python_work\work2026-4\memory-base-recovery-assets").resolve()


def require(condition, code):
    if not condition:
        raise AssertionError(code)


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def inventory(root):
    files, directories = [], []
    for path in root.rglob("*"):
        require(not bool(path.lstat().st_file_attributes & 0x400), "REPARSE_FORBIDDEN")
        name = path.relative_to(root).as_posix()
        if path.is_dir():
            directories.append(name)
        elif path.is_file() and name != "manifest.json":
            files.append({"path": name, "size": path.stat().st_size, "sha256": digest(path)})
    return sorted(files, key=lambda item: item["path"]), sorted(directories)


def registry_hash():
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
    raw = json.dumps(sorted(values), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify(report_path, result):
    require(report_path.parent == REPORTS and report_path.name.startswith("results-"), "REPORT_BOUNDARY")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    run_id = report["run_id"]
    require(bool(re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{6}", run_id)), "RUN_ID_INVALID")
    require(report.get("passed") and report["cleanup"]["passed"], "NATIVE_RUN_NOT_COMPLETE_AND_PASSED")
    checks = {item["name"]: item for item in report["checks"]}
    run_root = ASSETS / run_id
    snapshot = Path(report["snapshot"]).resolve(strict=True)
    require(snapshot == run_root / "snapshot", "SNAPSHOT_BOUNDARY")
    manifest_path = snapshot / "manifest.json"
    manifest_hash = digest(manifest_path)
    require(manifest_hash == checks["verified_cold_backup"]["evidence"]["manifest_sha256"], "MANIFEST_CHANGED")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    before = inventory(snapshot)
    require(before == (manifest["files"], manifest["directories"]), "SNAPSHOT_INVENTORY_DIFFERS")
    probe = run_root / ("wal-probe-" + uuid.uuid4().hex[:8])
    require(probe.parent.resolve() == run_root.resolve() and not probe.exists(), "NEW_PROBE_BOUNDARY")
    probe.mkdir()
    result.update(native_report_sha256=digest(report_path), frozen_code=report["frozen_code"],
                  script_commit=report["script_commit"], manifest_sha256=manifest_hash,
                  file_count=len(before[0]), directory_count=len(before[1]), probe=str(probe), queues=[])
    queue_files = [item for item in manifest["files"] if "queue" in item["path"].lower() and item["path"].endswith(".db")]
    require(bool(queue_files), "QUEUE_FILE_MISSING")
    for index, item in enumerate(queue_files):
        original = snapshot / item["path"]
        selected = probe / ("queue-" + str(index))
        selected.mkdir()
        copied = []
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(original) + suffix)
            if path.exists():
                destination = selected / path.name
                shutil.copyfile(path, destination)
                require(digest(path) == digest(destination), "PROBE_COPY_MISMATCH")
                copied.append({"suffix": suffix or "db", "size": path.stat().st_size, "sha256": digest(path)})
        # A regular independent copy applies the retained WAL. immutable=1 on the
        # original DB would ignore those frames. Closing may checkpoint only this copy.
        with sqlite3.connect(selected / original.name) as database:
            integrity = [row[0] for row in database.execute("PRAGMA integrity_check").fetchall()]
            tables = database.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            journal_mode = database.execute("PRAGMA journal_mode").fetchone()[0]
        require(integrity == ["ok"] and bool(tables), "WAL_AWARE_QUEUE_INTEGRITY_FAILED")
        result["queues"].append({"path": item["path"], "copied_files": copied, "integrity": "ok",
                                  "table_count": len(tables), "journal_mode": journal_mode,
                                  "wal_present": any(item["suffix"] == "-wal" for item in copied)})
    require(inventory(snapshot) == before and digest(manifest_path) == manifest_hash, "ORIGINAL_SNAPSHOT_MODIFIED")
    for index, value in enumerate(report["homes"]):
        home = Path(value).resolve(strict=True)
        expected = (Path.home() / ".memory-base-acceptance" / run_id / "source").resolve() if index == 0 else run_root / "target"
        require(home == expected, "HOME_BOUNDARY")
        require(json.loads((home / "processes.json").read_text(encoding="utf-8")) == {}, "OWNED_RECORDS_NOT_CLEARED")
        deployment = json.loads((home / "deployment.json").read_text(encoding="utf-8"))
        require(deployment["ports"] == checks["independent_deployments"]["evidence"]["ports"][index], "DEPLOYMENT_PORTS_CHANGED")
    baseline = checks["actual_autostart_command"]["evidence"]["run_baseline_sha256"]
    require(registry_hash() == baseline, "REGISTRY_BASELINE_CHANGED")
    result.update(snapshot_unchanged=True, manifest_fully_recomputed=True,
                  both_owned_process_records_empty=True, registry_baseline_sha256=baseline,
                  registry_baseline_restored=True, passed=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = args.report.resolve(strict=True)
    output = REPORTS / report.name.replace("results-", "verification-", 1)
    require(output != report and not output.exists(), "NEW_VERIFICATION_OUTPUT_REQUIRED")
    result = {"schema": 1, "post_hoc": True, "at": datetime.now(timezone.utc).isoformat(),
              "verifier_sha256": digest(Path(__file__)),
              "limitations": ["immutable PRAGMA in preregistered run covered base DB only",
                              "WAL-aware integrity is checked on a new independent copy",
                              "no pending-task execution or exactly-once claim"]}
    try:
        verify(report, result)
    except BaseException as error:
        result.update(passed=False, failure=str(error) if isinstance(error, AssertionError) else type(error).__name__)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("VERIFICATION " + ("PASSED " if result["passed"] else "FAILED ") + str(output))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
