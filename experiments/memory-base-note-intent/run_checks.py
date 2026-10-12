"""Run only documented Bubble gates against one frozen candidate, with safe logs."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

HERE = Path(__file__).resolve().parent
CANDIDATE = Path(r"D:\python_work\work2026-4\memory-base-bubble-bridge-assets\20261012\candidate92")
FROZEN = "8b7327f86ad632bf4fd1b367af969a94bf78db84"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=CANDIDATE, text=True).strip()


def main():
    assert git("rev-parse", "HEAD") == FROZEN and not git("status", "--porcelain")
    assert not (CANDIDATE / ".env").exists()
    store = HERE / "store/frozen-8b7327f"
    store.mkdir()
    report = {"candidate": str(CANDIDATE), "frozen_commit": FROZEN, "commands": [],
        "environment": "Inherited model/Bubble/Memory/ASR/browser settings removed; synthetic local tests only.",
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    prefixes = ("MEMORY_", "AGENT_BUBBLE_", "DEEPSEEK_", "DASHSCOPE_", "OPENAI_", "ASR_", "ARTICLE_BROWSER_")
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith(prefixes)}
    npm = shutil.which("npm.cmd")
    assert npm

    def save():
        text = json.dumps(report, indent=2) + "\n"
        (HERE / "checks.json").write_text(text, encoding="utf-8")
        (store / "report.json").write_text(text, encoding="utf-8")

    for label, command in (("build", [npm, "run", "build"]), ("test", [npm, "test"]), ("host", [npm, "run", "test:host"])):
        assert git("rev-parse", "HEAD") == FROZEN and not git("status", "--porcelain")
        log = store / (label + ".log")
        entry = {"gate": label, "command": command, "cwd": str(CANDIDATE), "log": str(log)}
        report["commands"].append(entry)
        print(json.dumps({"gate": label, "log": str(log)}), flush=True)
        started = time.perf_counter()
        with log.open("xb") as output:
            process = subprocess.Popen(command, cwd=CANDIDATE, env=env, stdout=output,
                stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
            entry["pid"] = process.pid
            save()
            entry["exit_code"] = process.wait()
        entry["wall_seconds"] = time.perf_counter() - started
        content = log.read_text(encoding="utf-8", errors="replace")
        entry["log_sha256"] = hashlib.sha256(log.read_bytes()).hexdigest()
        entry["counts"] = {name: int(match[1]) for name in ("tests", "pass", "fail", "skipped", "cancelled")
                           if (match := re.search(r"(?:^|\n)[^\n\d]*\b" + name + r"\s+(\d+)", content))}
        save()
        if entry["exit_code"] != 0:
            report["overall"] = "FAIL"
            save()
            raise SystemExit(1)
    baseline = json.loads((HERE / "baseline.json").read_text(encoding="utf-8"))
    report["actual_files_unchanged"] = all(hashlib.sha256((Path(baseline["actual"]) / row["path"]).read_bytes()).hexdigest()
        == row["actual"]["sha256"] for row in baseline["files"])
    assert report["actual_files_unchanged"]
    report["candidate_clean_and_frozen"] = git("rev-parse", "HEAD") == FROZEN and not git("status", "--porcelain")
    assert report["candidate_clean_and_frozen"]
    report["overall"] = "PASS"
    save()
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
