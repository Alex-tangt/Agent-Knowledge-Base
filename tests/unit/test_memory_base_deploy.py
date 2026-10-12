"""Deployment boundaries: protected secrets, owned processes and serialized model."""

import base64
import csv
import hashlib
import io
import json
import os
from contextlib import contextmanager
import shutil
import socket
import subprocess
import sys
import sysconfig
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
import textwrap
import zipfile

import pytest

from memory_base import credentials, deploy
from memory_base.embed import EmbeddingState

PACKAGE_SOURCE = Path(deploy.__file__).resolve().parent


def _wheel_fixture(tmp_path, *, product="agent-memory-base", version="0.1.0", missing=None, corrupt=False):
    """A real ZIP/RECORD fixture built from the product's own package files."""
    path = tmp_path / ("agent_memory_base-" + version + "-py3-none-any.whl")
    dist = "agent_memory_base-" + version + ".dist-info/"
    files = {"memory_base/" + item.name: item.read_bytes() for item in PACKAGE_SOURCE.glob("*.py")}
    for name in ("requirements.txt", "constraints.txt", "skill/SKILL.md"):
        files["memory_base/" + name] = (PACKAGE_SOURCE / name).read_bytes()
    files.update({dist + "METADATA": f"Metadata-Version: 2.1\nName: {product}\nVersion: {version}\n".encode(),
                  dist + "WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
                  dist + "entry_points.txt": b"[console_scripts]\nmemory-base = memory_base.deploy:main\n"})
    if missing:
        files.pop(missing)
    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for name, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        writer.writerow([name, "sha256=" + digest, len(content)])
    writer.writerow([dist + "RECORD", "", ""])
    files[dist + "RECORD"] = record.getvalue().encode()
    if corrupt:
        files["memory_base/__init__.py"] += b"# changed after RECORD\n"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def _wheel_launcher(tmp_path, monkeypatch, origin=None, version="0.1.0"):
    installed = tmp_path / "launcher" / "site-packages" / "memory_base" / "deploy.py"
    installed.parent.mkdir(parents=True)
    installed.write_text("installed wheel fixture")
    monkeypatch.setattr(deploy, "__file__", str(installed))

    class Distribution:
        def read_text(self, name):
            assert name == "direct_url.json"
            return json.dumps(origin) if origin is not None else None

    distribution = Distribution()
    distribution.version = version
    monkeypatch.setattr(deploy.metadata, "distribution", lambda _: distribution)
    return installed


def _stub_install_runtime(tmp_path, monkeypatch):
    target = tmp_path / "target" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    target.parent.mkdir(parents=True)
    target.write_text("target runtime fixture")
    site = tmp_path / "target" / "site-packages"
    model = tmp_path / "embedding.gguf"
    model.write_bytes(b"GGUFmodel")
    calls = []
    monkeypatch.setattr(deploy.subprocess, "run", lambda command, **kwargs: calls.append(command))
    monkeypatch.setattr(deploy, "_versions", lambda _: deploy.PINS)
    monkeypatch.setattr(deploy, "_installed_source", lambda python, source: site)
    # The fake executable cannot compile SDKs; tests below separately exercise
    # actual target bytecode preparation with a tiny installed Lark fixture.
    monkeypatch.setattr(deploy, "_prepare_runtime", lambda _: {"fixture": True})
    monkeypatch.setattr(deploy, "_ensure_secret", lambda *_: "synthetic")
    return target, site, model, calls


@pytest.mark.parametrize("mode", ["source-fresh", "wheel-adopt", "same-runtime"])
def test_install_prepares_selected_runtime_before_credentials(tmp_path, monkeypatch, mode):
    target, site, model, _ = _stub_install_runtime(tmp_path, monkeypatch)
    home = tmp_path / "home"
    events = []
    monkeypatch.setattr(deploy, "_installed_source", lambda *_: events.append("verified") or site)
    monkeypatch.setattr(deploy, "_prepare_runtime", lambda python: events.append(("prepared", python)), raising=False)
    monkeypatch.setattr(deploy, "_ensure_secret", lambda *_: events.append("credential") or "synthetic")
    if mode == "source-fresh":
        expected = (home / "runtime" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")).resolve()
        deploy.install(home, model=str(model), fresh_runtime=True)
    elif mode == "wheel-adopt":
        expected = target.resolve()
        deploy.install(home, str(target), str(model), wheel=str(_wheel_fixture(tmp_path)))
    else:
        _wheel_launcher(tmp_path, monkeypatch)
        expected = Path(sys.executable).parent.resolve() / Path(sys.executable).name
        deploy.install(home, sys.executable, str(model))
    assert events[:3] == ["verified", ("prepared", expected), "credential"]


def test_preparation_failure_preserves_deployment_and_data_without_provisioning_credentials(tmp_path, monkeypatch):
    target, _, model, _ = _stub_install_runtime(tmp_path, monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    previous = json.dumps({"python": str(target), "model": str(model), "unchanged": True}).encode()
    (home / "deployment.json").write_bytes(previous)
    (home / "openviking.conf").write_bytes(b"existing-config")
    (home / "data").mkdir()
    (home / "data" / "original.md").write_bytes(b"existing-data")
    monkeypatch.setattr(deploy, "_prepare_runtime", lambda _: (_ for _ in ()).throw(RuntimeError("preparation-failed")), raising=False)
    monkeypatch.setattr(deploy, "_ensure_secret", lambda *_: pytest.fail("must prepare before credentials"))
    with pytest.raises(RuntimeError, match="preparation-failed"):
        deploy.install(home)
    assert (home / "deployment.json").read_bytes() == previous
    assert (home / "openviking.conf").read_bytes() == b"existing-config"
    assert (home / "data" / "original.md").read_bytes() == b"existing-data"


def _lark_fixture(site, *, invalid=False):
    root = site / "lark_oapi"
    root.mkdir(parents=True)
    # Compilation must not import/execute the SDK or use a warmed provider.
    (root / "__init__.py").write_text("raise RuntimeError('must-not-import-sdk')\n")
    (root / "generated.py").write_text("def invalid(: compiler-source-secret\n" if invalid else "VALUE = 'tiny-generated-fixture'\n")
    info = site / "lark_oapi-1.7.3.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: lark-oapi\nVersion: 1.7.3\n")
    (info / "RECORD").write_text("lark_oapi/__init__.py,,\nlark_oapi/generated.py,,\n")
    return root


def _preparation_venv(tmp_path):
    root = tmp_path / "runtime"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root)], check=True, timeout=60)
    python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    output = subprocess.run([str(python), "-B", "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"],
                            check=True, capture_output=True, text=True, timeout=30)
    return python.parent.resolve() / python.name, Path(output.stdout.strip())


