"""Export the two frozen LF files; preserve deployed raw hashes and old failures."""
import hashlib
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
CANDIDATE = Path(r"D:\python_work\work2026-4\memory-base-bubble-bridge-assets\20261012\candidate92")
FROZEN = "36f9bf294e4117bf36a4228d63b3a3c7ad715af1"
BASE = "0a86cb1013a481aa91b4b97b826d62a4a8456fb8"
FILES = ("server/memory-tools.ts", "server/tests/memory-base.test.ts")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    git = lambda *args: subprocess.check_output(["git", *args], cwd=CANDIDATE)
    assert git("rev-parse", "HEAD").decode().strip() == FROZEN and not git("status", "--porcelain").strip()
    assert sorted(git("diff", BASE, FROZEN, "--name-only").decode().splitlines()) == sorted(FILES)
    baseline = json.loads((HERE / "baseline.json").read_text(encoding="utf-8"))
    report = {"candidate_commit": FROZEN, "base_commit": BASE, "files": [],
              "actual_files_unchanged": True, "candidate_clean": True}
    for row in baseline["files"]:
        name = row["path"]
        raw = (CANDIDATE / name).read_bytes()
        lf = raw.replace(b"\r\n", b"\n")
        assert lf == git("show", FROZEN + ":" + name)
        actual = (Path(baseline["actual"]) / name).read_bytes()
        assert sha(actual) == row["actual"]["sha256"], "Actual deployed source changed before delivery"
        artifact = HERE / "artifacts" / name
        artifact.parent.mkdir(parents=True, exist_ok=True)
        with artifact.open("xb") as output:
            output.write(lf)
        report["files"].append({"path": name, "artifact": str(artifact), "artifact_sha256": sha(lf),
            "candidate_raw_sha256": sha(raw), "candidate_lf_sha256": sha(lf),
            "candidate_crlf": raw.count(b"\r\n"), "actual_raw_sha256": sha(actual),
            "baseline_candidate_raw_sha256": row["candidate"]["sha256"],
            "baseline_normalized_sha256": row["candidate"]["lf_normalized_sha256"]})
    patch = git("diff", "--no-ext-diff", "--binary", BASE, FROZEN, "--", *FILES)
    with (HERE / "note-intent-two-files.patch").open("xb") as output:
        output.write(patch)
    report["patch_sha256"] = sha(patch)
    with (HERE / "delivery.json").open("x", encoding="utf-8") as output:
        output.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
