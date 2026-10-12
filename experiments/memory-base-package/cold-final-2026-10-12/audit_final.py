"""Read-only independent artifact, scope and cleanup audit after R5 completion."""
from __future__ import annotations

import base64
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata as metadata
import inspect
import io
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import winreg
import zipfile

from memory_base import deploy
from memory_base.credentials import get_secret

ROOT = Path(r"C:\Users\Tan\.mb86\r5")
HERE = Path(__file__).resolve().parent


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    result = json.loads((ROOT / "results.json").read_text(encoding="utf-8"))
    assert result["overall"] == "PASS"
    assert all(c["status"] == "completed" and c["exit_code"] == 0 for c in result["commands"])
    assert Path(sys.prefix).resolve() == (ROOT / "d/runtime").resolve()
    wheel = Path(result["wheel"]["path"])
    assert sha(wheel.read_bytes()) == result["wheel"]["sha256"]
    record_rows = 0
    with zipfile.ZipFile(wheel) as z:
        record = "agent_memory_base-0.1.0.dist-info/RECORD"
        for name, digest, size in csv.reader(io.StringIO(z.read(record).decode())):
            record_rows += 1
            if name == record:
                assert digest == size == ""
            else:
                raw = z.read(name)
                expected = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
                assert digest == "sha256=" + expected and size == str(len(raw))
        installed = {}
        for label in ("d", "f"):
            site = ROOT / label / "runtime/Lib/site-packages"
            entries = []
            for name in z.namelist():
                if name.startswith("memory_base/"):
                    target = site / name
                    assert target.read_bytes() == z.read(name), (label, name)
                    entries.append({"path": name, "sha256": sha(target.read_bytes())})
            origin = json.loads(next(site.glob("agent_memory_base-*.dist-info/direct_url.json")).read_text())
            assert origin["url"] == wheel.as_uri()
            assert origin["archive_info"]["hashes"]["sha256"] == result["wheel"]["sha256"]
            assert "include-system-site-packages = false" in (ROOT/label/"runtime/pyvenv.cfg").read_text().lower()
            actual = {re.sub(r"[-_.]+", "-", d.metadata["Name"]).lower():
                      {"version":d.version,"root":str(d._path)} for d in metadata.distributions(path=[str(site)])}
            assert all(Path(d["root"]).resolve().is_relative_to(site.resolve()) for d in actual.values())
            for n,v in deploy.PINS.items():
                assert actual[n]["version"] == v
            active_constraints = {}
            for line in (site/"memory_base/constraints.txt").read_text().splitlines():
                m = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)",line.strip())
                if m:
                    n = re.sub(r"[-_.]+", "-", m[1]).lower()
                    if n in actual:
                        assert actual[n]["version"] == m[2]
                        active_constraints[n] = m[2]
            lark = site/"lark_oapi"
            volc_roots = [p for p in site.glob("volcenginesdk*") if p.is_dir() and not p.name.endswith(".dist-info")]
            installed[label] = {"wheel_product_bytes_match":True,"entries":entries,
                "own_distributions":len(actual),"active_constraints":len(active_constraints),
                "direct_url":origin,"lark_python_files":len(list(lark.rglob("*.py"))),
                "lark_pyc_files":len(list(lark.rglob("*.pyc"))),
                "current_volc_sdk_roots":len(volc_roots),
                "current_volc_sdk_py_files":sum(len(list(p.rglob("*.py"))) for p in volc_roots),
                "current_volc_sdk_pyc_files":sum(len(list(p.rglob("*.pyc"))) for p in volc_roots)}
    native = result["native"]
    assert native["manager_commands"][0] == [str(ROOT/"d/runtime/Scripts/memory-base.exe"),"start","--home",str(ROOT/"d")]
    assert inspect.signature(deploy.start).parameters["timeout"].default == 180
    assert all(native["checks"].values()) and native["start_exit_code"] == native["stop_exit_code"] == 0
    records = json.loads((ROOT/"processes-before-stop.json").read_text())
    ownership = {name:deploy.owned_process(record) for name,record in records.items()}
    assert not any(ownership.values())
    config = json.loads((ROOT/"d/deployment.json").read_text())
    port_busy = {}
    for name, port in config["ports"].items():
        with socket.socket() as sock:
            sock.settimeout(0.3)
            port_busy[name] = sock.connect_ex(("127.0.0.1",port)) == 0
    assert not any(port_busy.values())
    assert json.loads((ROOT/"d/processes.json").read_text()) == {}
    capture_id = native["uri"].split("/")[-2]
    candidates = list((ROOT/"d/data/viking").rglob(capture_id+"/content.md"))
    assert len(candidates) == 1
    capture = candidates[0].read_bytes()
    assert sha(capture) == native["content_hash"]
    assert b"\r\n" in capture and capture.decode("utf-8").startswith("# 独立 wheel")
    secret_values = []
    for home in (ROOT/"d",ROOT/"f"):
        for name in ("root-key","upstream-key",*("host-"+p for p in deploy.PROFILES)):
            value = get_secret(name, home)
            if value:
                secret_values.append(value)
    evidence = sorted([*ROOT.glob("*.json"),*ROOT.glob("*.log"),*(ROOT/"d/logs").glob("*.log")])
    for path in evidence:
        data = path.read_bytes()
        assert not any(value.encode(enc) in data for value in secret_values for enc in ("utf-8","utf-16-le","utf-16-be")), path.name
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
        long_paths = winreg.QueryValueEx(key,"LongPathsEnabled")[0]
    audit = {"audited_at":datetime.now(timezone.utc).isoformat(),"frozen_sha":result["frozen_sha"],
        "wheel_sha256":sha(wheel.read_bytes()),"record_rows_verified":record_rows,
        "completed_commands":len(result["commands"]),"installed":installed,
        "default_start_timeout":180,"actual_start_command":native["manager_commands"][0],
        "start_seconds":native["start_seconds"],"native_checks":native["checks"],
        "current_owned_process_survivors":ownership,"current_ports_busy":port_busy,
        "current_process_records":{},"offline_capture_file":str(candidates[0]),
        "offline_capture_bytes":len(capture),"offline_capture_sha256":sha(capture),
        "scanned_secret_count":len(secret_values),"scanned_evidence_files":len(evidence),
        "plaintext_credential_matches":0,"windows_LongPathsEnabled_readonly":long_paths,
        "measurement_correction":"Original pre-native volcenginesdk count used a nonexistent single root. It does not prove no other SDK caches. Current actual distributed volcenginesdk* roots are enumerated here; this is post-run evidence.",
        "no_jobs_rerun":True,"overall":"PASS"}
    (ROOT/"final-audit.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:audit[k] for k in ("overall","wheel_sha256","completed_commands","start_seconds",
        "current_owned_process_survivors","current_ports_busy","offline_capture_sha256",
        "scanned_secret_count","scanned_evidence_files","plaintext_credential_matches")},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