@pytest.mark.parametrize("mode", ["explicit", "previous", "fresh"])
def test_actual_venv_selection_keeps_entry_prefix_and_site(tmp_path, mode):
    python, site = _preparation_venv(tmp_path)
    home = python.parent.parent.parent
    if mode == "previous":
        (home / "deployment.json").write_text(json.dumps({"python": str(python)}))
    _, selected = deploy._install_state(home, str(python) if mode == "explicit" else None, mode == "fresh")
    assert selected == python
    if os.name == "posix":
        assert python.is_symlink() and selected != python.resolve()
    output = subprocess.run([str(selected), "-B", "-c",
                             "import sys,sysconfig,json;print(json.dumps([sys.prefix,sysconfig.get_path('purelib')]))"],
                            check=True, capture_output=True, text=True, timeout=30)
    prefix, actual_site = json.loads(output.stdout)
    assert Path(prefix) == python.parent.parent and Path(actual_site) == site


def test_distinct_real_venvs_need_wheel_and_reject_other_interpreter_identity(tmp_path, monkeypatch):
    launcher, _ = _preparation_venv(tmp_path / "launcher")
    target, site = _preparation_venv(tmp_path / "target")
    assert launcher != target
    if os.name == "posix":
        assert launcher.is_symlink() and target.is_symlink() and launcher.resolve() == target.resolve()
    wheel = _wheel_fixture(tmp_path)
    _wheel_launcher(tmp_path, monkeypatch, {"url": wheel.as_uri(), "archive_info": {
        "hashes": {"sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}}})
    monkeypatch.setattr(sys, "executable", str(launcher))
    _, selected = deploy._install_state(tmp_path / "home", str(target), False)
    assert deploy._package_input(selected, None) == (None, wheel.resolve())
    assert deploy._package_input(launcher, None) == (None, None)
    imported = {"python": str(selected), "file": str(site / "memory_base/__init__.py"),
                "version": deploy.__version__, "distribution_version": deploy.__version__,
                "site": str(site), "distribution_root": str(site)}
    monkeypatch.setattr(deploy.subprocess, "run", lambda *_args, **_kwargs:
                        subprocess.CompletedProcess([], 0, json.dumps(imported), ""))
    assert deploy._installed_source(selected, None) == site.resolve()
    imported["python"] = str(launcher)
    with pytest.raises(RuntimeError, match="Target runtime did not import"):
        deploy._installed_source(selected, None)


def test_actual_target_preparation_compiles_only_own_lark_and_keeps_valid_caches(tmp_path, monkeypatch):
    python, site = _preparation_venv(tmp_path)
    root = _lark_fixture(site)
    other = site / "unused_cloud_provider"
    other.mkdir()
    (other / "__init__.py").write_text("VALUE = 1\n")
    sources = {path: path.read_bytes() for path in root.glob("*.py")}
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "untrusted-provider"))
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "outside-cache"))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("PYTHONOPTIMIZE", "2")
    first = deploy._prepare_runtime(python)
    assert first["origin"] == str(root.resolve()) and first["force"] is False
    assert first["pycache_prefix"] is None and first["interpreter_optimize"] == 0
    assert first["cache_tag"] == sys.implementation.cache_tag
    assert os.environ["PYTHONPYCACHEPREFIX"] == str(tmp_path / "outside-cache")
    assert os.environ["PYTHONOPTIMIZE"] == "2"
    caches = {path: path.stat().st_mtime_ns for path in root.rglob("*.pyc")}
    assert len(caches) == 2
    assert deploy._prepare_runtime(python)["origin"] == first["origin"]
    assert {path: path.stat().st_mtime_ns for path in root.rglob("*.pyc")} == caches
    assert all(path.read_bytes() == content for path, content in sources.items())
    assert not (other / "__pycache__").exists() and not (tmp_path / "outside-cache").exists()


def test_preparation_rejects_inherited_provider_even_with_python_optimization(tmp_path, monkeypatch):
    python, site = _preparation_venv(tmp_path)
    provider = tmp_path / "readonly-provider"
    root = _lark_fixture(provider)
    (site / "test-provider.pth").write_text(str(provider) + "\n")
    monkeypatch.setenv("PYTHONOPTIMIZE", "2")
    with pytest.raises(RuntimeError, match="inherited SDK providers"):
        deploy._prepare_runtime(python)
    assert not (root / "__pycache__").exists()


def test_actual_preparation_failure_is_explicit_without_exposing_compiler_source(tmp_path):
    python, site = _preparation_venv(tmp_path)
    _lark_fixture(site, invalid=True)
    with pytest.raises(RuntimeError, match="Cold-start preparation") as error:
        deploy._prepare_runtime(python)
    assert "compiler-source-secret" not in str(error.value)


def test_preparation_uses_selected_python_and_separate_budget_without_echoing_failed_output(tmp_path, monkeypatch):
    python = Path(sys.executable).parent.resolve() / Path(sys.executable).name
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", "do-not-use-cache")

    def failed(command, **kwargs):
        assert command[:3] == [str(python), "-B", "-c"] and command[-1] == str(python)
        assert kwargs["cwd"] == str(python.parent) and kwargs["timeout"] == 600
        assert "PYTHONPYCACHEPREFIX" not in kwargs["env"] and "PYTHONPATH" not in kwargs["env"]
        raise subprocess.TimeoutExpired(command, 600, output="private-source", stderr="private-secret")

    monkeypatch.setattr(deploy.subprocess, "run", failed)
    with pytest.raises(RuntimeError, match="Cold-start preparation") as error:
        deploy._prepare_runtime(python)
    assert "private-source" not in str(error.value) and "private-secret" not in str(error.value)


