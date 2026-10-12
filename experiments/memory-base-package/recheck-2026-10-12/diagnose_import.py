"""Read-only, bounded import differential; never starts a service or edits runtime."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata as metadata
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from run_recheck import MAIN, FORMAL, HERE, clean_env

PACKAGES = {"lark-oapi": "lark_oapi", "volcengine-python-sdk": "volcenginesdk",
            "openviking": "openviking", "fastapi": "fastapi", "pydantic": "pydantic"}
PROBE = """import faulthandler,importlib,json,sys,time
faulthandler.dump_traceback_later(25,exit=True)
started=time.perf_counter()
module=importlib.import_module(sys.argv[1])
faulthandler.cancel_dump_traceback_later()
print('[DEBUG-kb86-import] '+json.dumps({'module':sys.argv[1],'seconds':time.perf_counter()-started,
 'origin':module.__file__,'loaded_modules':len(sys.modules),
 'lark_modules':sum(n=='lark_oapi' or n.startswith('lark_oapi.') for n in sys.modules)},sort_keys=True))
"""


def identity():
    result = {"executable": sys.executable, "python": sys.version, "packages": {},
              "versions": {re.sub(r"[-_.]+", "-", d.metadata["Name"]).lower(): d.version
                           for d in metadata.distributions()}}
    for name, module in PACKAGES.items():
        dist = metadata.distribution(name)
        rows = sorted(row for row in csv.reader(io.StringIO(dist.read_text("RECORD")))
                      if row[0].startswith(module) and row[0].endswith(".py"))
        assert rows, name + " source census cannot be empty"
        roots = sorted({row[0].split("/", 1)[0] for row in rows})
        counts = {"py": 0, "pyc": 0}
        for top_level in roots:
            root = Path(dist.locate_file(top_level))  # Lexical paths; no per-file resolve/stat.
            for _, _, filenames in os.walk(root):
                counts["py"] += sum(filename.endswith(".py") for filename in filenames)
                counts["pyc"] += sum(filename.endswith(".pyc") for filename in filenames)
        result["packages"][name] = {"version": dist.version, "roots": roots,
            "actual_file_counts": counts, "record_python_files": len(rows),
            "source_record_sha256": hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()}
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity", action="store_true")
    args = parser.parse_args()
    if args.identity:
        identity()
        return
    assert Path(sys.executable).resolve() == MAIN.resolve()
    store = Path(r"C:\Users\Tan\.mb86\r4")
    acceptance = json.loads((store / "results.json").read_text(encoding="utf-8"))
    assert acceptance["overall"] == "FAIL" and acceptance["native"]["overall"] == "FAIL"
    assert acceptance["continuation"]["fresh_install_gate"] == "PASS"
    asset = store / ("diag-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    asset.mkdir()  # New diagnostics; never overwrite original acceptance evidence.
    report = {"asset": str(asset), "purpose": "Import-only diagnosis after sealed fresh installation; original native FAIL unchanged",
              "probe_limit_seconds": 25, "all_children_dont_write_bytecode": True,
              "commands": [], "identities": {}, "imports": []}
    interpreters = {"formal": FORMAL, "default_after_failed_start": store / "d/runtime/Scripts/python.exe",
                    "fresh_never_started": store / "f/runtime/Scripts/python.exe"}

    def save():
        text = json.dumps(report, indent=2) + "\n"
        (asset / "report.json").write_text(text, encoding="utf-8")
        (HERE / "import_diagnosis.json").write_text(text, encoding="utf-8")

    def run(command, stage):
        command = list(map(str, command))
        log = asset / (stage + ".log")
        entry = {"command": command, "stage": stage, "log": str(log), "cwd": os.environ["TEMP"]}
        print(json.dumps({"stage": stage, "log": str(log)}), flush=True)
        started = time.perf_counter()
        with log.open("xb") as output:
            process = subprocess.Popen(command, cwd=os.environ["TEMP"], env=clean_env(),
                                       stdout=output, stderr=subprocess.STDOUT)
            entry["pid"] = process.pid
            report["commands"].append(entry)
            save()
            entry["exit_code"] = process.wait()
        entry["wall_seconds"] = time.perf_counter() - started
        save()
        return entry, log.read_text(encoding="utf-8", errors="replace")

    for label, python in interpreters.items():
        command, output = run([python, "-B", __file__, "--identity"], label + "-identity")
        assert command["exit_code"] == 0
        report["identities"][label] = json.loads(output)
        save()
    formal_versions = report["identities"]["formal"]["versions"]
    report["dependency_differences"] = {}
    for label in ("default_after_failed_start", "fresh_never_started"):
        versions = report["identities"][label]["versions"]
        report["dependency_differences"][label] = {name: {"formal": formal_versions.get(name), "target": versions.get(name)}
            for name in sorted(formal_versions.keys() | versions.keys()) if formal_versions.get(name) != versions.get(name)}
    # Existing cache vs forced source in formal; default partially warmed vs fresh cold.
    # -B prevents all pyc writes, including formal; pycache_prefix points at a nonexistent test path.
    cases = [(label, python, "lark_oapi", False) for label, python in interpreters.items()]
    cases += [("formal_force_source", FORMAL, "lark_oapi", True),
              ("default_feishu_pure_import", interpreters["default_after_failed_start"],
               "openviking.parse.accessors.feishu_accessor", False)]
    for label, python, module, force_source in cases:
        command = [python, "-B", "-u", "-X", "importtime"]
        if force_source:
            command += ["-X", "pycache_prefix=" + str(asset / "nonexistent-pycache")]
        command += ["-c", PROBE, module]
        entry, output = run(command, label + "-import")
        timings = []
        for line in output.splitlines():
            match = re.fullmatch(r"import time:\s+(\d+)\s+\|\s+(\d+)\s+\|\s+(.+)", line)
            if match:
                timings.append({"self_us": int(match[1]), "cumulative_us": int(match[2]), "module": match[3].strip()})
        completed = next((json.loads(line.split(" ", 1)[1]) for line in output.splitlines()
                          if line.startswith("[DEBUG-kb86-import] ")), None)
        report["imports"].append({"label": label, "module": module, "exit_code": entry["exit_code"],
            "wall_seconds": entry["wall_seconds"], "completed": completed,
            "importtime_completed_lines": len(timings),
            "largest_self": sorted(timings, key=lambda item: item["self_us"], reverse=True)[:12],
            "largest_cumulative": sorted(timings, key=lambda item: item["cumulative_us"], reverse=True)[:12],
            "faulthandler_timeout": "Timeout (0:00:25)!" in output})
        save()
    assert not (asset / "nonexistent-pycache").exists()
    report["acceptance_report_untouched"] = json.loads((store / "results.json").read_text(encoding="utf-8")) == acceptance
    save()
    print(json.dumps({"asset": str(asset), "imports": [{key: case[key] for key in
          ("label", "exit_code", "wall_seconds", "completed", "faulthandler_timeout")} for case in report["imports"]]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
