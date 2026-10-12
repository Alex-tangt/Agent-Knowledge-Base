"""Finish independent fresh-install checks while preserving R4 native FAIL."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from run_recheck import MAIN, FORMAL, MODEL, FROZEN, HERE, REPO, clean_env, ports


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert Path(sys.executable).resolve() == MAIN.resolve()
    store = Path(r"C:\Users\Tan\.mb86\r4").resolve()
    assert store.parent == Path(r"C:\Users\Tan\.mb86").resolve()
    report = json.loads((store / "results.json").read_text(encoding="utf-8"))
    assert report["frozen_sha"] == FROZEN and report["overall"] == "FAIL"
    assert report["checks"]["default_auto_artifact_full_dependency_package_install"] == "PASS"
    assert report["failure_stage"] == "default-native"
    native = json.loads((store / "native.json").read_text(encoding="utf-8"))
    assert native["overall"] == "FAIL" and native["stop_exit_code"] == 0
    assert native["checks"]["owned_process_cleanup_no_residual"]
    with (store / "results-before-fresh.json").open("x", encoding="utf-8") as output:
        output.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    report["native"] = native
    report["continuation"] = {"reason": "Owner authorized only independent adopt/fresh checks; no native restart.",
                              "script_sha256": digest(Path(__file__)), "native_failure_preserved": True}
    wheel = Path(report["wheel"]["path"])
    assert wheel.is_relative_to(store) and digest(wheel) == report["wheel"]["sha256"]
    launcher = store / "l/Scripts/python.exe"
    cli = launcher.with_name("memory-base.exe")
    home = store / "f"
    assert not home.exists(), "Preserve existing fresh-case assets"

    def save():
        text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        (store / "results.json").write_text(text, encoding="utf-8")
        (HERE / "results.json").write_text(text, encoding="utf-8")

    def run(command, stage):
        command = list(map(str, command))
        log = store / (f"command-{len(report['commands']):03d}-{stage}.log")
        entry = {"command": command, "cwd": os.environ["TEMP"], "stage": stage,
                 "log": str(log), "status": "running"}
        print(json.dumps({"stage": stage, "log": str(log)}), flush=True)
        with log.open("wb") as output:
            process = subprocess.Popen(command, cwd=os.environ["TEMP"], env=clean_env(),
                                       stdout=output, stderr=subprocess.STDOUT)
            entry["pid"] = process.pid
            report["commands"].append(entry)
            save()
            entry["exit_code"] = process.wait()
        entry["status"] = "completed"
        save()
        assert entry["exit_code"] == 0, stage + " failed; see preserved log"
        return log.read_text(encoding="utf-8", errors="replace")

    try:
        report["continuation"]["commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
        run([cli, "install", "--home", home, "--runtime-python", launcher, "--model", MODEL], "adopt-real-install")
        config_path = home / "deployment.json"
        adopted = json.loads(config_path.read_text(encoding="utf-8"))
        assert Path(adopted["python"]).resolve() == launcher.resolve()
        adopted["ports"] = ports()
        config_path.write_text(json.dumps(adopted, indent=2) + "\n", encoding="utf-8")
        report["fixture_setup"].append({"home": str(home), "ports": adopted["ports"],
                                       "reason": "Only unstarted test deployment.json; no CLI ports option."})
        marker = home / "data/r2-preserved-marker.txt"
        marker.parent.mkdir(parents=True)
        marker.write_bytes("合成 fresh 保留标记\r\n".encode("utf-8"))
        marker_hash = digest(marker)
        assert not (home / "runtime").exists()
        run([cli, "install", "--home", home, "--wheel", wheel, "--fresh-runtime"], "fresh-real-install")
        fresh = json.loads(config_path.read_text(encoding="utf-8"))
        python = home / "runtime/Scripts/python.exe"
        assert Path(fresh["python"]).resolve() == python.resolve()
        assert fresh["model"] == adopted["model"] and fresh["ports"] == adopted["ports"]
        assert digest(marker) == marker_hash
        forbidden = Path(report["formal_readonly_identity"]["purelib"])
        info = json.loads(run([python, HERE / "inspect_environment.py", "--clean-target",
                               "--forbidden-site", forbidden], "fresh-target-origins"))
        assert info["direct_url"]["url"] == wheel.as_uri()
        assert info["direct_url"]["archive_info"]["hashes"]["sha256"] == report["wheel"]["sha256"]
        assert Path(fresh["source"]).resolve() == Path(info["site"]).resolve()
        report["fresh_case"] = {"adopted": adopted, "fresh": fresh, "target_origins": info,
                               "marker_sha256": marker_hash, "home_model_ports_marker_preserved": True}
        report["fresh_target_pip_check"] = run([python, "-m", "pip", "check"], "fresh-pip-check").strip()
        report["checks"]["adopt_then_explicit_wheel_fresh_full_install_preservation"] = "PASS"
        report["checks"]["direct_pins_constraints_target_origins_and_pip_check"] = "PASS"
        report["formal_pip_check"] = run([FORMAL, "-m", "pip", "check"], "formal-readonly-pip-check").strip()
        report["checks"]["formal_runtime_readonly_pip_check"] = "PASS"
        report["continuation"]["fresh_install_gate"] = "PASS"
    except Exception as error:
        report["continuation"]["fresh_install_gate"] = "FAIL"
        report["continuation"]["failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        # Native FAIL is never converted to overall PASS by installer success.
        report["overall"] = "FAIL"
        report["remaining_not_verified"] = ["three native services ready", "native Unicode/CRLF append",
            "native reader get/hash/search/list", "native anonymous401/reader403", "native stdio get/list"]
        save()
        print(json.dumps({"overall": report["overall"], "continuation": report["continuation"],
                          "checks": report["checks"]}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