def test_upstream_config_uses_external_embedder_and_no_secret(tmp_path):
    config = {"ports": {"embed": 17996, "upstream": 17886}}
    path = deploy.write_upstream_config(tmp_path, config)
    raw = path.read_text(encoding="utf-8")
    value = json.loads(raw)
    assert value["embedding"]["dense"]["provider"] == "openai"
    assert value["embedding"]["dense"]["api_base"] == "http://127.0.0.1:17996/v1"
    assert value["server"]["auth_mode"] == "api_key"
    assert value["server"]["root_api_key"] == "$MEMORY_BASE_ROOT_KEY"
    assert value["encryption"]["api_key_hashing"]["enabled"] is True
    assert "root-secret" not in raw
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_fresh_runtime_switch_preserves_deployment_home_and_existing_model(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    runtime.parent.mkdir(parents=True)
    runtime.write_text("test runtime")
    model = tmp_path / "embedding.gguf"
    model.write_bytes(b"GGUFmodel")
    (tmp_path / "deployment.json").write_text(json.dumps({"python": "/old/experimental/python", "model": str(model)}))
    monkeypatch.setattr(deploy, "_versions", lambda _: deploy.PINS)
    calls = []
    monkeypatch.setattr(deploy.subprocess, "run", lambda command, **kwargs: calls.append(command))
    monkeypatch.setattr(deploy, "_ensure_secret", lambda *_: "existing")
    monkeypatch.setattr(deploy, "_installed_source", lambda python, source: source.parent)
    prepared = []
    monkeypatch.setattr(deploy, "_prepare_runtime", lambda python: prepared.append(python))
    config = deploy.install(tmp_path, fresh_runtime=True)
    assert config["python"] == str(runtime.resolve())
    assert config["model"] == str(model.resolve())
    assert calls[-1] == [str(runtime.resolve()), "-m", "pip", "install", "--no-deps", "-e", str(PACKAGE_SOURCE)]
    assert config["source"] == str(PACKAGE_SOURCE.parent)
    assert prepared == [runtime.resolve()]
    assert json.loads((tmp_path / "openviking.conf").read_text())["storage"]["workspace"] == str(tmp_path / "data")


@pytest.mark.parametrize("fresh", [False, True], ids=["default", "fresh-runtime"])
def test_wheel_without_artifact_rejects_before_install_side_effects(tmp_path, monkeypatch, fresh):
    _wheel_launcher(tmp_path, monkeypatch)
    monkeypatch.setattr(deploy, "_versions", lambda _: deploy.PINS)
    calls = []
    monkeypatch.setattr(deploy.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(deploy, "_ensure_secret", lambda *_: pytest.fail("must reject before credentials"))
    home = tmp_path / "new-home"
    with pytest.raises(RuntimeError, match="--wheel"):
        deploy.install(home, fresh_runtime=fresh)
    assert calls == []
    assert not home.exists()


def test_explicit_wheel_installs_package_into_target_and_records_target_source(tmp_path, monkeypatch):
    wheel = _wheel_fixture(tmp_path)
    target, site, model, calls = _stub_install_runtime(tmp_path, monkeypatch)
    config = deploy.install(tmp_path / "home", str(target), str(model), wheel=str(wheel))
    assert calls[-1] == [str(target.resolve()), "-m", "pip", "install", "--no-deps", "--ignore-installed", str(wheel.resolve())]
    assert all("-e" not in command for command in calls)
    assert config["source"] == str(site)
    assert config["python"] == str(target.resolve())


@pytest.mark.parametrize("hash_field", ["hashes", "hash"], ids=["current-pep610", "legacy-pep610"])
def test_default_wheel_reuses_verified_local_origin(tmp_path, monkeypatch, hash_field):
    wheel = _wheel_fixture(tmp_path)
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    archive = {"hashes": {"sha256": digest}} if hash_field == "hashes" else {"hash": "sha256=" + digest}
    _wheel_launcher(tmp_path, monkeypatch, {"url": wheel.as_uri(), "archive_info": archive})
    target, site, model, calls = _stub_install_runtime(tmp_path, monkeypatch)
    config = deploy.install(tmp_path / "home", str(target), str(model))
    assert calls[-1][-1] == str(wheel.resolve())
    assert config["source"] == str(site)


@pytest.mark.parametrize("problem", ["missing", "wrong-product", "wrong-version", "bad-zip", "bad-record", "incomplete"])
def test_invalid_explicit_wheel_is_rejected_before_subprocesses(tmp_path, monkeypatch, problem):
    wheel = _wheel_fixture(tmp_path, product="other-product" if problem == "wrong-product" else "agent-memory-base",
                           version="0.2.0" if problem == "wrong-version" else "0.1.0",
                           corrupt=problem == "bad-record", missing="memory_base/mcp_stdio.py" if problem == "incomplete" else None)
    if problem == "missing":
        wheel = tmp_path / "missing.whl"
    elif problem == "bad-zip":
        wheel.write_bytes(b"not a zip")
    monkeypatch.setattr(deploy.subprocess, "run", lambda *_a, **_k: pytest.fail("must reject before subprocesses"))
    home = tmp_path / "home"
    with pytest.raises(RuntimeError, match="--wheel"):
        deploy.install(home, wheel=str(wheel))
    assert not home.exists()


@pytest.mark.parametrize("problem", ["missing-file", "hash-mismatch", "remote", "no-hash", "credential-url"])
def test_invalid_pep610_origin_fails_before_install_side_effects(tmp_path, monkeypatch, problem):
    wheel = _wheel_fixture(tmp_path)
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    url = (tmp_path / "missing.whl").as_uri() if problem == "missing-file" else wheel.as_uri()
    if problem == "remote":
        url = "https://example.invalid/product.whl"
    elif problem == "credential-url":
        url = "file://user:do-not-display@localhost/product.whl"
    archive = {"hashes": {"sha256": "0" * 64 if problem == "hash-mismatch" else digest}}
    if problem == "no-hash":
        archive = {}
    _wheel_launcher(tmp_path, monkeypatch, {"url": url, "archive_info": archive})
    monkeypatch.setattr(deploy.subprocess, "run", lambda *_a, **_k: pytest.fail("must reject before subprocesses"))
    home = tmp_path / "home"
    with pytest.raises(RuntimeError, match="--wheel") as error:
        deploy.install(home)
    assert "do-not-display" not in str(error.value)
    assert not home.exists()


def test_same_installed_runtime_does_not_need_original_wheel(tmp_path, monkeypatch):
    _wheel_launcher(tmp_path, monkeypatch)
    _, _, model, calls = _stub_install_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(deploy, "_local_wheel_origin", lambda: pytest.fail("current runtime already has package"))
    config = deploy.install(tmp_path / "home", sys.executable, str(model))
    assert len(calls) == 1 and calls[0][1:5] == ["-m", "pip", "install", "--no-compile"]
    assert config["python"] == str(Path(sys.executable).parent.resolve() / Path(sys.executable).name)


def test_target_import_verification_rejects_launchers_package_and_clears_pythonpath(tmp_path, monkeypatch):
    python = Path(sys.executable).parent.resolve() / Path(sys.executable).name
    site = tmp_path / "target-site"
    payload = {"python": str(python), "file": str(tmp_path / "launcher" / "memory_base" / "__init__.py"),
               "site": str(site), "version": "0.1.0", "distribution_version": "0.1.0", "distribution_root": str(site)}
    monkeypatch.setenv("PYTHONPATH", "untrusted-caller-source")

    def query(command, **kwargs):
        assert "PYTHONPATH" not in kwargs["env"]
        assert kwargs["cwd"] == str(python.parent)
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(deploy.subprocess, "run", query)
    with pytest.raises(RuntimeError, match="Target runtime"):
        deploy._installed_source(python, None)
    payload["file"] = str(site / "memory_base" / "__init__.py")
    assert deploy._installed_source(python, None) == site.resolve()


def test_cli_wheel_preflight_precedes_lock_and_requires_install_command(tmp_path, monkeypatch, capsys):
    _wheel_launcher(tmp_path, monkeypatch)
    monkeypatch.setattr(deploy, "lifecycle_lock", lambda _: pytest.fail("must reject before lock/home creation"))
    assert deploy.main(["install", "--home", str(tmp_path / "home")]) == 1
    assert "--wheel" in capsys.readouterr().err
    assert not (tmp_path / "home").exists()
    with pytest.raises(SystemExit) as error:
        deploy.main(["status", "--wheel", "unused.whl"])
    assert error.value.code == 2


def test_cli_forwards_wheel_and_fresh_runtime(tmp_path, monkeypatch):
    wheel = _wheel_fixture(tmp_path)
    with pytest.raises(ValueError, match="either"):
        deploy.install(tmp_path / "other", sys.executable, fresh_runtime=True, wheel=str(wheel))
    captured = []
    monkeypatch.setattr(deploy, "install", lambda *args, **kwargs: captured.append((args, kwargs)))
    assert deploy.main(["install", "--home", str(tmp_path / "home"), "--fresh-runtime", "--wheel", str(wheel)]) == 0
    assert captured == [((tmp_path / "home", None, None), {"fresh_runtime": True, "wheel": str(wheel)})]


@pytest.mark.skipif(sys.version_info[:2] != (3, 12), reason="Product wheel requires Python 3.12")
@pytest.mark.parametrize("mode", ["automatic-default", "explicit-fresh"])
def test_real_wheel_bootstraps_independent_target_runtime(tmp_path, mode):
    """Real build and package pip installs; dependency/credential stages are fixtures."""
    source = tmp_path / "build-source"
    shutil.copytree(PACKAGE_SOURCE, source, ignore=shutil.ignore_patterns("__pycache__", "build", "*.egg-info"))
    env = {key: value for key, value in deploy.clean_env().items()
           if key.upper() not in {"PYTHONPATH", "PYTHONHOME"} and not key.upper().startswith("MEMORY_")}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
    commands = []

    def run(command, cwd=tmp_path):
        command = [str(item) for item in command]
        commands.append(command)
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                                text=True, encoding="utf-8", timeout=90)
        assert result.returncode == 0, (command, result.stdout, result.stderr)
        return result.stdout

    wheels = tmp_path / "wheels"
    run([sys.executable, "-m", "pip", "wheel", source, "--no-deps", "--no-build-isolation", "--no-index", "--wheel-dir", wheels])
    wheel, = wheels.glob("*.whl")
    dependency_site = sysconfig.get_path("purelib")

    def venv(root):
        # Reuse only installed build/pip dependencies. Do not process the caller's
        # editable .pth files, and keep pip writes inside each new venv.
        run([sys.executable, "-m", "venv", "--without-pip", "--system-site-packages", root])
        python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        site = Path(run([python, "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"]).strip())
        (site / "test-dependency-provider.pth").write_text(dependency_site + "\n", encoding="utf-8")
        return python, site

    launcher, launcher_site = venv(tmp_path / "launcher")
    home = tmp_path / "deployment"
    target, target_site = venv(home / "runtime")
    assert not (target_site / "memory_base").exists()
    run([launcher, "-m", "pip", "install", "--no-deps", "--no-index", "--ignore-installed", wheel])
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUFsynthetic-header-fixture")
    controller = textwrap.dedent("""
        import json, subprocess, sys
        from pathlib import Path
        from memory_base import deploy
        original = subprocess.run
        calls = []
        def staged_run(command, **kwargs):
            calls.append([str(item) for item in command])
            if command[1:5] == ['-m', 'pip', 'install', '--no-compile']:
                return subprocess.CompletedProcess(command, 0, '', '')
            return original(command, **kwargs)
        deploy.subprocess.run = staged_run
        deploy._versions = lambda python: dict(deploy.PINS)
        deploy._prepare_runtime = lambda python: {'fixture': 'Upstream SDK preparation is not measured here'}
        deploy._ensure_secret = lambda *args: 'synthetic-not-persisted'
        home, model, mode, wheel = sys.argv[1:]
        argv = ['install', '--home', home, '--model', model]
        if mode == 'explicit-fresh':
            argv += ['--fresh-runtime', '--wheel', wheel]
        assert deploy.main(argv) == 0
        config = json.loads((Path(home)/'deployment.json').read_text())
        print('WHEEL_PROOF=' + json.dumps({'config':config,'calls':calls,'launcher_origin':deploy.__file__}))
    """)
    stdout = run([launcher, "-c", controller, home, model, mode, wheel])
    proof = json.loads(next(line.removeprefix("WHEEL_PROOF=") for line in stdout.splitlines() if line.startswith("WHEEL_PROOF=")))
    assert Path(proof["launcher_origin"]) == launcher_site / "memory_base" / "deploy.py"
    assert proof["config"]["source"] == str(target_site)
    target_entry = target.parent.resolve() / target.name
    assert proof["config"]["python"] == str(target_entry)
    assert [str(target_entry), "-m", "pip", "install", "--no-deps", "--ignore-installed", str(wheel.resolve())] in proof["calls"]
    origin = run([target, "-c", "import memory_base;print(memory_base.__file__)"]).strip()
    assert Path(origin) == target_site / "memory_base" / "__init__.py"
    assert "usage:" in run([target, "-m", "memory_base", "--help"])
    assert "usage:" in run([home / "runtime" / ("Scripts/memory-base.exe" if os.name == "nt" else "bin/memory-base"), "--help"])
    proof.update({"mode": mode, "target_origin": origin, "wheel": str(wheel),
                  "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(), "commands": commands,
                  "dependency_provider": dependency_site,
                  "boundary": "Real wheel build/package pip installs. Upstream dependency install/version checks, SDK cold-start preparation and vault writes are fixtures; no clean dependency-stack installation or cold startup is claimed."})
    (tmp_path / "real-wheel-proof.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"mode": mode, "proof": str(tmp_path / "real-wheel-proof.json"), "target_origin": origin,
                      "wheel_sha256": proof["wheel_sha256"]}))


