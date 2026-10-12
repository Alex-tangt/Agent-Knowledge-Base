"""Independent Lead read of the actual formal snapshot's isolated restored home."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request

from memory_base import deploy
from memory_base.credentials import get_secret
from memory_base.upstream import OpenVikingClient, UpstreamError

HOME = Path(r"C:\Users\Tan\.mb94\a")
SNAPSHOT = Path(r"D:\python_work\work2026-4\memory-base-backups\formal-20261012T014904")
REPO = Path(__file__).resolve().parents[2]
EXPECTED = {
    "4b24c835877cfaa5666c2ba322431a663aab3fdd17c3196ac99274e94fcf591d": "a773d1d44eb19f2b3e061417b37aabfc728a1878bf50f0918eb994e1746b8095",
    "0ada0a7776fcf4e646f0b3b54d69972334f01b04a7da6488c4044fa6f46dbd7b": "5f6ba7e2e05b205484c0034111404a363151d35d95516871c61fd6d727eeadc7",
    "5a3c5791d6c61e7a5771ca7006d5d0498134c7076dcad2e3312177af4f47ce9c": "167b784b8f81f90927b6b3d656c0d3df8cec10f92d1601fb4c1901271f0ca6e0",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    config = deploy.load_config(HOME)
    assert Path(sys.executable).resolve() == Path(config["python"]).resolve()
    assert not deploy._records(HOME) and not any(deploy._port_busy(port) for port in config["ports"].values())
    snapshot_raw = (SNAPSHOT / "manifest.json").read_bytes()
    assert sha(snapshot_raw) == "27dc14f772e61a351be62bf3c5dfb24397c27cec8894612065139a8677541c65"
    manifest = json.loads(snapshot_raw)
    for item in manifest["files"]:
        raw = (SNAPSHOT / item["path"]).read_bytes()
        assert len(raw) == item["size"] and sha(raw) == item["sha256"]
    expected_dirs = set(manifest["directories"])
    assert {str(p.relative_to(SNAPSHOT)).replace("\\", "/") for p in SNAPSHOT.rglob("*") if p.is_dir()} == expected_dirs
    evidence = REPO / "experiments/memory-base-formal-restore"
    archive_manifest = json.loads((evidence / "evidence-manifest.json").read_bytes())
    for item in archive_manifest["files"]:
        raw = (evidence / item["path"]).read_bytes()
        assert len(raw) == item["bytes"] and sha(raw) == item["sha256"]
        assert raw == Path(item["asset_original"]).read_bytes()
    report = {"date": datetime.now(timezone.utc).isoformat(), "home": str(HOME),
              "snapshot_manifest_sha256": sha(snapshot_raw), "snapshot_files_independently_verified": len(manifest["files"]),
              "snapshot_directories": len(expected_dirs), "archived_asset_pairs_verified_before_lead_start": len(archive_manifest["files"]),
              "boundary": "Lead starts the already restored isolated home for independent read and permission checks. This is not another install/restore or first-start measurement; target service logs may grow. Original snapshot and archived worker reports remain fixed. No new capture or paid model."}
    output = Path(__file__).with_name("lead-formal-restore-2026-10-12.json")
    logs = REPO / ".scratch/lead-formal-restore"
    logs.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("MEMORY_") and k.upper() not in {"PYTHONPATH", "PYTHONHOME"}}
    records = {}

    def manager(action):
        process = subprocess.run([sys.executable, "-m", "memory_base", action, "--home", str(HOME)],
                                 env=env, cwd=os.environ["TEMP"], capture_output=True, text=True, encoding="utf-8")
        (logs / (action + ".log")).write_text(process.stdout + process.stderr, encoding="utf-8")
        assert process.returncode == 0, "Owned target manager failed; see local safe log"
        return json.loads(process.stdout)

    def request(body):
        token = get_secret("host-reader", HOME)
        req = urllib.request.Request(f"http://127.0.0.1:{config['ports']['gateway']}/api/memory/get",
                                     data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=75) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    try:
        assert manager("start")["status"] == "ready"
        records = deploy._records(HOME)
        assert all(deploy.owned_component(name, HOME, config, row) for name, row in records.items())
        assert len(records) == 3
        verified = []
        for capture, digest in EXPECTED.items():
            uri = "viking://resources/captures/" + capture + "/content.md"
            code, content = request({"uri": uri, "expected_revision": digest})
            assert code == 200 and content["revision"] == digest and sha(content["content"].encode()) == digest
            verified.append({"uri": uri, "revision": digest})
        report["three_original_receipts"] = verified
        digest = sha((REPO / "README.md").read_bytes())
        code, content = request({"uri": "projects/agent-kb/README.md", "scope": "projects/agent-kb", "expected_revision": digest})
        assert code == 200 and content["revision"] == digest and sha(content["content"].encode()) == digest
        report["current_source_revision_matches"] = True
        code, _ = request({"uri": verified[0]["uri"], "scope": "projects/agent-kb"})
        assert code == 403
        gateway = deploy.gateway_env(HOME, config)
        native = OpenVikingClient(f"http://127.0.0.1:{config['ports']['upstream']}", gateway["MEMORY_BASE_OV_KEY"])
        try:
            native.call("GET", "/api/v1/admin/accounts/mb94-unowned/users")
            raise AssertionError("Native foreign-account request was not denied")
        except UpstreamError as error:
            assert error.status == 403
        report["scope_and_native_account_denied_403"] = True
        report["overall"] = "PASS"
    finally:
        manager("stop")
        assert not deploy._records(HOME)
        assert not any(deploy.owned_component(name, HOME, config, row) for name, row in records.items())
        assert not any(deploy._port_busy(port) for port in config["ports"].values())
        assert (SNAPSHOT / "manifest.json").read_bytes() == snapshot_raw
        assert all(sha((SNAPSHOT / item["path"]).read_bytes()) == item["sha256"] for item in manifest["files"])
        report["owned_target_stopped_no_residual"] = True
        report["snapshot_original_files_still_match"] = True
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


def remaining_permission():
    """Finish only the invalid-route probe; retain the earlier partial evidence."""
    previous = Path(__file__).with_name("lead-formal-restore-first-partial-2026-10-12.json")
    report = json.loads(previous.read_bytes())
    assert report["owned_target_stopped_no_residual"] and len(report["three_original_receipts"]) == 3
    assert {item["revision"] for item in report["three_original_receipts"]} == set(EXPECTED.values())
    config = deploy.load_config(HOME)
    assert Path(sys.executable).resolve() == Path(config["python"]).resolve()
    assert not deploy._records(HOME) and not any(deploy._port_busy(port) for port in config["ports"].values())
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("MEMORY_") and k.upper() not in {"PYTHONPATH", "PYTHONHOME"}}
    records = {}
    logs = REPO / ".scratch/lead-formal-restore"

    def manager(action):
        process = subprocess.run([sys.executable, "-m", "memory_base", action, "--home", str(HOME)],
                                 env=env, cwd=os.environ["TEMP"], capture_output=True, text=True, encoding="utf-8")
        (logs / ("permission-" + action + ".log")).write_text(process.stdout + process.stderr, encoding="utf-8")
        assert process.returncode == 0
        return json.loads(process.stdout)

    report["first_partial_report"] = previous.name
    report["permission_probe_date"] = datetime.now(timezone.utc).isoformat()
    report["overall"] = "FAIL"
    try:
        assert manager("start")["status"] == "ready"
        records = deploy._records(HOME)
        assert len(records) == 3 and all(deploy.owned_component(name, HOME, config, row) for name, row in records.items())
        native = OpenVikingClient(f"http://127.0.0.1:{config['ports']['upstream']}", get_secret("upstream-key", HOME))
        try:
            native.call("GET", "/api/v1/admin/accounts/mb94-unowned/users")
            raise AssertionError("Native foreign-account request was not denied")
        except UpstreamError as error:
            assert error.status == 403
        report["native_account_denied_403_on_actual_route"] = True
        report["gateway_scope_denied_403_in_first_probe"] = True
        report["overall"] = "PASS"
    finally:
        manager("stop")
        assert not deploy._records(HOME) and not any(deploy._port_busy(port) for port in config["ports"].values())
        assert not any(deploy.owned_component(name, HOME, config, row) for name, row in records.items())
        manifest = json.loads((SNAPSHOT / "manifest.json").read_bytes())
        assert sha((SNAPSHOT / "manifest.json").read_bytes()) == report["snapshot_manifest_sha256"]
        assert all(sha((SNAPSHOT / item["path"]).read_bytes()) == item["sha256"] for item in manifest["files"])
        report["remaining_probe_owned_cleanup"] = True
        report["snapshot_original_files_still_match"] = True
        report["boundary"] += " First partial proof and its 404 route failure are separate files; only the corrected native account permission probe was repeated. Worker archive/asset pairs were verified before the first Lead start, not after live target logs grew."
        Path(__file__).with_name("lead-formal-restore-2026-10-12.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    remaining_permission() if "--permission-only" in sys.argv else main()
