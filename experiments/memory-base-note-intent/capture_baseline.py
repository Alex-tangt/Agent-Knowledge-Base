"""Preserve only the two authorized Bubble files before #92 changes."""
import hashlib
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
ACTUAL = Path(r"D:\python_work\work2026-10\Agent-bubble")
CANDIDATE = Path(r"D:\python_work\work2026-4\memory-base-bubble-bridge-assets\20261012\candidate92")
FILES = ("server/memory-tools.ts", "server/tests/memory-base.test.ts")


def main():
    store = HERE / "store/baseline"
    store.mkdir(parents=True)
    report = {"actual": str(ACTUAL), "candidate": str(CANDIDATE),
        "candidate_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=CANDIDATE, text=True).strip(), "files": []}
    for name in FILES:
        row = {"path": name}
        for label, root in (("actual", ACTUAL), ("candidate", CANDIDATE)):
            raw = (root / name).read_bytes()
            target = store / label / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as output:
                output.write(raw)
            row[label] = {"sha256": hashlib.sha256(raw).hexdigest(),
                "lf_normalized_sha256": hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(),
                "bytes": len(raw), "crlf": raw.count(b"\r\n"), "raw_backup": str(target)}
        row["normalized_equal"] = row["actual"]["lf_normalized_sha256"] == row["candidate"]["lf_normalized_sha256"]
        report["files"].append(row)
    with (HERE / "baseline.json").open("x", encoding="utf-8") as output:
        output.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