def test_secrets_are_env_override_and_home_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv(credentials.env_name("host-codex"), "process-secret")
    assert credentials.get_secret("host-codex", tmp_path) == "process-secret"
    assert credentials._target("host-codex", tmp_path) != credentials._target("host-codex", tmp_path / "other")


def test_non_windows_never_writes_plaintext_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials.os, "name", "posix")
    with pytest.raises(RuntimeError, match="MEMORY_BASE_SECRET_HOST_CODEX"):
        credentials.set_secret("host-codex", "secret", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_spawn_refuses_unowned_healthy_service(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "owned_process", lambda _: False)
    monkeypatch.setattr(deploy, "_port_busy", lambda _: True)
    monkeypatch.setattr(deploy, "_health", lambda *_: {"status": "ok"})
    monkeypatch.setattr(deploy.subprocess, "Popen", lambda *_a, **_k: pytest.fail("must not spawn"))
    with pytest.raises(RuntimeError, match="unowned service"):
        deploy._spawn("upstream", tmp_path, {"ports": {"upstream": 17886}}, {}, 1)


def test_stop_refuses_reused_pid_without_killing(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "load_config", lambda _: {"ports": {"upstream": 17886}})
    monkeypatch.setattr(deploy, "_records", lambda _: {"upstream": {"pid": 42, "port": 17886}})
    monkeypatch.setattr(deploy, "owned_process", lambda _: False)
    monkeypatch.setattr(deploy, "_port_busy", lambda _: True)
    monkeypatch.setattr(deploy.subprocess, "run", lambda *_a, **_k: pytest.fail("must not kill"))
    with pytest.raises(RuntimeError, match="ownership no longer matches"):
        deploy.stop(tmp_path, ("upstream",))


def test_ownership_binds_pid_creation_and_exact_command(monkeypatch):
    record = {"pid": 42, "created": "first", "command": "owned-command"}
    monkeypatch.setattr(deploy, "_process_info", lambda _: {"created": "later", "command": "owned-command"})
    assert not deploy.owned_process(record)
    monkeypatch.setattr(deploy, "_process_info", lambda _: {"created": "first", "command": "different"})
    assert not deploy.owned_process(record)
    monkeypatch.setattr(deploy, "_process_info", lambda _: {"created": "first", "command": "owned-command"})
    assert deploy.owned_process(record)


def _command_text(command):
    return subprocess.list2cmdline(command) if os.name == "nt" else " ".join(command)


def test_component_ownership_binds_real_command_to_home_with_shared_runtime(tmp_path, monkeypatch):
    left, right = tmp_path / "left", tmp_path / "right"
    config = {"python": sys.executable, "ports": {"gateway": 17888}}
    command = _command_text(deploy._component_command("gateway", left, config))
    record = {"pid": 42, "created": "now", "command": command, "port": 17888, "home": str(right)}
    monkeypatch.setattr(deploy, "_process_info", lambda _: {"created": "now", "command": command})
    assert deploy.owned_component("gateway", left, config, record)
    assert not deploy.owned_component("gateway", right, config, record)
    record["command"] += " --other"
    assert not deploy.owned_component("gateway", left, config, record)


@pytest.mark.parametrize("component", deploy.COMPONENTS)
def test_previous_home_wrapper_remains_owned_only_by_exact_deployment(tmp_path, monkeypatch, component):
    previous_wrapper = ("import os,runpy,sys;os.environ['MEMORY_BASE_HOME']=sys.argv[1];"
                        "module=sys.argv[2];sys.argv=[module,*sys.argv[3:]];"
                        "runpy.run_module(module,run_name='__main__')")
    config = {"python": sys.executable, "ports": {"embed": 17996, "upstream": 17886, "gateway": 17888},
              "model": "/test/model.gguf"}
    arguments = deploy._component_command(component, tmp_path, config)
    arguments[2] = previous_wrapper
    command = _command_text(arguments)
    record = {"pid": 42, "created": "now", "command": command, "port": config["ports"][component]}
    monkeypatch.setattr(deploy, "_process_info", lambda _: {"created": "now", "command": record["command"]})
    assert deploy.owned_component(component, tmp_path, config, record)
    assert not deploy.owned_component(component, tmp_path / "other", config, record)
    record["command"] += " --other"
    assert not deploy.owned_component(component, tmp_path, config, record)


def test_legacy_component_ownership_requires_home_local_runtime(tmp_path, monkeypatch):
    config = {"python": str(tmp_path / "runtime" / "python"), "ports": {"gateway": 17888}}
    command = _command_text(deploy._component_command("gateway", tmp_path, config, legacy=True))
    record = {"pid": 42, "created": "now", "command": command, "port": 17888}
    monkeypatch.setattr(deploy, "_process_info", lambda _: {"created": "now", "command": record["command"]})
    assert deploy.owned_component("gateway", tmp_path, config, record)
    config["python"] = sys.executable
    record["command"] = _command_text(deploy._component_command("gateway", tmp_path, config, legacy=True))
    assert not deploy.owned_component("gateway", tmp_path, config, record)


def test_component_wrapper_sets_home_and_preserves_module_arguments(tmp_path):
    package = tmp_path / "memory_base"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "embed.py").write_text("import json,os,sys;print(json.dumps([os.environ['MEMORY_BASE_HOME'],sys.argv]))")
    config = {"python": sys.executable, "ports": {"embed": 17996}, "model": "/test/model.gguf"}
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    result = subprocess.run(deploy._component_command("embed", tmp_path, config), cwd=tmp_path,
                            env=env, capture_output=True, text=True, check=True)
    home, arguments = json.loads(result.stdout)
    assert home == str(tmp_path)
    assert arguments[1:] == ["--port", "17996", "--model", "/test/model.gguf"]


