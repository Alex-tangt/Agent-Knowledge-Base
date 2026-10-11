"""Safely reproduce the wheel's default/fresh-runtime CLI bootstrap failure."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from memory_base import deploy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", required=True, type=Path)
    args = parser.parse_args()
    experiment = Path(__file__).resolve().parent
    store = args.store.resolve()
    assert store.is_relative_to(experiment / "store")
    assert Path(deploy.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    assert not Path(deploy.__file__).with_name("pyproject.toml").exists()
    cases = {}
    for name, flags in (("default_new_home", []), ("fresh_runtime", ["--fresh-runtime"])):
        home = store / "default-cli" / name
        calls = []

        def capture(command, **kwargs):
            calls.append([str(item) for item in command])
            return subprocess.CompletedProcess(command, 0, "", "")

        out, err = io.StringIO(), io.StringIO()
        argv = ["install", "--home", str(home), *flags]
        with patch.object(deploy.subprocess, "run", capture), \
                patch.object(deploy, "_versions", lambda python: dict(deploy.PINS)), \
                patch.object(deploy, "_ensure_secret", side_effect=AssertionError("Unexpected credential operation")), \
                redirect_stdout(out), redirect_stderr(err):
            exit_code = deploy.main(argv)
        assert exit_code == 1
        expected = "memory-base: Installing into a new runtime requires the source checkout; this package was installed as a wheel\n"
        assert err.getvalue() == expected
        assert len(calls) == 2
        assert calls[0][1:3] == ["-m", "venv"]
        assert calls[1][1:5] == ["-m", "pip", "install", "--no-compile"]
        assert not any("-e" in call or any(item.endswith(".whl") for item in call) for call in calls)
        cases[name] = {"entrypoint": "memory_base.deploy.main (wheel console-script target)",
                       "argv": argv, "cli_exit_code": exit_code, "stdout": out.getvalue(),
                       "stderr": err.getvalue(), "subprocess_calls_not_executed": calls,
                       "memory_base_install_requested_for_target_runtime": False}
    result = {"frozen_sha": "d8a670905f93c86b36c6957b601fd8c2f02d0f45",
              "python": sys.executable, "installed_deploy_origin": deploy.__file__,
              "cwd": str(Path.cwd()), "product_gate": "FAIL", "cases": cases,
              "boundary": "Subprocess creation/pip and version verification are stubbed; no upstream dependencies installed and no credential operations. This reproduces CLI control flow, not a clean dependency installation.",
              "finding": "An installed wheel cannot use the default install or --fresh-runtime entrypoint to deploy itself into the newly selected runtime."}
    (store / "default-install-reproduction.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
