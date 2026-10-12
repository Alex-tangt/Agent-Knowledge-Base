"""Lead applies the frozen two-file Bubble note-intent change after byte checks.

This does not alter credentials/data or restart the backend. Review and run the
frozen candidate gates first; then use Bubble's own lifecycle commands.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ACTUAL = Path(r"D:\python_work\work2026-10\Agent-bubble")
CANDIDATE = Path(r"D:\python_work\work2026-4\memory-base-bubble-bridge-assets\20261012\candidate92")
ASSETS = CANDIDATE.parent
FROZEN = "36f9bf294e4117bf36a4228d63b3a3c7ad715af1"
FILES = {
    "server/memory-tools.ts": (
        "339a852a374af74e471502afa083c48ca6787811bd57cdded03f08bd0b4ee686",
        "2dfe82df62eb129564a76586bebdb40998e06a964c1e99393310ef911d1353de"),
    "server/tests/memory-base.test.ts": (
        "6dc351fd6a015d8a98011e4f83b2f7c791de112a3652f2c738871999e8017256",
        "1d1e25b86c1b5168a0ebaec3fad1a5e9a415a90440ac23ca358b6b8ca1c6a13c"),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    assert sys.version_info[:2] == (3, 12)
    assert ACTUAL.resolve() == ACTUAL and CANDIDATE.resolve() == CANDIDATE
    assert subprocess.check_output(["git", "status", "--porcelain"], cwd=CANDIDATE) == b""
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=CANDIDATE).decode().strip() == FROZEN
    changes = {}
    for name, (before, after) in FILES.items():
        original = (ACTUAL / name).read_bytes()
        artifact = subprocess.check_output(["git", "show", FROZEN + ":" + name], cwd=CANDIDATE)
        assert sha(original) == before, "Actual baseline changed; do not overwrite it"
        assert sha(artifact) == after, "Frozen artifact does not match reviewed bytes"
        changes[name] = original, artifact
    # Read-only SQLite query covers every task, including conversations outside
    # the HTTP list's 100-item display limit. Never print task bodies or secrets.
    node = shutil.which("node")
    code = """import {DatabaseSync} from 'node:sqlite';import {resolve,join} from 'node:path';
process.loadEnvFile('.env');
const db=new DatabaseSync(join(resolve(process.env.DATA_DIR||'data'),'bubble.sqlite'),{readOnly:true});
const active=db.prepare(\"SELECT count(*) AS n FROM tasks WHERE status IN ('queued','running')\").get().n;
const conversations=db.prepare('SELECT count(*) AS n FROM conversations').get().n;
db.close();process.stdout.write(JSON.stringify({active,conversations}));"""
    tasks = json.loads(subprocess.check_output([node, "--input-type=module", "-e", code], cwd=ACTUAL))
    assert tasks["active"] == 0, "An actual task is active; defer deployment"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = ASSETS / ("actual-note-intent-backup-" + stamp)
    backup.mkdir(exist_ok=False)
    report = {"date": stamp, "candidate_commit": FROZEN, "actual": str(ACTUAL),
              "backup": str(backup), "baseline_all_verified_before_write": True,
              "active_tasks_before_apply": tasks["active"], "conversations": tasks["conversations"],
              "only_two_source_files": True, "line_endings": "LF", "files": {}}
    for name, (original, artifact) in changes.items():
        saved = backup / name
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(original)
        assert saved.read_bytes() == original
    for name, (original, artifact) in changes.items():
        (ACTUAL / name).write_bytes(artifact)
        assert (ACTUAL / name).read_bytes() == artifact
        report["files"][name] = {"before": sha(original), "after": sha(artifact)}
    out = Path(__file__).with_name("note-intent-applied-2026-10-12.json")
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