def test_actual_spawn_uses_prepared_cache_policy_without_changing_user_environment(tmp_path, monkeypatch):
    package = tmp_path / "memory_base"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "embed.py").write_text(textwrap.dedent("""
        import json, os, sys
        from http.server import BaseHTTPRequestHandler, HTTPServer
        state = {'service':'memory-base-embedding', 'pid':os.getpid(),
                 'pycache_prefix':sys.pycache_prefix, 'optimize':sys.flags.optimize,
                 'cache_tag':sys.implementation.cache_tag,
                 'flags_present':any(name in os.environ for name in ('PYTHONPYCACHEPREFIX','PYTHONOPTIMIZE'))}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers()
                self.wfile.write(json.dumps(state).encode())
            def log_message(self, *args): pass
        HTTPServer(('127.0.0.1',int(sys.argv[sys.argv.index('--port')+1])), Handler).serve_forever()
    """))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    config = {"python": sys.executable, "source": str(tmp_path), "ports": {"embed": port}, "model": "unused-fixture.gguf"}
    deploy._write_json(tmp_path / "deployment.json", config)
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "user-cache"))
    monkeypatch.setenv("PYTHONOPTIMIZE", "2")
    records = {}
    try:
        deploy._spawn("embed", tmp_path, config, records, 30)
        state = deploy._health("embed", config)
        assert state and state["pycache_prefix"] is None and state["optimize"] == 0
        assert state["cache_tag"] == sys.implementation.cache_tag and not state["flags_present"]
        assert os.environ["PYTHONPYCACHEPREFIX"] == str(tmp_path / "user-cache")
        assert os.environ["PYTHONOPTIMIZE"] == "2"
        assert not (tmp_path / "user-cache").exists()
    finally:
        deploy.stop(tmp_path, components=("embed",))
        assert not deploy._records(tmp_path) and not deploy._port_busy(port)


