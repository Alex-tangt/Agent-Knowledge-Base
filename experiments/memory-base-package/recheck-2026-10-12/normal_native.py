"""Independent normal-environment native run; preserve every R4 FAIL artifact."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from run_recheck import MAIN, FROZEN, HERE, REPO, clean_env


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert Path(sys.executable).resolve() == MAIN.resolve()
    asset_root = Path(r"C:\Users\Tan\.mb86\r4")
    assert asset_root.resolve() == asset_root
    home = asset_root / "f"
    store = asset_root / "normal-native"
    assert not store.exists(), "Preserve all previous normal-native evidence"
    original_paths = [asset_root / name for name in ("results.json", "native.json", "results-before-fresh.json")]
    original_hashes = {str(path): digest(path) for path in original_paths}
    acceptance = json.loads(original_paths[0].read_text(encoding="utf-8"))
    assert acceptance["frozen_sha"] == FROZEN and acceptance["overall"] == "FAIL"
    assert acceptance["continuation"]["fresh_install_gate"] == "PASS"
    config = json.loads((home / "deployment.json").read_text(encoding="utf-8"))
    python = home / "runtime/Scripts/python.exe"
    assert Path(config["python"]) == python
    assert Path(config["source"]) == home / "runtime/Lib/site-packages"
    marker = home / "data/r2-preserved-marker.txt"
    assert digest(marker) == acceptance["fresh_case"]["marker_sha256"]
    assert sorted(path.name for path in (home / "data").iterdir()) == [marker.name]
    assert not (home / "processes.json").exists() or json.loads((home / "processes.json").read_text()) == {}
    census = json.loads((HERE / "complete_import_census.json").read_text(encoding="utf-8"))
    packages = census["identities"]["fresh_never_started"]["packages"]
    assert packages["lark-oapi"]["actual_file_counts"]["pyc"] == 0
    assert packages["volcengine-python-sdk"]["actual_file_counts"]["pyc"] == 0
    store.mkdir()
    command = list(map(str, [python, HERE / "native_probe.py", "--home", home,
                            "--store", store, "--asset-root", asset_root]))
    env = clean_env()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    assert all(not key.upper().startswith("MEMORY_") for key in env)
    assert "PYTHONPATH" not in env and "PYTHONHOME" not in env and "PYTHONDONTWRITEBYTECODE" not in env
    report = {"frozen_sha": FROZEN, "home": str(home), "report_root": str(store),
        "script_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "script_sha256": digest(Path(__file__)), "native_probe_sha256": digest(HERE / "native_probe.py"),
        "original_artifact_hashes": original_hashes, "command": command, "cwd": os.environ["TEMP"],
        "environment": {"extra_bytecode_prohibition_removed": True, "memory_python_path_home_cleared": True},
        "precondition": {"fresh_install_pass": True, "openviking_workspace_uninitialized": True,
            "preserved_marker_sha256": digest(marker), "lark_pyc": 0, "volcengine_pyc": 0},
        "method": "Same frozen installed wheel and 300s manager timeout; no precompile, new dependencies, fork, LLM or service changes."}

    def save():
        text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        (store / "result.json").write_text(text, encoding="utf-8")
        (HERE / "normal_native_result.json").write_text(text, encoding="utf-8")

    started = time.perf_counter()
    log = store / "probe.log"
    print(json.dumps({"stage": "normal-native", "home": str(home), "log": str(log)}), flush=True)
    try:
        with log.open("xb") as output:
            process = subprocess.Popen(command, cwd=os.environ["TEMP"], env=env,
                                       stdout=output, stderr=subprocess.STDOUT)
            report["pid"] = process.pid
            save()
            report["exit_code"] = process.wait()
        report["wall_seconds"] = time.perf_counter() - started
        report["native"] = json.loads((store / "native.json").read_text(encoding="utf-8"))
        report["marker_preserved_after_native"] = digest(marker) == report["precondition"]["preserved_marker_sha256"]
        report["original_artifacts_unchanged"] = all(digest(path) == original_hashes[str(path)] for path in original_paths)
        report["overall"] = "PASS" if (report["exit_code"] == 0 and report["native"]["overall"] == "PASS"
            and report["marker_preserved_after_native"] and report["original_artifacts_unchanged"]) else "FAIL"
    except Exception as error:
        report["overall"] = "FAIL"
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        save()
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if report["overall"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
