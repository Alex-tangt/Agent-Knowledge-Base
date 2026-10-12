"""Independent R2: real wheel, dependency installs, native behavior and cleanup."""
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
import socket
import subprocess
import sys
import tomllib
from urllib.parse import urlsplit
import zipfile

FROZEN = "45296a58fe2559e0b92f4af851c018b56148865a"
MAIN = Path(r"D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe")
FORMAL = Path(r"C:\Users\Tan\.agent-memory-base\runtime\Scripts\python.exe")
MODEL = Path(r"C:\Users\Tan\.cache\openviking\models\bge-small-zh-v1.5-f16.gguf")
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
FOREIGN = [Path(os.environ["TEMP"]), Path(os.environ["SystemRoot"]) / "System32"]


def sha(content):
    return hashlib.sha256(content).hexdigest()


def clean_env():
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith(("MEMORY_", "PIP_"))
           and key.upper() not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PYTHONDONTWRITEBYTECODE", "PYTHONOPTIMIZE", "PYTHONPYCACHEPREFIX"}}
    env.update({"PYTHONUTF8": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_CONFIG_FILE": os.devnull, "PIP_INDEX_URL": "https://pypi.org/simple",
                "NO_PROXY": "localhost,127.0.0.1,::1"})
    return env


def versions(lines):
    found = {}
    for line in lines:
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==([^\s;]+)", line.strip())
        if match:
            found[re.sub(r"[-_.]+", "-", match[1]).lower()] = match[2]
    return found