def _gateway_body_probe(tmp_path, python, *, legacy=False):
    # Replace only the server loop: execute the real gateway module and routes.
    # No port is opened and the parsed request is handled without an upstream.
    (tmp_path / "uvicorn.py").write_text(textwrap.dedent("""
        import json, os
        from fastapi.testclient import TestClient

        def run(app, **kwargs):
            app.state.capture_service.add = lambda request, identity: {
                "request": request.model_dump(), "identity": identity.public()}
            body = {"request_id": "bootstrap-test", "title": "Original title",
                    "content": "  Original content\\r\\n"}
            headers = {"Authorization": "Bearer test-token"}
            with TestClient(app, raise_server_exceptions=False) as client:
                valid = client.post("/api/memory/add", json=body, headers=headers)
                invalid = client.post("/api/memory/add", json={**body, "content": []}, headers=headers)
            result = {"status": valid.status_code,
                      "body": valid.json() if valid.status_code == 200 else valid.text,
                      "invalid_status": invalid.status_code,
                      "home": os.environ["MEMORY_BASE_HOME"]}
            if valid.status_code == 500:
                try:
                    with TestClient(app) as client:
                        client.post("/api/memory/add", json=body, headers=headers)
                except Exception as error:
                    result["error"] = str(error)
            print(json.dumps(result))
    """), encoding="utf-8")
    env = deploy.clean_env({
        "PYTHONPATH": os.pathsep.join([str(tmp_path), str(Path(deploy.__file__).resolve().parents[1])]),
        "MEMORY_BASE_HOME": str(tmp_path), "MEMORY_BASE_OV_KEY": "unused-test-key",
        "MEMORY_BASE_TOKENS": json.dumps({"test-token": {"principal": "test", "role": "writer"}}),
    })
    config = {"python": str(python), "ports": {"gateway": 17888}}
    result = subprocess.run(deploy._component_command("gateway", tmp_path, config, legacy=legacy),
                            cwd=tmp_path, env=env, capture_output=True, text=True, check=True, timeout=30)
    return json.loads(result.stdout)


@pytest.mark.parametrize("legacy", [False, True], ids=["home-wrapper", "legacy-module"])
def test_gateway_component_subprocess_validates_request_body(tmp_path, legacy):
    result = _gateway_body_probe(tmp_path, sys.executable, legacy=legacy)
    assert result["status"] == 200, result
    assert result["body"]["request"]["content"] == "  Original content\r\n"
    assert result["body"]["identity"] == {"principal": "test", "role": "writer"}
    assert result["invalid_status"] == 422
    assert result["home"] == str(tmp_path)


def test_upstream_health_rejects_dev_mode_and_wrong_version(monkeypatch):
    config = {"ports": {"upstream": 17886}}
    payload = {"healthy": True, "version": "0.4.23", "auth_mode": "dev"}
    monkeypatch.setattr(deploy, "_request", lambda *_a, **_k: (200, payload))
    assert deploy._health("upstream", config) is None
    payload["auth_mode"] = "api_key"
    payload["version"] = "0.5.0"
    assert deploy._health("upstream", config) is None
    payload["version"] = "0.4.23"
    assert deploy._health("upstream", config) == payload


