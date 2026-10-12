"""Lead applies only the frozen Bubble host-state files after combined gates."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ACTUAL = Path(r"D:\python_work\work2026-10\Agent-bubble")
CANDIDATE = Path(r"D:\python_work\work2026-4\memory-base-bubble-bridge-assets\20261012\candidate-final")
FROZEN = "8e13fae"
FILES = {
    "scripts/host-state.mjs": (None, "6482f8b05cf7a42aba7de431fba339f0ab9be1a716f63fdabb8a917a23eb43c0"),
    "scripts/tests/local-backend.test.mjs": (
        "336e0f96252bbe46539db0abd1bafb5310d9f4decc0640de2f4be91a0ce89119",
        "9d0f1fae875ca8da4a1ac24bd6ac1c981962bf22c3362ab40c9f3ef91a303b7a"),
    "scripts/local-backend.mjs": (
        "b4896de296d068f7605d93b5549ee623ff58f6fe44bff1caee5edd3ec5d9c6ad",
        "fb63de37a3c6f7e358fc0862badf4982557bc23bd788874219f338d64ae5cb5d"),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    assert ACTUAL.resolve() == ACTUAL and CANDIDATE.resolve() == CANDIDATE
    assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=CANDIDATE)
    frozen = subprocess.check_output(["git", "rev-parse", FROZEN], cwd=CANDIDATE).decode().strip()
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=CANDIDATE).decode().strip() == frozen
    changes = {}
    for name, (before, after) in FILES.items():
        path = ACTUAL / name
        original = path.read_bytes() if path.exists() else None
        assert (sha(original) if original is not None else None) == before, "Actual file changed; stop before writing"
        artifact = subprocess.check_output(["git", "show", frozen + ":" + name], cwd=CANDIDATE)
        assert sha(artifact) == after
        changes[name] = original, artifact
    code = """import {DatabaseSync} from 'node:sqlite';import {resolve,join} from 'node:path';
process.loadEnvFile('.env');
const db=new DatabaseSync(join(resolve(process.env.DATA_DIR||'data'),'bubble.sqlite'),{readOnly:true});
const active=db.prepare(\"SELECT count(*) AS n FROM tasks WHERE status IN ('queued','running')\").get().n;
db.close();process.stdout.write(JSON.stringify({active}));"""
    task = json.loads(subprocess.check_output([shutil.which("node"), "--input-type=module", "-e", code], cwd=ACTUAL))
    assert task["active"] == 0, "A user task is active; defer deployment"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = CANDIDATE.parent / ("actual-host-state-backup-" + stamp)
    backup.mkdir(exist_ok=False)
    report = {"date": stamp, "combined_candidate_commit": frozen,
              "host_candidate_commit": "ba8b5b5c557fceee93c9e5738b43ecee7ec9b156",
              "actual": str(ACTUAL), "backup": str(backup), "files": {},
              "baseline_all_verified_before_write": True, "active_tasks_before_apply": 0,
              "scope": "Three scripts only, no credentials, data or server TS changes"}
    for name, (original, artifact) in changes.items():
        if original is not None:
            target = backup / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original)
            assert target.read_bytes() == original
    (backup / "absent-files.json").write_text(json.dumps([name for name, (raw, _) in changes.items() if raw is None]))
    # Publish the new helper before the launcher that imports it.
    for name, (original, artifact) in changes.items():
        (ACTUAL / name).write_bytes(artifact)
        assert (ACTUAL / name).read_bytes() == artifact
        report["files"][name] = {"before": sha(original) if original is not None else None, "after": sha(artifact)}
    Path(__file__).with_name("host-state-applied-2026-10-12.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
