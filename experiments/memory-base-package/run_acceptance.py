"""Reproduce #86 without modifying source, formal runtime or host configuration."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
import zipfile

FROZEN_SHA = "d8a670905f93c86b36c6957b601fd8c2f02d0f45"
PREREGISTRATION_SHA = "fc0b0e9dcb418b3b8b6cf8d93247198c541eb162"
MAIN_PYTHON = Path(r"D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe")
FORMAL_PYTHON = Path(r"C:\Users\Tan\.agent-memory-base\runtime\Scripts\python.exe")
HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def clean_env() -> dict:
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("MEMORY_") and key.upper() not in {"PYTHONPATH", "PYTHONHOME"}}
    env.update({"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
    return env


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-python", type=Path, default=MAIN_PYTHON)
    parser.add_argument("--formal-python", type=Path, default=FORMAL_PYTHON)
    args = parser.parse_args()
    assert Path(sys.executable).resolve() == args.main_python.resolve(), "Run with the activated main venv Python."
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    store = HERE / "store" / ("run-" + stamp)
    store.mkdir(parents=True, exist_ok=False)
    result = {"frozen_sha": FROZEN_SHA, "preregistration_commit": PREREGISTRATION_SHA,
              "started_utc": stamp, "platform": sys.platform, "store": str(store),
              "commands": [], "checks": {}, "limitations": [
                  "Dependencies are reused read-only from the formal Python 3.12 runtime and base system site-packages.",
                  "No clean-environment upstream dependency installation was attempted.",
                  "MCP HTTP gateway uses synthetic responses; real OpenViking behavior belongs to #85.",
                  "Installer subprocess and credential operations are stubbed for boundary observation only.",
                  "Windows/Python 3.12 evidence only; POSIX first credential persistence remains unverified/limited."]}

    def save():
        (store / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def run(command, *, cwd=REPO, expected=0, timeout=90):
        command = [str(item) for item in command]
        completed = subprocess.run(command, cwd=cwd, env=clean_env(), capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=timeout)
        entry = {"command": command, "cwd": str(cwd), "exit_code": completed.returncode,
                 "stdout": completed.stdout, "stderr": completed.stderr, "pythonpath": None}
        result["commands"].append(entry)
        save()
        if expected is not None:
            assert completed.returncode == expected, {"command": command, "exit_code": completed.returncode,
                                                     "stderr": completed.stderr}
        return completed

    try:
        prereg = run(["git", "merge-base", "--is-ancestor", PREREGISTRATION_SHA, "HEAD"])
        identity_code = ("import sys,sysconfig,importlib.metadata as m,json;"
                         "print(json.dumps({'python':sys.version,'executable':sys.executable,"
                         "'base_prefix':sys.base_prefix,'purelib':sysconfig.get_path('purelib'),"
                         "'pip':m.version('pip')}))")
        identities = {"build": json.loads(run([args.main_python, "-c", identity_code]).stdout),
                      "formal_readonly": json.loads(run([args.formal_python, "-c", identity_code]).stdout)}
        result["identities"] = identities
        assert identities["build"]["python"].startswith("3.12.")
        assert identities["formal_readonly"]["python"].startswith("3.12.")
        archive = store / "source.zip"
        run(["git", "archive", "--format=zip", "--output", archive, FROZEN_SHA, "memory_base"])
        source = store / "source"
        with zipfile.ZipFile(archive) as z:
            z.extractall(source)
        package = source / "memory_base"
        config = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))
        result["package_config"] = config
        wheels = store / "wheels"
        wheels.mkdir()
        run([args.main_python, "-m", "pip", "wheel", package, "--no-deps", "--no-build-isolation",
             "--no-index", "--wheel-dir", wheels], timeout=120)
        wheel, = wheels.glob("*.whl")
        inventory = []
        with zipfile.ZipFile(wheel) as z:
            names = sorted(z.namelist())
            assert all(name.startswith(("memory_base/", "agent_memory_base-0.1.0.dist-info/")) for name in names)
            for path in package.rglob("*"):
                if path.is_file() and (path.suffix == ".py" or path.name in {"requirements.txt", "constraints.txt", "SKILL.md"}):
                    if "build" in path.relative_to(package).parts:
                        continue
                    name = "memory_base/" + path.relative_to(package).as_posix()
                    assert name in names, name
                    assert z.read(name) == path.read_bytes(), name
            for name in names:
                payload = z.read(name)
                inventory.append({"path": name, "bytes": len(payload), "sha256": sha256(payload)})
            metadata_name = next(name for name in names if name.endswith("/METADATA"))
            wheel_metadata = BytesParser().parsebytes(z.read(metadata_name))
            record_name = next(name for name in names if name.endswith("/RECORD"))
            for line in z.read(record_name).decode().splitlines():
                name, digest, size = line.rsplit(",", 2)
                if name == record_name:
                    assert digest == size == ""
                else:
                    payload = z.read(name)
                    actual = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()
                    assert digest == "sha256=" + actual and size == str(len(payload)), name
            result["wheel"] = {"path": str(wheel), "sha256": sha256(wheel.read_bytes()),
                               "bytes": wheel.stat().st_size, "inventory": inventory,
                               "requires_python": wheel_metadata["Requires-Python"],
                               "requires_dist": wheel_metadata.get_all("Requires-Dist"),
                               "entry_points": z.read(next(n for n in names if n.endswith("/entry_points.txt"))).decode(),
                               "record_verified": True, "source_bytes_match": True}
        result["checks"]["wheel_inventory_and_record"] = "PASS"
        isolated = store / "venv"
        run([args.formal_python, "-m", "venv", "--system-site-packages", isolated], timeout=120)
        python = isolated / "Scripts" / "python.exe"
        initial_identity = json.loads(run([python, "-c", identity_code]).stdout)
        purelib = Path(initial_identity["purelib"])
        formal_site = identities["formal_readonly"]["purelib"]
        pth = purelib / "formal-dependencies-readonly.pth"
        # A path alone does not initialize Windows pywin32 DLL lookup. Process
        # only that dependency's standard .pth; do not run editable package .pth.
        pth.write_text("import sys,site; sys.path.insert(sys.path.index(" + repr(str(purelib)) + ")+1, "
                       + repr(formal_site) + "); site.addpackage(" + repr(formal_site)
                       + ", 'pywin32.pth', set(sys.path))\n", encoding="utf-8")
        result["dependency_reuse"] = {"venv_system_site_packages": True, "formal_sitepackages": formal_site,
                                     "base_prefix": initial_identity["base_prefix"], "pth": str(pth),
                                     "pth_sha256": sha256(pth.read_bytes()),
                                     "explicit_dependency_bootstrap": {
                                         "file": str(Path(formal_site) / "pywin32.pth"),
                                         "sha256": sha256((Path(formal_site) / "pywin32.pth").read_bytes())}}
        # The reused formal site may already advertise this distribution. Force
        # installation only into the new venv, without uninstalling that copy.
        run([python, "-m", "pip", "install", "--no-deps", "--no-index", "--ignore-installed", wheel], timeout=120)
        result["checks"]["wheel_no_deps_install"] = "PASS"
        modules = sorted("memory_base" if item["path"] == "memory_base/__init__.py"
                         else item["path"].removesuffix(".py").replace("/", ".")
                         for item in inventory if item["path"].startswith("memory_base/") and item["path"].endswith(".py"))
        probe = HERE / "probe_installed.py"
        foreign_cwds = [Path(os.environ["TEMP"]), Path(os.environ["SystemRoot"]) / "System32"]
        origins = []
        for cwd in foreign_cwds:
            assert not cwd.resolve().is_relative_to(REPO)
            checked = run([python, probe, "imports", "--site", purelib, "--modules", *modules], cwd=cwd)
            origins.append(json.loads(checked.stdout))
            for command in ([python, "-m", "memory_base", "--help"],
                            [isolated / "Scripts" / "memory-base.exe", "--help"],
                            [python, "-m", "memory_base.mcp_stdio", "--help"]):
                help_result = run(command, cwd=cwd)
                assert "usage:" in help_result.stdout
        result["imports_from_foreign_cwds"] = origins
        result["checks"]["all_installed_module_imports_and_cli"] = "PASS"
        pins = json.loads(run([python, "-c", "import json;from memory_base.deploy import PINS;print(json.dumps(PINS))"],
                              cwd=foreign_cwds[0]).stdout)
        normalize = lambda name: re.sub(r"[-_.]+", "-", name).lower()
        def versions(lines):
            found = {}
            for line in lines:
                match = re.fullmatch(r"([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==([^\s;]+)", line.strip())
                if match:
                    found[normalize(match[1])] = match[2]
            return found
        declared = versions(config["project"]["dependencies"])
        requirements = versions((package / "requirements.txt").read_text().splitlines())
        constraints = versions((package / "constraints.txt").read_text().splitlines())
        assert declared == requirements == pins
        assert {name: constraints[name] for name in pins} == pins
        assert versions(result["wheel"]["requires_dist"]) == pins
        runtime_versions = json.loads(run([args.formal_python, "-c",
            "import importlib.metadata as m,json;print(json.dumps({n:m.version(n) for n in " + repr(list(pins)) + "}))"],
            cwd=foreign_cwds[0]).stdout)
        assert runtime_versions == pins
        result["pin_consistency"] = {"pins": pins, "project": declared, "requirements": requirements,
                                     "constraints_direct": {name: constraints[name] for name in pins},
                                     "formal_versions": runtime_versions}
        result["checks"]["direct_pin_consistency"] = "PASS"
        mcp_store = store / "mcp"
        mcp_store.mkdir()
        result["mcp"] = json.loads(run([python, probe, "mcp", "--store", mcp_store],
                                        cwd=foreign_cwds[0], timeout=60).stdout)
        result["checks"]["real_mcp_four_tools_against_stub_gateway"] = "PASS"
        boundary_store = store / "install-boundary"
        boundary_store.mkdir()
        result["installer_boundary"] = json.loads(run([python, probe, "install-boundary", "--store", boundary_store,
                                                         "--formal-python", args.formal_python],
                                                        cwd=foreign_cwds[0]).stdout)
        result["checks"]["installer_interpreter_boundary_observed"] = "PASS"
        reproduction = run([python, HERE / "reproduce_default_install.py", "--store", store],
                           cwd=foreign_cwds[0])
        result["default_install_reproduction"] = json.loads(reproduction.stdout)
        # Observing the rejection is a successful probe, but the default product
        # entrypoint still fails to deploy the wheel into its selected runtime.
        result["checks"]["wheel_default_and_fresh_runtime_bootstrap"] = result["default_install_reproduction"]["product_gate"]
        checked = run([args.formal_python, "-m", "pip", "check"], cwd=foreign_cwds[0], expected=None)
        result["formal_pip_check"] = {"exit_code": checked.returncode, "stdout": checked.stdout,
                                      "stderr": checked.stderr, "readonly": True}
        result["checks"]["formal_runtime_pip_check"] = "PASS" if checked.returncode == 0 else "FAIL"
        result["package_protocol_overall"] = ("PASS" if all(value == "PASS" for name, value in result["checks"].items()
            if name != "wheel_default_and_fresh_runtime_bootstrap") else "FAIL")
        result["overall"] = "PASS" if all(value == "PASS" for value in result["checks"].values()) else "FAIL"
    except Exception as error:
        result["overall"] = "FAIL"
        result["failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        save()
        (HERE / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"overall": result.get("overall"), "checks": result["checks"],
                          "store": str(store), "wheel_sha256": result.get("wheel", {}).get("sha256")}, indent=2))
    if result["overall"] == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