def test_gateway_env_has_separate_host_identities_and_never_root_key(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "get_secret", lambda name, _: "upstream-secret" if name == "upstream-key" else None)
    monkeypatch.setattr(deploy, "_ensure_secret", lambda name, _: "token-" + name)
    env = deploy.gateway_env(tmp_path, {"ports": {"upstream": 17886, "gateway": 17888}})
    identities = json.loads(env["MEMORY_BASE_TOKENS"])
    assert {entry["principal"] for entry in identities.values()} == set(deploy.PROFILES)
    assert identities["token-host-reader"]["role"] == "reader"
    assert identities["token-host-agent-bubble"]["role"] == "writer"
    assert env["MEMORY_BASE_OV_ACCOUNT"] == "personal"
    assert env["MEMORY_BASE_OV_USER"] == "gateway"
    assert deploy.ROOT_KEY_ENV not in env


def test_gateway_env_uses_narrow_or_denied_persisted_host_scope_rules(tmp_path, monkeypatch):
    deploy._write_json(tmp_path / "access.json", {"schema": 1, "profiles": {
        "codex": {"read_scopes": ["projects/kb/docs"], "write_scopes": []},
        "reader": {"read_scopes": []}, "agent-bubble": {"write_scopes": ["captures/2026-10-11"]}}})
    monkeypatch.setattr(deploy, "get_secret", lambda *_: "upstream-key")
    monkeypatch.setattr(deploy, "_ensure_secret", lambda name, _: name)
    env = deploy.gateway_env(tmp_path, {"ports": {"upstream": 17886, "gateway": 17888}})
    identities = json.loads(env["MEMORY_BASE_TOKENS"])
    assert identities["host-codex"]["read_scopes"] == ["projects/kb/docs"]
    assert identities["host-codex"]["write_scopes"] == []
    assert identities["host-reader"]["read_scopes"] == []
    assert identities["host-agent-bubble"]["write_scopes"] == ["captures/2026-10-11"]
    assert "read_scopes" not in identities["host-dsh"]


def test_gateway_env_rejects_bad_scope_policy_before_reading_any_secret(tmp_path, monkeypatch):
    deploy._write_json(tmp_path / "access.json", {"schema": True, "profiles": {}})
    monkeypatch.setattr(deploy, "get_secret", lambda *_: pytest.fail("must validate before credentials"))
    monkeypatch.setattr(deploy, "_ensure_secret", lambda *_: pytest.fail("must validate before credentials"))
    with pytest.raises(ValueError):
        deploy.gateway_env(tmp_path, {"ports": {"upstream": 17886, "gateway": 17888}})


def test_log_output_redacts_every_registered_credential(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "get_secret", lambda name, _: "secret-" + name)
    assert deploy.redact("root=secret-root-key host=secret-host-dsh", tmp_path) == "root=[REDACTED] host=[REDACTED]"


