"""Small real POSIX venv differential; no pip, SDK, model or service calls."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def inspect(python):
    script = "import sys,sysconfig,json;print(json.dumps({'entry':sys.executable,'prefix':sys.prefix,'purelib':sysconfig.get_path('purelib')}))"
    environment = {key: value for key, value in os.environ.items()
                   if key not in ("PYTHONPATH", "PYTHONHOME") and not key.startswith("MEMORY_")}
    return json.loads(subprocess.check_output([str(python), "-B", "-c", script], env=environment, text=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected", choices=("red", "green"), required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    report = args.report.resolve()
    if report.exists() or not report.is_relative_to(repo / "experiments/memory-base-runtime-path"):
        raise RuntimeError("Refuse report overwrite or out-of-scope target")
    if os.name != "posix":
        raise RuntimeError("Real POSIX symlinks required")
    sys.path.insert(0, str(repo))
    from memory_base import deploy
    fixture = Path(tempfile.mkdtemp(prefix="agent-kb-97-"))
    rows = []
    for name in ("a", "b"):
        root = fixture / name
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", "--symlinks", str(root)], check=True, timeout=30)
        entry = root / "bin/python"
        assert entry.is_symlink()
        _, selected = deploy._install_state(fixture / "unused-home", str(entry), False)
        expected = inspect(entry)
        observed = inspect(selected)
        rows.append({"entry": str(entry), "resolved_base": str(entry.resolve()), "selected": str(selected),
                     "expected": expected, "observed": observed,
                     "preserves_environment": expected["prefix"] == observed["prefix"] and expected["purelib"] == observed["purelib"]})
    assert rows[0]["resolved_base"] == rows[1]["resolved_base"]
    assert rows[0]["expected"]["prefix"] != rows[1]["expected"]["prefix"]
    green = all(row["preserves_environment"] for row in rows) and rows[0]["selected"] != rows[1]["selected"]
    verdict = "green" if green else "red"
    output = {"verdict": verdict, "python_version": sys.version, "fixture_root": str(fixture), "rows": rows,
              "boundary": "Only real POSIX symlink/prefix/purelib selection; no Python3.12 wheel or dependency-stack acceptance claim."}
    report.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": verdict, "report": report.name, "pid": os.getpid(), "python": sys.version.split()[0]}), flush=True)
    assert verdict == args.expected, "Interpreter selection differential did not match expected verdict"


if __name__ == "__main__":
    main()