def ports():
    sockets = [socket.socket() for _ in range(3)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        selected = dict(zip(("upstream", "gateway", "embed"), [sock.getsockname()[1] for sock in sockets]))
    finally:
        for sock in sockets:
            sock.close()
    return selected


def main():
    assert Path(sys.executable).resolve() == MAIN.resolve(), "Activate the main venv before running R2"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-name", default="run-" + stamp, help="New unique store directory name; use a short name on Windows")
    parser.add_argument("--asset-root", type=Path, help="Owner-authorized external R4 fixture root only")
    args = parser.parse_args()
    assert args.run_name not in {".", ".."} and Path(args.run_name).name == args.run_name
    if args.asset_root is not None:
        parent = Path(r"C:\Users\Tan\.mb86")
        authorized = parent / "r5"
        assert parent.resolve() == parent.absolute(), "Asset parent may not redirect through an alias"
        assert args.asset_root.resolve() == authorized.resolve(), "External asset scope is limited to .mb86/r5"
        assert authorized.resolve().parent == parent.resolve()
        assert not authorized.exists(), "Preserve all existing assets; choose a newly authorized root"
        store = authorized.resolve()
    else:
        store = HERE / "store" / args.run_name
    store.mkdir(parents=True, exist_ok=False)
    if args.asset_root is not None:
        note = store.parent / "README-kb86-r5.txt"
        with note.open("x", encoding="utf-8") as handle:
            handle.write("Owner-authorized #86 R4 fixture assets only. Preserve r4, its runtimes, data, wheels and logs.\n"
                         "Repository evidence: experiments/memory-base-package/recheck-2026-10-12/.\n"
                         "Frozen product: " + FROZEN + ". No formal service/config/runtime ownership.\n")
    report = {"frozen_sha": FROZEN, "store": str(store), "started_utc": stamp, "commands": [], "checks": {},
              "script_hashes": {path.name: sha(path.read_bytes()) for path in HERE.glob("*.py")},
              "limitations": ["Windows/Python 3.12 only; POSIX first credential persistence is not verified.",
                              "Launcher explicitly reuses formal dependencies; the two target runtimes do not.",
                              "No LLM/summary generation; native search uses the existing local GGUF model."]}
    report["fixture_path_layout"] = {"launcher": "l", "default_home": "d", "fresh_home": "f",
                                    "short_home_evidence_only": True, "windows_long_paths_not_enabled_by_test": True,
                                    "authorized_external_asset_root": args.asset_root is not None}
    report['environment_policy']={name:clean_env().get(name) for name in
        ('PYTHONDONTWRITEBYTECODE','PYTHONOPTIMIZE','PYTHONPYCACHEPREFIX')}
    # R2 method correction: preserve this machine's existing credential-free
    # loopback network proxy; private pip configuration remains excluded.
    proxy_policy = {}
    for name, value in clean_env().items():
        if name.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}:
            parsed = urlsplit(value)
            assert parsed.hostname in {"localhost", "127.0.0.1", "::1"} and not parsed.username
            proxy_policy[name.upper()] = {"loopback": True, "userinfo": False, "value_logged": False}
    report["network_proxy_policy"] = proxy_policy

    def save():
        (store / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def run(command, *, cwd=FOREIGN[0], stage="command", expected=0):
        command = list(map(str, command))
        if command[0].lower().endswith('l\\scripts\\python.exe') and command[1]!='-B':
            command.insert(1,'-B')
        log = store / (f"command-{len(report['commands']):03d}-{stage}.log")
        entry = {"command": command, "cwd": str(cwd), "stage": stage, "log": str(log), "status": "running"}
        print(json.dumps({"stage": stage, "command": command, "log": str(log)}, ensure_ascii=False), flush=True)
        with log.open("wb") as output:
            process = subprocess.Popen(command, cwd=cwd, env=clean_env(), stdout=output, stderr=subprocess.STDOUT)
            entry["pid"] = process.pid
            report["commands"].append(entry)
            save()
            # Preserve actual pip/session until completion; no observation timeout.
            import psutil,time
            preparation_seen={}
            while process.poll() is None:
                try:
                    for child in psutil.Process(process.pid).children(recursive=True):
                        cmd=child.cmdline()
                        if '-c' in cmd and any('compileall.compile_dir' in part for part in cmd):
                            item=preparation_seen.setdefault(str(child.pid),{'pid':child.pid,'birth':child.create_time(),
                                'first_observed_utc':datetime.now(timezone.utc).isoformat(),
                                'target_executable':cmd[0],'actual_prepare_child':True})
                            item['last_observed_utc']=datetime.now(timezone.utc).isoformat()
                            report.setdefault('automatic_preparation_children',{})[stage]=preparation_seen
                            save()
                except (psutil.Error,OSError):
                    pass
                time.sleep(0.5)
            entry["exit_code"]=process.returncode
        entry["status"] = "completed"
        save()
        text = log.read_text(encoding="utf-8", errors="replace")
        if expected is not None and entry["exit_code"] != expected:
            report["failure_stage"] = stage
            raise RuntimeError(f"{stage} exited {entry['exit_code']}; see {log}")
        return text

    def checked(key):
        report["checks"][key] = "PASS"
        save()
        print(json.dumps({"check": key, "result": "PASS"}), flush=True)

    def configuration(home):
        return json.loads((home / "deployment.json").read_text(encoding="utf-8"))

    def set_fixture_ports(home):
        config = configuration(home)
        config["ports"] = ports()
        (home / "deployment.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        report.setdefault("fixture_setup", []).append({"home": str(home), "ports": config["ports"],
            "reason": "CLI has no ports option; only this unstarted test deployment.json is modified."})
        save()
        return config

    def inspect(python, *, clean=False):
        command = [python, HERE / "inspect_environment.py"]
        if clean:
            command += ["--clean-target", "--forbidden-site", formal_site]
        info = json.loads(run(command, stage="target-origins" if clean else "launcher-origins"))
        assert info["direct_url"]["archive_info"]["hashes"]["sha256"] == report["wheel"]["sha256"]
        assert info["direct_url"]["url"] == wheel.as_uri()
        assert info["pins"] == pins
        return info

    def external_checks(python, site, label):
        observations = []
        for cwd in FOREIGN:
            assert not cwd.resolve().is_relative_to(REPO)
            observations.append(json.loads(run([python, original_probe, "imports", "--site", site,
                                                "--modules", *modules], cwd=cwd, stage=label + "-imports")))
            for command in ([python, "-m", "memory_base", "--help"],
                            [Path(python).parent / "memory-base.exe", "--help"],
                            [python, "-m", "memory_base.mcp_stdio", "--help"]):
                assert "usage:" in run(command, cwd=cwd, stage=label + "-help")
        report[label + "_external_cwds"] = observations
        save()

    try:
        prereg = run(["git", "log", "-1", "--format=%H", "--", str(HERE / "PREREGISTRATION.md")],
                     cwd=REPO, stage="preregistration").strip()
        run(["git", "merge-base", "--is-ancestor", prereg, "HEAD"], cwd=REPO, stage="preregistration-ancestor")
        report["preregistration_commit"] = prereg
        report["script_commit"] = run(["git", "rev-parse", "HEAD"], cwd=REPO, stage="script-commit").strip()
        identity_code = ("import json,sys,sysconfig,importlib.metadata as m;print(json.dumps({"
                         "'python':sys.version,'purelib':sysconfig.get_path('purelib'),'pip':m.version('pip')}))")
        report["build_identity"] = json.loads(run([MAIN, "-c", identity_code], stage="build-identity"))
        report["formal_readonly_identity"] = json.loads(run([FORMAL, "-c", identity_code], stage="formal-identity"))
        assert report["build_identity"]["python"].startswith("3.12.")
        formal_site = Path(report["formal_readonly_identity"]["purelib"])
        archive = store / "source.zip"
        run(["git", "archive", "--format=zip", "--output", archive, FROZEN, "memory_base"], cwd=REPO, stage="export")
        source = store / "source"
        with zipfile.ZipFile(archive) as exported:
            exported.extractall(source)
        package = source / "memory_base"
        tracked = run(["git", "ls-tree", "-r", "--name-only", FROZEN, "memory_base"], cwd=REPO, stage="tracked").splitlines()
        exported_hashes = []
        for name in tracked:
            raw = subprocess.check_output(["git", "show", FROZEN + ":" + name], cwd=REPO)
            content = (source / name).read_bytes()
            assert raw.replace(b"\r\n", b"\n") == content.replace(b"\r\n", b"\n"), "Archive changed source content"
            exported_hashes.append({"path": name, "git_blob_sha256": sha(raw), "export_sha256": sha(content),
                                    "raw_bytes_equal": raw == content, "crlf_normalized_equal": True})
        config = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))
        wheels = store / "wheels"
        wheels.mkdir()
        run([MAIN, "-m", "pip", "wheel", package, "--no-deps", "--no-build-isolation", "--no-index",
             "--wheel-dir", wheels], cwd=REPO, stage="build-wheel")
        wheel, = wheels.glob("*.whl")
        with zipfile.ZipFile(wheel) as built:
            names = sorted(built.namelist())
            assert all(name.startswith(("memory_base/", "agent_memory_base-0.1.0.dist-info/")) for name in names)
            package_files = [name for name in tracked if name.endswith(".py") or
                             Path(name).name in {"requirements.txt", "constraints.txt", "SKILL.md"}]
            assert set(name for name in names if name.startswith("memory_base/")) == set(package_files)
            for name in package_files:
                assert built.read(name) == (source / name).read_bytes(), "Wheel differs from archive source"
            metadata = BytesParser().parsebytes(built.read("agent_memory_base-0.1.0.dist-info/METADATA"))
            assert metadata["Name"] == "agent-memory-base" and metadata["Version"] == "0.1.0"
            assert metadata["Requires-Python"] == ">=3.12,<3.13" or metadata["Requires-Python"] == "<3.13,>=3.12"
            entrypoints = built.read("agent_memory_base-0.1.0.dist-info/entry_points.txt").decode()
            assert "memory-base = memory_base.deploy:main" in entrypoints
            record = "agent_memory_base-0.1.0.dist-info/RECORD"
            for line in built.read(record).decode().splitlines():
                name, digest, size = line.rsplit(",", 2)
                if name == record:
                    assert digest == size == ""
                else:
                    content = built.read(name)
                    encoded = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
                    assert digest == "sha256=" + encoded and size == str(len(content))
            report["wheel"] = {"path": str(wheel), "sha256": sha(wheel.read_bytes()), "archive_sha256": sha(archive.read_bytes()),
                "inventory": [{"path": name, "sha256": sha(built.read(name)), "bytes": len(built.read(name))} for name in names],
                "exported_source": exported_hashes, "wheel_export_bytes_match": True, "record_verified": True,
                "entry_points": entrypoints, "requires_python": metadata["Requires-Python"],
                "requires_dist": metadata.get_all("Requires-Dist")}
        checked("wheel_inventory_source_metadata_record")
        launcher = store / "l"
        run([MAIN, "-m", "venv", launcher], stage="create-launcher")
        launcher_python = launcher / "Scripts/python.exe"
        site = Path(json.loads(run([launcher_python, "-c", identity_code], stage="launcher-identity"))["purelib"])
        pth = site / "formal-dependencies-readonly.pth"
        pth.write_text("import sys,site; sys.path.insert(sys.path.index(" + repr(str(site)) + ")+1, " + repr(str(formal_site))
                       + "); site.addpackage(" + repr(str(formal_site)) + ", 'pywin32.pth', set(sys.path))\n", encoding="utf-8")
        report["launcher_dependency_reuse"] = {"provider_site": str(formal_site), "pth": str(pth), "pth_sha256": sha(pth.read_bytes()),
                                               "system_site_packages": False, "new_dependency_stack": False}
        run([launcher_python, "-m", "pip", "install", "--no-deps", "--no-index", "--ignore-installed", wheel], stage="install-launcher-wheel")
        pins = versions(config["project"]["dependencies"])
        assert versions((package / "requirements.txt").read_text().splitlines()) == pins
        constraints = versions((package / "constraints.txt").read_text().splitlines())
        assert {name: constraints[name] for name in pins} == pins
        assert versions(report["wheel"]["requires_dist"]) == pins
        report["launcher_origins"] = inspect(launcher_python)
        checked("launcher_real_no_deps_wheel_install")
        modules = sorted("memory_base" if name == "memory_base/__init__.py" else name[:-3].replace("/", ".")
                         for name in package_files if name.endswith(".py"))
        original_probe = HERE / "probe_installed.py"
        report["reused_probe_sha256"] = sha(original_probe.read_bytes())
        external_checks(launcher_python, site, "launcher")
        checked("external_cwd_all_modules_console_module_mcp_help")
        protocol_store = store / "synthetic-protocol"
        protocol_store.mkdir()
        report["synthetic_protocol"] = json.loads(run([launcher_python, original_probe, "mcp", "--store", protocol_store], stage="synthetic-mcp"))
        checked("real_mcp_four_tools_synthetic_protocol_only")
        launcher_command = [launcher_python, "-B", "-m", "memory_base"]
        default_home = store / "d"
        assert not default_home.exists()
        run([*launcher_command, "install", "--home", default_home, "--model", MODEL], stage="default-real-install")
        target_python = default_home / "runtime/Scripts/python.exe"
        default_config = set_fixture_ports(default_home)
        report["default_deployment"] = default_config
        target_info = inspect(target_python, clean=True)
        site_path=Path(target_info['site'])
        roots={name:{'python_files':len(list((site_path/name).rglob('*.py'))),
                     'pyc_files':len(list((site_path/name).rglob('*.pyc')))} for name in ('lark_oapi','volcenginesdk')}
        report['default_pre_native']={'sdk_counts':roots,'data_exists':(default_home/'data').exists(),
            'process_records_exists':(default_home/'processes.json').exists(),
            'automatic_preparation':report.get('automatic_preparation_children',{}).get('default-real-install',{}),
            'service_timeout_default':180,'no_start_timeout_argument':True}
        assert not (default_home/'data').exists()
        assert roots['lark_oapi']['python_files']==roots['lark_oapi']['pyc_files']
        assert report['default_pre_native']['automatic_preparation']
        save()
        assert Path(default_config["source"]).resolve() == Path(target_info["site"]).resolve()
        report["default_target_origins"] = target_info
        report["default_target_pip_check"] = run([target_python, "-m", "pip", "check"], stage="default-pip-check").strip()
        checked("default_auto_artifact_full_dependency_package_install")
        external_checks(target_python, target_info["site"], "default-target")
        report["native"] = json.loads(run([target_python, HERE / "native_probe.py", "--home", default_home, "--store", store], stage="default-native"))
        assert report["native"]["overall"] == "PASS"
        checked("default_native_http_permissions_search_list_stdio_cleanup")
        fresh_home = store / "f"
        assert not fresh_home.exists()
        run([*launcher_command, "install", "--home", fresh_home, "--runtime-python", target_python, "--model", MODEL], stage="adopt-real-install")
        adopted = set_fixture_ports(fresh_home)
        assert Path(adopted["python"]).resolve() == target_python.resolve()
        marker = fresh_home / "data/r2-preserved-marker.txt"
        marker.parent.mkdir(parents=True)
        marker.write_bytes("合成 fresh 保留标记\r\n".encode())
        marker_hash = sha(marker.read_bytes())
        assert not (fresh_home / "runtime").exists()
        run([*launcher_command, "install", "--home", fresh_home, "--wheel", wheel, "--fresh-runtime"], stage="fresh-real-install")
        fresh = configuration(fresh_home)
        fresh_python = fresh_home / "runtime/Scripts/python.exe"
        assert Path(fresh["python"]).resolve() == fresh_python.resolve()
        assert fresh["ports"] == adopted["ports"] and fresh["model"] == adopted["model"]
        assert sha(marker.read_bytes()) == marker_hash
        fresh_info = inspect(fresh_python, clean=True)
        assert Path(fresh["source"]).resolve() == Path(fresh_info["site"]).resolve()
        report["fresh_case"] = {"adopted": adopted, "fresh": fresh, "target_origins": fresh_info,
                               "marker_sha256": marker_hash, "home_model_ports_marker_preserved": True}
        report["fresh_target_pip_check"] = run([fresh_python, "-m", "pip", "check"], stage="fresh-pip-check").strip()
        checked("adopt_then_explicit_wheel_fresh_full_install_preservation")
        checked("direct_pins_constraints_target_origins_and_pip_check")
        report["formal_pip_check"] = run([FORMAL, "-m", "pip", "check"], stage="formal-readonly-pip-check").strip()
        checked("formal_runtime_readonly_pip_check")
        report["overall"] = "PASS"
    except Exception as error:
        report["overall"] = "FAIL"
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        save()
        (HERE / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"overall": report.get("overall"), "store": str(store), "checks": report["checks"],
                          "failure": report.get("failure")}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