def test_restart_reuses_upstream_identity_and_enables_acl(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(deploy, "_ensure_secret", lambda *_: "root")
    monkeypatch.setattr(deploy, "get_secret", lambda *_: "existing-upstream")
    monkeypatch.setattr(deploy, "set_secret", lambda *_: pytest.fail("existing key must not be replaced"))
    monkeypatch.setattr(deploy, "_api", lambda config, path, *args, **kwargs:
                        [{"account_id": "personal"}] if path == "/api/v1/admin/accounts" else
                        calls.append((path, args, kwargs)) or {})
    deploy.ensure_identity(tmp_path, {"ports": {"upstream": 17886}})
    assert len(calls) == 2
    assert calls[0][0].endswith("/settings")
    assert calls[0][1] == ("PATCH", {"acl": {"enabled": True}})
    assert calls[1][2]["key"] == "existing-upstream"


def test_restored_identity_is_reissued_even_when_target_vault_has_old_key(tmp_path, monkeypatch):
    calls, secrets = [], []
    deploy._write_json(tmp_path / "restore-state.json", {"identity_reissue_pending": True, "manifest_sha256": "a" * 64})
    monkeypatch.setattr(deploy, "_ensure_secret", lambda *_: "target-root")
    monkeypatch.setattr(deploy, "get_secret", lambda *_: "stale-target-key")
    monkeypatch.setattr(deploy, "set_secret", lambda *args: secrets.append(args))

    def api(config, path, *args, **kwargs):
        calls.append((path, args, kwargs))
        if path == "/api/v1/admin/accounts":
            return [{"account_id": "personal"}]
        if path.endswith("/key"):
            return {"user_key": "fresh-restored-key"}
        return {}

    monkeypatch.setattr(deploy, "_api", api)
    deploy.ensure_identity(tmp_path, {"ports": {"upstream": 17886}})
    assert secrets == [("upstream-key", "fresh-restored-key", tmp_path)]
    assert calls[-1][2]["key"] == "fresh-restored-key"
    assert json.loads((tmp_path / "restore-state.json").read_text())["identity_reissue_pending"] is False


def test_restored_identity_failure_keeps_reissue_marker(tmp_path, monkeypatch):
    deploy._write_json(tmp_path / "restore-state.json", {"identity_reissue_pending": True})
    monkeypatch.setattr(deploy, "_ensure_secret", lambda *_: "root")
    monkeypatch.setattr(deploy, "get_secret", lambda *_: "stale-key")
    monkeypatch.setattr(deploy, "_api", lambda *_a, **_k: [{"account_id": "personal"}] if _a[1].endswith("/accounts")
                        else (_ for _ in ()).throw(RuntimeError("regeneration failed")))
    with pytest.raises(RuntimeError, match="regeneration failed"):
        deploy.ensure_identity(tmp_path, {"ports": {"upstream": 17886}})
    assert json.loads((tmp_path / "restore-state.json").read_text())["identity_reissue_pending"] is True


def _installed_home(tmp_path):
    home = tmp_path / "home"
    deploy._write_json(home / "deployment.json", {"schema": 1})
    return home


def test_source_cli_registration_is_atomic_idempotent_and_never_writes_project(tmp_path):
    home = _installed_home(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    original = source / "README.md"
    original.write_bytes("权威原文\n".encode("utf-8"))
    before = original.stat()
    result = deploy.source_add(home, "kb", str(source))
    assert result["status"] == "registered"
    assert result["project"]["include"] == ["README.md", "docs/**/*.md"]
    registered = (home / "sources.json").read_bytes()
    assert deploy.source_add(home, "kb", str(source))["status"] == "unchanged"
    assert (home / "sources.json").read_bytes() == registered
    assert deploy.source_list(home)["projects"][0]["availability"] == "reachable"
    assert original.read_bytes() == "权威原文\n".encode("utf-8")
    assert original.stat().st_mtime_ns == before.st_mtime_ns
    assert [p.name for p in source.iterdir()] == ["README.md"]
    assert not (home / "sources.json.tmp").exists()


def test_source_replace_requires_explicit_switch_and_preserves_custom_patterns(tmp_path):
    home = _installed_home(tmp_path)
    source, second = tmp_path / "source", tmp_path / "second"
    source.mkdir()
    second.mkdir()
    deploy.source_add(home, "kb", str(source), include=["notes/**/*.md"], exclude=[])
    previous = (home / "sources.json").read_bytes()
    with pytest.raises(ValueError, match="--replace"):
        deploy.source_add(home, "kb", str(second))
    assert (home / "sources.json").read_bytes() == previous
    assert deploy.source_add(home, "kb", str(second), replace=True)["status"] == "replaced"
    project = deploy.source_list(home)["projects"][0]
    assert project["include"] == ["notes/**/*.md"] and project["exclude"] == []


def test_source_remove_changes_only_registry_and_missing_root_is_visible(tmp_path):
    home = _installed_home(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    deploy.source_add(home, "kb", str(source))
    source.rmdir()
    assert deploy.source_list(home)["projects"][0]["availability"] == "unavailable"
    assert deploy.source_remove(home, "kb")["status"] == "unregistered"
    assert deploy.source_remove(home, "kb")["status"] == "unchanged"
    assert deploy.source_list(home)["projects"] == []


def test_source_add_refuses_unavailable_or_home_overlapping_root(tmp_path):
    home = _installed_home(tmp_path)
    with pytest.raises(ValueError, match="existing readable"):
        deploy.source_add(home, "kb", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="independent"):
        deploy.source_add(home, "kb", str(home))
    assert not (home / "sources.json").exists()


def test_source_registry_invalid_does_not_get_overwritten(tmp_path):
    home = _installed_home(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (home / "sources.json").write_text('{"schema":1,"projects":[],"unknown":"do not erase"}')
    previous = (home / "sources.json").read_bytes()
    with pytest.raises(ValueError):
        deploy.source_add(home, "kb", str(source))
    assert (home / "sources.json").read_bytes() == previous


def test_access_set_preserves_other_side_and_profile_and_normalizes_scopes(tmp_path):
    home = _installed_home(tmp_path)
    deploy.access_set(home, "codex", read_scopes=["root", "projects/kb", "root"], write_scopes=[])
    deploy.access_set(home, "reader", read_scopes=["projects/kb/docs"])
    result = deploy.access_set(home, "codex", read_scopes=[])
    assert result["gateway_restart_required"] is True
    access = json.loads((home / "access.json").read_text())
    assert access["profiles"]["codex"] == {"read_scopes": [], "write_scopes": []}
    assert access["profiles"]["reader"] == {"read_scopes": ["projects/kb/docs"]}
    assert deploy.access_set(home, "codex", read_scopes=[])["status"] == "unchanged"
    assert not (home / "access.json.tmp").exists()


def test_access_set_rejects_unknown_profile_or_ambiguous_scope_without_mutation(tmp_path):
    home = _installed_home(tmp_path)
    for profile, scopes in (("unknown", ["root"]), ("codex", ["projects/../escape"]), ("codex", None)):
        with pytest.raises(ValueError):
            deploy.access_set(home, profile, read_scopes=scopes)
    assert not (home / "access.json").exists()


def test_access_cli_requires_profile_and_rejects_conflicting_grant_deny(tmp_path):
    for argv in (["access-set", "--deny-read"],
                 ["access-set", "--profile", "codex"],
                 ["access-set", "--profile", "codex", "--read-scope", "root", "--deny-read"]):
        with pytest.raises(SystemExit) as error:
            deploy.main(argv)
        assert error.value.code == 2


def test_autostart_cli_status_is_read_only_and_enable_disable_use_lifecycle_lock(tmp_path, monkeypatch, capsys):
    from memory_base import autostart
    home = _installed_home(tmp_path)
    events = []

    @contextmanager
    def lock(path):
        events.append(("lock", path))
        yield
        events.append(("unlock", path))

    monkeypatch.setattr(deploy, "lifecycle_lock", lock)
    monkeypatch.setattr(autostart, "status", lambda path: events.append(("status", path)) or {"enabled": False})
    monkeypatch.setattr(autostart, "enable", lambda path, python: events.append(("enable", path, python)) or {"enabled": True})
    monkeypatch.setattr(autostart, "disable", lambda path: events.append(("disable", path)) or {"enabled": False})
    monkeypatch.setattr(deploy, "load_config", lambda _: {"python": "installed-python"})
    assert deploy.main(["autostart", "--home", str(home)]) == 0
    assert events == [("status", home)]
    assert json.loads(capsys.readouterr().out) == {"enabled": False}
    events.clear()
    assert deploy.main(["autostart", "--home", str(home), "--mode", "enable"]) == 0
    assert events == [("lock", home), ("enable", home, "installed-python"), ("unlock", home)]
    assert json.loads(capsys.readouterr().out) == {"enabled": True}
    events.clear()
    monkeypatch.setattr(deploy, "load_config", lambda _: pytest.fail("disable must allow removed runtime"))
    assert deploy.main(["autostart", "--home", str(home), "--mode", "disable"]) == 0
    assert events == [("lock", home), ("disable", home), ("unlock", home)]


def test_llama_context_is_serialized_across_parallel_requests():
    class UnsafeModel:
        active = 0
        lock = threading.Lock()

        def create_embedding(self, texts):
            with self.lock:
                self.active += 1
                assert self.active == 1, "model entered concurrently"
            time.sleep(0.005)
            with self.lock:
                self.active -= 1
            return {"data": [{"embedding": [float(len(text))]} for text in texts]}

    state = EmbeddingState(UnsafeModel())
    with ThreadPoolExecutor(max_workers=8) as executor:
        outputs = list(executor.map(lambda i: state.embed([str(i)]), range(24)))
    assert outputs == [[[1.0]]] * 10 + [[[2.0]]] * 14
    assert state.calls == state.items == 24
