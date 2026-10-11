"""Install and manage the pinned personal memory service on the local computer.

The manager owns only processes recorded under its state home. Credentials are
injected into child environments and never placed in configuration or PID files.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from memory_base.credentials import env_name, get_secret, set_secret, state_home
from memory_base.embed import DEFAULT_MODEL, MODEL_ID

PINS = {"openviking": "0.4.23", "openviking-sdk": "0.1.13", "mcp": "1.30.0",
        "llama-cpp-python": "0.3.36", "volcengine-python-sdk": "5.0.51", "openai": "2.54.0"}
PROFILES = {"agent-bubble": "writer", "codex": "writer", "dsh": "writer",
            "opencode": "writer", "reader": "reader"}
COMPONENTS = ("embed", "upstream", "gateway")
ROOT_KEY_ENV = "MEMORY_BASE_ROOT_KEY"
_RUN_COMPONENT = ("import os,runpy,sys;os.environ['MEMORY_BASE_HOME']=sys.argv[1];"
                  "module=sys.argv[2];sys.argv=[module,*sys.argv[3:]];"
                  "runpy.run_module(module,run_name='__main__')")


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


@contextmanager
def lifecycle_lock(home: Path):
    home.mkdir(parents=True, exist_ok=True)
    with (home / "lifecycle.lock").open("a+b") as file:
        file.seek(0)
        file.write(b"0")
        file.flush()
        file.seek(0)
        if os.name == "nt":
            import msvcrt
            deadline = time.monotonic() + 60
            while True:
                try:
                    msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Another memory-base lifecycle command is still running")
                    time.sleep(0.2)
            try:
                yield
            finally:
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(file, fcntl.LOCK_UN)


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    # Windows environment names are case insensitive; inherited duplicates can
    # make httpx fail before reaching a loopback endpoint.
    env = {key.upper() if os.name == "nt" else key: value for key, value in os.environ.items()}
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(name, None)
    env["NO_PROXY"] = "localhost,127.0.0.1,::1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    if extra:
        env.update(extra)
    return env


def _request(url: str, method="GET", body=None, key: str | None = None,
             user: str | None = None, timeout=15) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key
    if user:
        headers.update({"X-OpenViking-Account": "personal", "X-OpenViking-User": user})
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                     headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read())
        except (ValueError, UnicodeDecodeError):
            payload = {}
        return error.code, payload
    except (OSError, ValueError):
        return 0, {}


def _api(config: dict, path: str, method="GET", body=None, key=None, user=None) -> dict:
    code, payload = _request(f"http://127.0.0.1:{config['ports']['upstream']}{path}",
                             method, body, key, user, timeout=90)
    if code != 200 or payload.get("status") != "ok":
        # Response bodies may contain submitted content or credentials. Display
        # only the route and status; full bodies are deliberately not logged.
        raise RuntimeError(f"Upstream {method} {path.split('?')[0]} failed (HTTP {code})")
    return payload.get("result", {})


def _versions(python: Path) -> dict:
    script = "import importlib.metadata as m,json;print(json.dumps({n:m.version(n) for n in " + repr(list(PINS)) + "}))"
    result = subprocess.run([str(python), "-c", script], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def _ensure_secret(name: str, home: Path) -> str:
    value = get_secret(name, home)
    if value:
        return value
    if os.name != "nt":
        raise RuntimeError(f"Missing credential: set {env_name(name)} in the service environment")
    value = secrets.token_urlsafe(32)
    set_secret(name, value, home)
    return value


def install(home: Path, runtime_python: str | None = None, model: str | None = None,
            ports: dict | None = None, fresh_runtime: bool = False) -> dict:
    """Create a dedicated venv or adopt an explicitly selected pinned runtime."""
    config_path = home / "deployment.json"
    previous = json.loads(config_path.read_text()) if config_path.exists() else {}
    if fresh_runtime and runtime_python:
        raise ValueError("Choose either --fresh-runtime or --runtime-python")
    python = Path(runtime_python or (None if fresh_runtime else previous.get("python")) or
                  home / "runtime" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")).resolve()
    if not python.exists():
        if runtime_python:
            raise FileNotFoundError("Selected runtime Python does not exist")
        subprocess.run([sys.executable, "-m", "venv", str(home / "runtime")], check=True)
    # Always let pip apply the verified constraints: matching direct versions
    # alone cannot detect a changed transitive dependency. Already satisfied
    # installations retain their packages.
    # The upstream cloud SDK ships many generated modules. Compile only modules
    # actually imported by our runtime rather than all unused providers at install.
    subprocess.run([str(python), "-m", "pip", "install", "--no-compile", "-r",
                    str(Path(__file__).with_name("requirements.txt"))], check=True)
    versions = _versions(python)
    if versions != PINS:
        raise RuntimeError("Runtime versions do not match the deployment pins")
    source_dir = Path(__file__).parent
    if (source_dir / "pyproject.toml").exists():
        subprocess.run([str(python), "-m", "pip", "install", "--no-deps", "-e", str(source_dir)], check=True)
    elif Path(sys.executable).resolve() != python:
        raise RuntimeError("Installing into a new runtime requires the source checkout; this package was installed as a wheel")
    model_path = Path(model or previous.get("model") or DEFAULT_MODEL).expanduser().resolve()
    if not model_path.exists():
        # Upstream's pinned model specification supplies its public download URL.
        script = ("from openviking.models.embedder.local_embedders import LocalDenseEmbedder;"
                  "LocalDenseEmbedder()")
        subprocess.run([str(python), "-c", script], env=clean_env(), check=True)
        if not model_path.exists():
            raise FileNotFoundError("Selected GGUF model does not exist")
    if model_path.read_bytes()[:4] != b"GGUF":
        raise RuntimeError("Embedding model does not have a GGUF header")
    selected_ports = ports or previous.get("ports") or {"upstream": 8786, "gateway": 8788, "embed": 8796}
    if len(set(selected_ports.values())) != 3 or any(not 1024 <= int(p) <= 65535 for p in selected_ports.values()):
        raise ValueError("Service ports must be distinct ports between 1024 and 65535")
    for secret in ("root-key", *["host-" + p for p in PROFILES]):
        _ensure_secret(secret, home)
    config = {"schema": 1, "python": str(python), "model": str(model_path), "ports": selected_ports,
              "source": str(Path(__file__).parent.parent.resolve()), "account": "personal",
              "user": "gateway", "versions": versions,
              "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest()}
    _write_json(config_path, config)
    write_upstream_config(home, config)
    return config


def load_config(home: Path) -> dict:
    path = home / "deployment.json"
    if not path.exists():
        raise RuntimeError("Memory base is not installed; run memory-base install first")
    return json.loads(path.read_text(encoding="utf-8"))


def write_upstream_config(home: Path, config: dict) -> Path:
    path = home / "openviking.conf"
    _write_json(path, {
        "default_account": "personal", "default_user": "gateway",
        "storage": {"workspace": str(home / "data")},
        "embedding": {"dense": {"provider": "openai", "api_base": f"http://127.0.0.1:{config['ports']['embed']}/v1",
                                 "api_key": "local-embedding", "model": MODEL_ID, "dimension": 512,
                                 "input": "text", "encoding_format": "float"}, "max_concurrent": 1},
        "encryption": {"api_key_hashing": {"enabled": True}},
        "server": {"host": "127.0.0.1", "port": config["ports"]["upstream"],
                   "auth_mode": "api_key", "root_api_key": "$" + ROOT_KEY_ENV},
    })
    return path


def _process_info(pid: int) -> dict | None:
    if os.name == "nt":
        command = (f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId = {int(pid)}';"
                   "if ($p) { @{command=$p.CommandLine;created=$p.CreationDate.ToUniversalTime().ToString('o')} | ConvertTo-Json -Compress }")
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                                capture_output=True, text=True, timeout=20,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            return json.loads(result.stdout) if result.stdout.strip() else None
        except ValueError:
            return None
    if sys.platform == "darwin":
        result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "args="],
                                capture_output=True, text=True, timeout=5)
        raw = result.stdout.strip()
        return {"created": raw[:24], "command": raw[24:].strip()} if raw else None
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        created = raw.rsplit(")", 1)[1].split()[19]
        command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        return {"command": command, "created": created}
    except OSError:
        return None


def owned_process(record: dict | None) -> bool:
    if not record or not isinstance(record.get("pid"), int):
        return False
    actual = _process_info(record["pid"])
    return bool(actual and actual.get("created") == record.get("created")
                and actual.get("command") == record.get("command"))


def _component_command(component: str, home: Path, config: dict, *, legacy=False) -> list[str]:
    if component == "embed":
        module = "memory_base.embed"
        args = ["--port", str(config["ports"][component]), "--model", config["model"]]
    elif component == "upstream":
        module = "openviking_cli.server_bootstrap"
        args = ["--config", str(home / "openviking.conf"), "--host", "127.0.0.1",
                "--port", str(config["ports"][component])]
    else:
        module, args = "memory_base.gateway", []
    if legacy:
        return [config["python"], "-m", module, *args]
    # The home appears in the real command line, even with a shared runtime.
    # No credential is passed as a command argument.
    return [config["python"], "-c", _RUN_COMPONENT, str(home), module, *args]


def owned_component(component: str, home: Path, config: dict, record: dict | None) -> bool:
    """Bind process creation and command to this deployment, not just a PID file."""
    if not owned_process(record) or record.get("port") != config["ports"][component]:
        return False
    actual = record.get("command", "").strip()
    commands = [_component_command(component, home, config)]
    if Path(config["python"]).resolve().is_relative_to(home.resolve()):
        commands.append(_component_command(component, home, config, legacy=True))
    return any(actual == (subprocess.list2cmdline(command) if os.name == "nt" else " ".join(command))
               for command in commands)


def _port_busy(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _records(home: Path) -> dict:
    path = home / "processes.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _health(component: str, config: dict) -> dict | None:
    code, payload = _request(f"http://127.0.0.1:{config['ports'][component]}/health", timeout=3)
    if code != 200:
        return None
    if component == "embed" and payload.get("service") != "memory-base-embedding":
        return None
    if component == "upstream" and (payload.get("version") != PINS["openviking"]
                                     or payload.get("auth_mode") != "api_key" or not payload.get("healthy")):
        return None
    if component == "gateway" and payload.get("service") != "memory-base-gateway":
        return None
    return payload


def ensure_identity(home: Path, config: dict) -> None:
    root_key = _ensure_secret("root-key", home)
    accounts = _api(config, "/api/v1/admin/accounts", key=root_key)
    if not any(account.get("account_id") == "personal" for account in accounts):
        result = _api(config, "/api/v1/admin/accounts", "POST",
                      {"account_id": "personal", "admin_user_id": "gateway",
                       "settings": {"acl": {"enabled": True}}}, key=root_key)
        set_secret("upstream-key", result["user_key"], home)
    restore_state = home / "restore-state.json"
    reissue = restore_state.exists() and json.loads(restore_state.read_text(encoding="utf-8")).get("identity_reissue_pending")
    key = get_secret("upstream-key", home)
    if not key or reissue:
        # A restored account may outlive its local protected credential. Key
        # regeneration is limited to this deployment's own gateway identity.
        result = _api(config, "/api/v1/admin/accounts/personal/users/gateway/key", "POST", {}, key=root_key)
        key = result["user_key"]
        set_secret("upstream-key", key, home)
    _api(config, "/api/v1/admin/accounts/personal/settings", "PATCH", {"acl": {"enabled": True}}, key=root_key)
    _api(config, "/api/v1/fs/ls?uri=viking%3A%2F%2Fresources", key=key, user="gateway")
    if reissue:
        state = json.loads(restore_state.read_text(encoding="utf-8"))
        state["identity_reissue_pending"] = False
        _write_json(restore_state, state)


def _spawn(component: str, home: Path, config: dict, records: dict, timeout: float) -> None:
    existing = records.get(component)
    if owned_component(component, home, config, existing):
        if _health(component, config):
            return
        raise RuntimeError(f"Owned {component} process is running but unhealthy; inspect memory-base logs")
    if _port_busy(config["ports"][component]):
        raise RuntimeError(f"Port {config['ports'][component]} is occupied by an unowned service")
    env = clean_env({"MEMORY_BASE_HOME": str(home), "PYTHONPATH": config["source"]})
    command = _component_command(component, home, config)
    if component == "upstream":
        env[ROOT_KEY_ENV] = _ensure_secret("root-key", home)
    elif component == "gateway":
        env.update(gateway_env(home, config))
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / (component + ".out.log")).open("ab") as out, (logs / (component + ".err.log")).open("ab") as err:
        process = subprocess.Popen(command, cwd=str(home), env=env, stdout=out, stderr=err,
                                   stdin=subprocess.DEVNULL, start_new_session=os.name != "nt",
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    actual = _process_info(process.pid)
    if not actual:
        raise RuntimeError(f"Could not record ownership of {component} process")
    records[component] = {"pid": process.pid, **actual, "port": config["ports"][component]}
    _write_json(home / "processes.json", records)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{component} exited during startup; inspect memory-base logs")
        if _health(component, config):
            return
        time.sleep(0.5)
    raise RuntimeError(f"{component} did not become healthy before timeout; inspect memory-base logs")


def gateway_env(home: Path, config: dict) -> dict[str, str]:
    key = get_secret("upstream-key", home)
    if not key:
        raise RuntimeError("Upstream gateway identity has not been provisioned")
    tokens = {_ensure_secret("host-" + profile, home): {"principal": profile, "role": role}
              for profile, role in PROFILES.items()}
    return {"MEMORY_BASE_OV_URL": f"http://127.0.0.1:{config['ports']['upstream']}", "MEMORY_BASE_OV_KEY": key,
            "MEMORY_BASE_OV_ACCOUNT": "personal", "MEMORY_BASE_OV_USER": "gateway",
            "MEMORY_BASE_TOKENS": json.dumps(tokens), "MEMORY_BASE_HOST": "127.0.0.1",
            "MEMORY_BASE_PORT": str(config["ports"]["gateway"])}


def start(home: Path, components=COMPONENTS, timeout: float = 180) -> dict:
    config = load_config(home)
    if _versions(Path(config["python"])) != PINS:
        raise RuntimeError("Runtime versions have changed; rerun memory-base install")
    write_upstream_config(home, config)
    records = _records(home)
    for component in COMPONENTS:
        if component not in components:
            continue
        _spawn(component, home, config, records, timeout)
        if component == "upstream":
            ensure_identity(home, config)
    return status(home)


def stop(home: Path, components=COMPONENTS) -> dict:
    config = load_config(home)
    records = _records(home)
    for component in reversed(COMPONENTS):
        if component not in components or component not in records:
            continue
        record = records[component]
        if owned_component(component, home, config, record):
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(record["pid"]), "/T", "/F"],
                               capture_output=True, check=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                os.killpg(record["pid"], signal.SIGTERM)
            deadline = time.monotonic() + 20
            while (owned_process(record) or _port_busy(record["port"])) and time.monotonic() < deadline:
                time.sleep(0.2)
            if owned_process(record) or _port_busy(record["port"]):
                raise RuntimeError(f"{component} process or port is still active after stopping the owned process")
        elif _port_busy(record["port"]):
            raise RuntimeError(f"Refusing to stop {component}: recorded process ownership no longer matches")
        records.pop(component)
        _write_json(home / "processes.json", records)
    return {"status": "stopped", "remaining": list(records)}


def status(home: Path) -> dict:
    config = load_config(home)
    records = _records(home)
    services = {component: {"pid": records.get(component, {}).get("pid"),
                            "owned": owned_component(component, home, config, records.get(component)),
                            "port": config["ports"][component], "health": _health(component, config)}
                for component in COMPONENTS}
    return {"status": "ready" if all(s["owned"] and s["health"] for s in services.values()) else "unavailable",
            "home": str(home), "url": f"http://127.0.0.1:{config['ports']['gateway']}",
            "versions": config["versions"], "services": services}


def redact(text: str, home: Path) -> str:
    for name in ("root-key", "upstream-key", *["host-" + profile for profile in PROFILES]):
        value = get_secret(name, home)
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="memory-base")
    parser.add_argument("command", choices=("install", "start", "stop", "restart", "status", "logs", "host-token",
                                             "backup", "restore"))
    parser.add_argument("--home")
    parser.add_argument("--runtime-python", help="Explicit existing runtime; versions are checked against pins")
    parser.add_argument("--fresh-runtime", action="store_true", help="Select the dedicated <home>/runtime venv instead of an adopted runtime")
    parser.add_argument("--model")
    parser.add_argument("--profile", choices=list(PROFILES), default="agent-bubble")
    parser.add_argument("--components", default=",".join(COMPONENTS))
    parser.add_argument("--lines", type=int, default=80)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--destination", help="New backup directory outside deployment and source roots")
    parser.add_argument("--backup", help="Verified cold backup to restore into this installed empty deployment")
    args = parser.parse_args(argv)
    home = state_home(args.home)
    components = tuple(args.components.split(","))
    if any(component not in COMPONENTS for component in components):
        parser.error("Unknown service component")
    if args.command == "backup" and not args.destination:
        parser.error("backup requires --destination")
    if args.command == "restore" and not args.backup:
        parser.error("restore requires --backup")
    try:
        if args.command == "host-token":
            token = get_secret("host-" + args.profile, home)
            if not token:
                raise RuntimeError("Host credential is not provisioned; run install first")
            # This is the only CLI command whose stdout intentionally contains a
            # secret. Hosts capture it directly into their process environment.
            print(token)
            return 0
        if args.command in ("backup", "restore"):
            from memory_base.backup import create_backup, restore_backup
            result = (create_backup(home, Path(args.destination), timeout=args.timeout) if args.command == "backup"
                      else restore_backup(home, Path(args.backup), timeout=args.timeout))
        elif args.command == "status":
            result = status(home)
        elif args.command == "logs":
            for component in components:
                for suffix in ("out", "err"):
                    path = home / "logs" / f"{component}.{suffix}.log"
                    if path.exists():
                        print(f"[{component}.{suffix}]")
                        print(redact("\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-args.lines:]), home))
            return 0
        else:
            with lifecycle_lock(home):
                if args.command == "install":
                    install(home, args.runtime_python, args.model, fresh_runtime=args.fresh_runtime)
                    result = {"status": "installed", "home": str(home), "versions": PINS}
                elif args.command == "stop":
                    result = stop(home, components)
                elif args.command == "restart":
                    stop(home, components)
                    result = start(home, components, args.timeout)
                else:
                    result = start(home, components, args.timeout)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        requires_ready = args.command == "status" or (args.command in ("start", "restart") and set(components) == set(COMPONENTS))
        return 1 if requires_ready and result["status"] != "ready" else 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"memory-base: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
