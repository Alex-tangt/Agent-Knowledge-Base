"""一键部署 CLI（#52 / ADR-0028）：`memory-agent install`。

设计要点：

- **脚本只是薄壳**：`install.sh` / `install.ps1` 只负责用系统 Python 以
  `PYTHONPATH=<repo> python -m memory_agent.deploy install` 起头；真正的编排都在这里。
- **bootstrap 自举**：本模块顶层**只依赖 stdlib**（`opencode_config` 也是零依赖），
  所以能在 venv / 依赖还没装好时先跑起来；它自己建 venv、装 deploy requirements、
  以 editable 装 `ragcore` + `memory_agent`，再继续后面的步骤。
- **幂等**：venv / 依赖 / 注册 / skill /（已自洽的）索引都可重复跑；`--dry-run` 不落盘。
- **跨平台**：Linux/WSL 与 Windows 同一条路径；Linux/Windows 默认取 CPU 轮子。
- **依赖解耦**：用 `memory_agent/deploy-requirements.txt`，与 `legal_web` 无关（ADR-0028 D3）。

用法：

    bash install.sh [--dry-run] [--no-index] [--no-daemon] [--with-tests]
    pwsh install.ps1 ...
    memory-agent install --dry-run          # 已在 venv 内时

stdout 只用于人读的进度 + 内部子命令的 JSON（不是 MCP 协议通道）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request

from memory_agent.opencode_config import (
    PLUGIN_DEP_NAME,
    PLUGIN_DEP_RANGE,
    install_agent,
    install_plugin,
    install_skill,
    opencode_agent_dir,
    opencode_config_path,
    opencode_package_json,
    opencode_plugin_dir,
    opencode_skill_dir,
    remove_opencode_registration,
    uninstall_agent,
    uninstall_plugin,
    uninstall_skill,
    write_opencode_registration,
    write_package_dependency,
)

MEMORY_AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(MEMORY_AGENT_DIR)
DEPLOY_REQUIREMENTS = os.path.join(MEMORY_AGENT_DIR, "deploy-requirements.txt")
SKILL_SOURCE_DIR = os.path.join(MEMORY_AGENT_DIR, "skill")
AGENT_NAME = "memory-research"
AGENT_SOURCE_FILE = os.path.join(MEMORY_AGENT_DIR, "agent", f"{AGENT_NAME}.md")
PLUGIN_SOURCE_FILE = os.path.join(MEMORY_AGENT_DIR, "plugin", f"{AGENT_NAME}.js")
PROXY_SCRIPT = os.path.join(MEMORY_AGENT_DIR, "proxy.py")
BUILD_INDEX_SCRIPT = os.path.join(MEMORY_AGENT_DIR, "build_index.py")

#: Linux / Windows 上 torch 默认 PyPI 轮子是 CUDA 构建（大）。本产品模型跑 CPU，取 CPU 轮子。
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"

DEFAULT_HOST = os.environ.get("MEMORY_MCP_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("MEMORY_MCP_PORT", "8765"))
DEFAULT_PATH = os.environ.get("MEMORY_MCP_PATH", "/mcp")


# --------------------------------------------------------------------- 通用

def venv_dir(root: str) -> str:
    return os.path.join(root, "venv")


def venv_python(root: str) -> str:
    if os.name == "nt":
        return os.path.join(venv_dir(root), "Scripts", "python.exe")
    return os.path.join(venv_dir(root), "bin", "python")


def _same_interpreter(path: str) -> bool:
    if not path or not os.path.isfile(path):
        return False
    try:
        return os.path.samefile(os.path.abspath(sys.executable), os.path.abspath(path))
    except OSError:
        return os.path.realpath(sys.executable) == os.path.realpath(path)


def _printable(cmd: list) -> str:
    parts = [str(part) for part in cmd]
    if os.name == "nt":
        return subprocess.list2cmdline(parts)
    return " ".join(parts)


def _run(cmd: list, *, dry_run: bool = False, capture: bool = False,
         check: bool = True, cwd: str | None = None, label: str | None = None):
    prefix = f"[{label}] " if label else ""
    print(f"{prefix}$ {_printable(cmd)}", flush=True)
    if dry_run:
        return None
    kwargs = {"cwd": cwd, "text": True, "encoding": "utf-8", "errors": "replace"}
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    return subprocess.run([str(part) for part in cmd], check=check, **kwargs)


def _use_cpu_torch(args: argparse.Namespace) -> bool:
    if args.cpu_torch is not None:
        return args.cpu_torch
    # 个人模式的引擎就是 CPU 推理：Linux 与 Windows 都默认取 CPU 轮子，
    # 免得在只有 CPU 的机器上白下 ~2.5GB 的 CUDA 版 torch。macOS 轮子本就是 CPU/MPS，
    # 走 PyPI 默认即可。（显式 `--no-cpu-torch` 可覆盖。）
    return sys.platform.startswith(("linux", "win"))


# --------------------------------------------------------------------- bootstrap

def _pip_ok(target: str) -> bool:
    """目标解释器是否带可用 pip（半途失败留下的 venv 可能没有）。"""
    try:
        proc = subprocess.run([target, "-m", "pip", "--version"],
                              capture_output=True, text=True)
        return proc.returncode == 0
    except OSError:
        return False


def _create_venv(root: str, python: str, args: argparse.Namespace) -> int | None:
    print(f"[deps] 创建 venv：{venv_dir(root)}")
    try:
        _run([python, "-m", "venv", venv_dir(root)], dry_run=args.dry_run, label="deps")
    except subprocess.CalledProcessError:
        print("FAIL: 创建 venv 失败。Debian/Ubuntu 需先 `sudo apt install python3-venv`。",
              file=sys.stderr)
        return 1
    target = venv_python(root)
    if not args.dry_run and not _pip_ok(target):
        print(f"FAIL: venv 建好后 pip 不可用（{target}）；确认系统已装 python3-venv / ensurepip。",
              file=sys.stderr)
        return 1
    return None


def _bootstrap(root: str, args: argparse.Namespace) -> int | None:
    """建 venv + 装依赖（幂等）。返回非 0 表示失败，None 表示可继续。"""
    target = venv_python(root)
    if _same_interpreter(target):
        print(f"[deps] 已在目标 venv 内：{target}")
        return None

    python = args.python or sys.executable
    if not os.path.isfile(target) or not args.dry_run and not _pip_ok(target):
        if os.path.isdir(venv_dir(root)) and not args.dry_run:
            print("[deps] venv 不完整（pip 不可用）；重建")
            shutil.rmtree(venv_dir(root), ignore_errors=True)
        rc = _create_venv(root, python, args)
        if rc:
            return rc

    pip = [target, "-m", "pip"]
    _run([*pip, "install", "--upgrade", "pip"], dry_run=args.dry_run, label="deps")
    if _use_cpu_torch(args):
        _run([*pip, "install", "torch", "--index-url", TORCH_CPU_INDEX],
             dry_run=args.dry_run, label="deps")

    install_cmd = [
        *pip, "install", "-r", DEPLOY_REQUIREMENTS,
        "-e", os.path.join(root, "ragcore"),
        "-e", os.path.join(root, "memory_agent"),
    ]
    if args.with_tests:
        install_cmd.append("pytest")
    try:
        _run(install_cmd, dry_run=args.dry_run, label="deps")
    except subprocess.CalledProcessError:
        print("FAIL: 依赖安装失败（见上）。", file=sys.stderr)
        return 1
    return None


# --------------------------------------------------------------------- 步骤

def _register(args: argparse.Namespace) -> int:
    result = write_opencode_registration(
        opencode_config_path(args.opencode_home), dry_run=args.dry_run)
    status = result["status"]
    if status == "parse-error":
        print(f"FAIL: {result['path']} 不是合法 JSON，拒绝覆盖；请手动合并 mcp.memory-agent。",
              file=sys.stderr)
        return 1
    if status == "would-write":
        print(f"[register] 预览：将写入 {result['path']}（--dry-run 不落盘）")
    elif status == "unchanged":
        print(f"[register] 已是最新（幂等）：{result['path']}")
    else:
        suffix = f"（备份 {result['backup']}）" if result["backup"] else ""
        print(f"[register] {status}：{result['path']}{suffix}")
    return 0


def _skill(args: argparse.Namespace) -> int:
    dest = opencode_skill_dir(args.opencode_home)
    action = install_skill(SKILL_SOURCE_DIR, dest, dry_run=args.dry_run)
    if action == "missing-source":
        print(f"FAIL: 找不到 skill 源：{SKILL_SOURCE_DIR}/SKILL.md", file=sys.stderr)
        return 1
    suffix = "（--dry-run 未落盘）" if args.dry_run else ""
    print(f"[skill] {action}：{dest}{suffix}")
    return 0


def _agent(args: argparse.Namespace) -> int:
    dest = os.path.join(opencode_agent_dir(args.opencode_home), f"{AGENT_NAME}.md")
    action = install_agent(AGENT_SOURCE_FILE, dest, dry_run=args.dry_run)
    if action == "missing-source":
        print(f"FAIL: 找不到 agent 源：{AGENT_SOURCE_FILE}", file=sys.stderr)
        return 1
    suffix = "（--dry-run 未落盘）" if args.dry_run else ""
    print(f"[agent] {action}：{dest}{suffix}")
    return 0


def _plugin(args: argparse.Namespace) -> int:
    dest = os.path.join(opencode_plugin_dir(args.opencode_home), f"{AGENT_NAME}.js")
    action = install_plugin(PLUGIN_SOURCE_FILE, dest, dry_run=args.dry_run)
    if action == "missing-source":
        print(f"FAIL: 找不到 plugin 源：{PLUGIN_SOURCE_FILE}", file=sys.stderr)
        return 1
    suffix = "（--dry-run 未落盘）" if args.dry_run else ""
    print(f"[plugin] {action}：{dest}{suffix}")

    dep = write_package_dependency(
        opencode_package_json(args.opencode_home), PLUGIN_DEP_NAME, PLUGIN_DEP_RANGE,
        dry_run=args.dry_run)
    if dep["status"] == "parse-error":
        print(f"FAIL: {dep['path']} 不是合法 JSON，拒绝覆盖；请手动加依赖 {PLUGIN_DEP_NAME}。",
              file=sys.stderr)
        return 1
    if dep["status"] == "would-write":
        print(f"[plugin] 预览：将写入依赖 {PLUGIN_DEP_NAME} → {dep['path']}（--dry-run 不落盘）")
    elif dep["status"] == "unchanged":
        print(f"[plugin] 依赖已是最新：{dep['path']}")
    else:
        backup = f"（备份 {dep['backup']}）" if dep["backup"] else ""
        print(f"[plugin] 依赖 {dep['status']}：{dep['path']}{backup}")
    return 0


def _index_status(target: str) -> dict | None:
    proc = _run([target, "-m", "memory_agent.deploy", "__status"],
                capture=True, check=False, label="index")
    if proc is None or proc.returncode != 0:
        return None
    for line in reversed((proc.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def _index(target: str, args: argparse.Namespace) -> int:
    if args.dry_run:
        print("[index] 预览：将重建派生索引（首次会下载 BGE-M3 ~2.2GB）")
        return 0
    if not args.force_index:
        status = _index_status(target)
        if status and status.get("built") and status.get("consistent"):
            print(f"[index] 已存在且自洽（gen={status.get('gen')}, "
                  f"entries={status.get('entries')}）；跳过重建（--force-index 可强制）")
            return 0
    print("[index] 重建派生索引（首次下载 / 加载 BGE-M3，CPU 上较慢）...")
    try:
        _run([target, BUILD_INDEX_SCRIPT], label="index")
    except subprocess.CalledProcessError:
        print("FAIL: 建索引失败（可单独跑 memory_agent/build_index.py 看 stderr）。", file=sys.stderr)
        return 1
    return 0


def _daemon(target: str, args: argparse.Namespace) -> int:
    if args.dry_run:
        print(f"[daemon] 预览：将确保 {DEFAULT_HOST}:{DEFAULT_PORT}{DEFAULT_PATH} 在跑")
        return 0
    print(f"[daemon] 确保 {DEFAULT_HOST}:{DEFAULT_PORT}{DEFAULT_PATH} 在跑（首次加载模型）...")
    proc = _run([target, PROXY_SCRIPT, "--ensure",
                 "--host", DEFAULT_HOST, "--port", str(DEFAULT_PORT), "--path", DEFAULT_PATH],
                check=False, label="daemon")
    if proc is None or proc.returncode != 0:
        print("FAIL: daemon 未就绪（见 memory_agent/vector_db/daemon.log）。", file=sys.stderr)
        return 1
    return 0


def _http_json(url: str, *, timeout: float, payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json"} if data is not None else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _health_smoke() -> tuple[bool, str]:
    try:
        status, body = _http_json(f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/health", timeout=3.0)
        return status == 200 and body.get("status") == "ok", f"status={status}"
    except Exception as exc:  # noqa: BLE001
        return False, repr(exc)


def _embeddings_smoke() -> tuple[bool, str]:
    try:
        status, body = _http_json(
            f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/v1/embeddings", timeout=300.0,
            payload={"input": "memory-agent 部署冒烟", "model": "BAAI/bge-m3"})
        dim = len(body["data"][0]["embedding"])
        return status == 200 and dim == 1024, f"dim={dim}"
    except Exception as exc:  # noqa: BLE001
        return False, repr(exc)


def _mcp_smoke(target: str) -> tuple[bool, str]:
    proc = _run([target, "-m", "memory_agent.deploy", "__mcp-smoke"],
                capture=True, check=False, label="smoke")
    if proc is None or proc.returncode != 0:
        return False, (proc.stderr or "").strip()[-400:] if proc else "no result"
    for line in reversed((proc.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                result = json.loads(line)
            except json.JSONDecodeError:
                continue
            return bool(result.get("ok")), json.dumps(result, ensure_ascii=False)
    return False, "no JSON from smoke"


def _smoke(target: str, args: argparse.Namespace) -> int:
    if args.dry_run:
        print("[smoke] 预览：将探测 /health · POST /v1/embeddings(dim=1024) · 经 proxy 调 MCP")
        return 0
    ok = True
    healthy, detail = _health_smoke()
    print(f"  [{'PASS' if healthy else 'FAIL'}] daemon /health — {detail}")
    ok &= healthy
    if healthy:
        embedded, detail = _embeddings_smoke()
        print(f"  [{'PASS' if embedded else 'FAIL'}] POST /v1/embeddings dim=1024 — {detail}")
        ok &= embedded
    else:
        print("  [SKIP] embeddings / MCP 冒烟（daemon 未就绪）")
    if ok:
        mcp_ok, detail = _mcp_smoke(target)
        print(f"  [{'PASS' if mcp_ok else 'FAIL'}] 经 proxy 调 MCP — {detail}")
        ok &= mcp_ok
    return 0 if ok else 1


# --------------------------------------------------------------------- 内部子命令

def _cmd_status(argv: list[str]) -> int:
    try:
        from memory_agent.runtime import get_index
        status = get_index().status()
    except Exception as exc:  # noqa: BLE001 - 未建索引 / 配置问题都当“未就绪”
        status = {"built": False, "error": str(exc)}
    print(json.dumps(status, ensure_ascii=False))
    return 0


def _extract_structured(result):
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        if set(structured) == {"result"}:
            return structured["result"]
        return structured
    text = result.content[0].text if result.content else ""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, IndexError):
        return text


def _cmd_mcp_smoke(argv: list[str]) -> int:
    import asyncio

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def _run_smoke() -> dict:
        # 必须显式传完整 env：MCP SDK 的 StdioServerParameters 默认只给子进程一个
        # **过滤后的安全环境**，MEMORY_MCP_PORT / HF_* 等都不会传下去（#53 实测：
        # 隔离端口 8766 被丢掉 → proxy 回落 8765，连到宿主 Windows 的 daemon）。
        params = StdioServerParameters(
            command=sys.executable, args=[PROXY_SCRIPT], cwd=REPO_ROOT,
            env={**os.environ})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = [tool.name for tool in (await session.list_tools()).tools]
                status = _extract_structured(
                    await session.call_tool("memory_index_status", {}))
                hits = _extract_structured(await session.call_tool(
                    "memory_search",
                    {"query": "部署冒烟 memory_agent 记忆", "k": 3, "writable_only": True}))
        return {
            "ok": bool(tools) and isinstance(status, dict)
            and status.get("built") and status.get("consistent") is True,
            "tools": len(tools),
            "gen": status.get("gen") if isinstance(status, dict) else None,
            "hits": len(hits) if isinstance(hits, list) else None,
        }

    try:
        result = asyncio.run(_run_smoke())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": repr(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


_INTERNAL = {"__status": _cmd_status, "__mcp-smoke": _cmd_mcp_smoke}


# --------------------------------------------------------------------- 编排

def run_install(args: argparse.Namespace) -> int:
    root = os.path.abspath(args.repo or REPO_ROOT)
    print("== memory-agent 一键部署（#52 / ADR-0028）==")
    print(f"   repo : {root}")
    print(f"   mode : {'dry-run（不落盘）' if args.dry_run else 'install'}")
    if not os.path.isfile(os.path.join(root, "memory_agent", "pyproject.toml")):
        print(f"FAIL: 这不是仓库根（缺 memory_agent/pyproject.toml）：{root}", file=sys.stderr)
        return 2

    if args.skip_deps:
        print("[deps] 跳过（--skip-deps）")
    else:
        rc = _bootstrap(root, args)
        if rc:
            return rc

    target = venv_python(root)
    needs_venv = not (args.no_index and args.no_daemon and args.no_smoke)
    if not args.dry_run and needs_venv and not os.path.isfile(target):
        print(f"FAIL: 目标解释器不存在：{target}", file=sys.stderr)
        return 1

    steps = []
    if args.no_register:
        print("[register] 跳过（--no-register）")
    else:
        steps.append(lambda: _register(args))
    if args.no_skill:
        print("[skill] 跳过（--no-skill）")
    else:
        steps.append(lambda: _skill(args))
    if args.no_agent:
        print("[agent] 跳过（--no-agent）")
    else:
        steps.append(lambda: _agent(args))
    if args.no_plugin:
        print("[plugin] 跳过（--no-plugin）")
    else:
        steps.append(lambda: _plugin(args))
    if args.no_index:
        print("[index] 跳过（--no-index）")
    else:
        steps.append(lambda: _index(target, args))
    if args.no_daemon:
        print("[daemon] 跳过（--no-daemon）")
    else:
        steps.append(lambda: _daemon(target, args))
    if args.no_smoke:
        print("[smoke] 跳过（--no-smoke）")
    else:
        steps.append(lambda: _smoke(target, args))

    for step in steps:
        rc = step()
        if rc:
            return rc

    print("== 完成 ==" + ("（dry-run：未落盘）" if args.dry_run else ""))
    return 0


# --------------------------------------------------------------------- 卸载

def _stop_daemon(args: argparse.Namespace) -> int:
    target = venv_python(os.path.abspath(args.repo or REPO_ROOT))
    if args.dry_run:
        print(f"[daemon] 预览：将停止 {DEFAULT_HOST}:{DEFAULT_PORT}{DEFAULT_PATH} 的 daemon")
        return 0
    if not os.path.isfile(target):
        print("[daemon] 目标 venv 不存在，跳过停止")
        return 0
    _run([target, PROXY_SCRIPT, "--stop", "--host", DEFAULT_HOST,
          "--port", str(DEFAULT_PORT)], check=False, label="daemon")
    return 0


def _unregister(args: argparse.Namespace) -> int:
    result = remove_opencode_registration(
        opencode_config_path(args.opencode_home), dry_run=args.dry_run)
    status = result["status"]
    if status == "parse-error":
        print(f"FAIL: {result['path']} 不是合法 JSON，拒绝改动；请手动删除 mcp.memory-agent。",
              file=sys.stderr)
        return 1
    if status == "absent":
        print(f"[unregister] 无注册（幂等）：{result['path']}")
    elif status == "would-remove":
        print("[unregister] 预览：将移除 mcp.memory-agent（--dry-run 不落盘）")
    else:
        print(f"[unregister] removed：{result['path']}（备份 {result['backup']}）")
    return 0


def _remove_skill(args: argparse.Namespace) -> int:
    dest = opencode_skill_dir(args.opencode_home)
    action = uninstall_skill(dest, dry_run=args.dry_run)
    suffix = "（--dry-run 未落盘）" if args.dry_run else ""
    print(f"[skill] {action}：{dest}{suffix}")
    return 0


def _remove_agent(args: argparse.Namespace) -> int:
    dest = os.path.join(opencode_agent_dir(args.opencode_home), f"{AGENT_NAME}.md")
    action = uninstall_agent(dest, dry_run=args.dry_run)
    suffix = "（--dry-run 未落盘）" if args.dry_run else ""
    print(f"[agent] {action}：{dest}{suffix}")
    return 0


def _remove_plugin(args: argparse.Namespace) -> int:
    dest = os.path.join(opencode_plugin_dir(args.opencode_home), f"{AGENT_NAME}.js")
    action = uninstall_plugin(dest, dry_run=args.dry_run)
    suffix = "（--dry-run 未落盘）" if args.dry_run else ""
    print(f"[plugin] {action}：{dest}{suffix}")
    # 依赖 `@opencode-ai/plugin` 是**共享**的（他工具也可能装）：卸载**不动**它，
    # 免得误伤；留着无害（opencode 只按需 bun install）。
    print(f"[plugin] 依赖 {PLUGIN_DEP_NAME} 保留（共享，卸载不移除）："
          f"{opencode_package_json(args.opencode_home)}")
    return 0


def run_uninstall(args: argparse.Namespace) -> int:
    root = os.path.abspath(args.repo or REPO_ROOT)
    print("== memory-agent 卸载（注册 / skill / daemon；不删 clone）==")
    print(f"   repo : {root}")
    print(f"   mode : {'dry-run（不落盘）' if args.dry_run else 'uninstall'}")

    steps = []
    if args.no_daemon:
        print("[daemon] 跳过（--no-daemon）")
    else:
        steps.append(lambda: _stop_daemon(args))
    if args.no_register:
        print("[unregister] 跳过（--no-register）")
    else:
        steps.append(lambda: _unregister(args))
    if args.no_skill:
        print("[skill] 跳过（--no-skill）")
    else:
        steps.append(lambda: _remove_skill(args))
    if args.no_agent:
        print("[agent] 跳过（--no-agent）")
    else:
        steps.append(lambda: _remove_agent(args))
    if args.no_plugin:
        print("[plugin] 跳过（--no-plugin）")
    else:
        steps.append(lambda: _remove_plugin(args))

    for step in steps:
        rc = step()
        if rc:
            return rc

    print("== 完成 ==" + ("（dry-run：未落盘）" if args.dry_run else ""))
    if not args.dry_run:
        print(f"如需彻底移除运行时（venv / 索引 / 本仓 KB），删除该目录即可：{root}")
    return 0


# --------------------------------------------------------------------- CLI

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory-agent install",
        description="一键部署 memory_agent（幂等；ADR-0028）",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="只预览将执行的命令与将写入的内容，不落盘")
    parser.add_argument("--repo", default=None, help="仓库根（默认本文件所在仓库）")
    parser.add_argument("--python", default=None,
                        help="用于创建 venv 的解释器（默认当前解释器）")
    parser.add_argument("--opencode-home", default=None,
                        help="opencode 配置所在用户目录（默认当前用户 home；测试用）")
    parser.add_argument("--skip-deps", action="store_true",
                        help="跳过 venv / 依赖安装（已装好时用）")
    parser.add_argument("--with-tests", action="store_true",
                        help="额外安装 pytest（跑 tests/unit 验收用）")
    parser.add_argument("--force-index", action="store_true",
                        help="即使已有自洽索引也重建")
    parser.add_argument("--no-register", action="store_true", help="不写 opencode 注册")
    parser.add_argument("--no-skill", action="store_true", help="不落位 skill")
    parser.add_argument("--no-agent", action="store_true",
                        help="不落位 memory-research subagent（#60）")
    parser.add_argument("--no-plugin", action="store_true",
                        help="不落位 memory_research 插件（#61）")
    parser.add_argument("--no-index", action="store_true", help="不建 / 重建索引")
    parser.add_argument("--no-daemon", action="store_true", help="不拉起 daemon")
    parser.add_argument("--no-smoke", action="store_true", help="不跑冒烟")
    torch_group = parser.add_mutually_exclusive_group()
    torch_group.add_argument("--cpu-torch", dest="cpu_torch", action="store_true",
                             default=None,
                             help="从 PyTorch CPU 索引装 torch（Linux/Windows 默认）")
    torch_group.add_argument("--no-cpu-torch", dest="cpu_torch", action="store_false",
                             default=None, help="不特殊处理 torch（走 PyPI 默认，可能是 CUDA 大包）")
    return parser


def _build_uninstall_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory-agent uninstall",
        description="卸载 memory_agent 的 opencode 注册与 skill，并停掉 daemon（不删 clone）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只预览，不落盘")
    parser.add_argument("--repo", default=None, help="仓库根（默认本文件所在仓库）")
    parser.add_argument("--opencode-home", default=None,
                        help="opencode 配置所在用户目录（默认当前用户 home；测试用）")
    parser.add_argument("--no-register", action="store_true", help="不移除 opencode 注册")
    parser.add_argument("--no-skill", action="store_true", help="不移除 skill")
    parser.add_argument("--no-agent", action="store_true", help="不移除 agent（#60）")
    parser.add_argument("--no-plugin", action="store_true", help="不移除 plugin（#61）")
    parser.add_argument("--no-daemon", action="store_true", help="不停 daemon")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in _INTERNAL:
        return _INTERNAL[argv[0]](argv[1:])
    if argv and argv[0] == "uninstall":
        return run_uninstall(_build_uninstall_parser().parse_args(argv[1:]))
    if argv and argv[0] == "install":
        argv = argv[1:]
    args = _build_parser().parse_args(argv)
    return run_install(args)


if __name__ == "__main__":
    raise SystemExit(main())
