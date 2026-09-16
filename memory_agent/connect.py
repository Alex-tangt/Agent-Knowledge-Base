"""一步安装第二个消费者（#41 / ADR-0025 D17）。

「深·共享服务」：第二个 agent 软件（本机 = DeepTutor）**不新建服务端、不复制索引**，
而是作为 MCP `streamableHttp` 客户端连**同一个常驻 daemon**（`/mcp`）。本模块就是那条
「一步安装」命令——只做**配置 + 核验**，不新增任何服务端代码：

1. **写 DeepTutor 部署级 MCP 配置**（`<DEEPTUTOR_HOME>/data/user/settings/mcp.json`，
   `DEEPTUTOR_HOME` 缺省 = CWD，与 DeepTutor 自己的 `get_runtime_home()` 一致）：
   一条 `streamableHttp` → `http://127.0.0.1:8765/mcp`，**保留其它条目**、原子写、幂等。
2. **落位 skill**（`~/.config/opencode/skills/memory-agent/`）。
3. **核验 opencode 侧**（`~/.config/opencode/opencode.json` 的 `mcp.memory-agent` → `proxy.py`）；
   缺则打印 patch，**不擅自改用户配置**。
4. **确保 daemon 在跑**（复用 `proxy.ensure_daemon`）。

只读边界（D17）：第二消费者 v1 只用读工具，安装器在 DeepTutor 侧用
`enabled_tools` 白名单把写 / 维护工具挡掉——边界落在配置里，不靠对方自觉。

用法：

    venv\\Scripts\\python.exe -m memory_agent.connect --dry-run
    venv\\Scripts\\python.exe -m memory_agent.connect --deeptutor-home D:\\Study\\my-deeptutor

零配置、无 token：本机单部署者下网关 `require_token` 默认关（ADR-0018 D2）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile

from memory_agent.settings import MCP_HTTP_HOST, MCP_HTTP_PATH, MCP_HTTP_PORT

#: 第二条消费者在 DeepTutor 侧的服务名（两个消费者共用一个 daemon，但各自登记名字）。
SERVER_NAME = "memory-agent"

#: 只读工具白名单（D17）：search / get / index_status / ingest_list。
#: 写（add / supersede / archive）与全量重建（reindex）留在本产品的写入网关。
READONLY_TOOLS = (
    "memory_search",
    "memory_get",
    "memory_index_status",
    "memory_ingest_list",
)

#: DeepTutor 部署级配置在 `<runtime-home>/data/user/settings/` 下。
DEEPTUTOR_DATA_SUBDIR = os.path.join("data", "user", "settings")
DEEPTUTOR_MCP_FILENAME = "mcp.json"

#: opencode 全局配置 / skill 落点（与 opencode 自身的约定一致）。
OPENCODE_CONFIG_SUBDIR = os.path.join(".config", "opencode")
OPENCODE_SKILL_DIRNAME = "memory-agent"

DEFAULT_TOOL_TIMEOUT = 30
DEFAULT_OPENCODE_TIMEOUT_MS = 20000


# --------------------------------------------------------------------- 路径解析

def resolve_deeptutor_home(explicit: str | None = None, environ: dict | None = None,
                           cwd: str | None = None) -> str:
    """DeepTutor 运行时 home：显式 > `DEEPTUTOR_HOME` > CWD（与其自身一致）。"""
    env = os.environ if environ is None else environ
    if explicit and str(explicit).strip():
        return os.path.abspath(os.path.expanduser(str(explicit).strip()))
    raw = (env.get("DEEPTUTOR_HOME") or "").strip()
    if raw:
        return os.path.abspath(os.path.expanduser(raw))
    return os.path.abspath(cwd or os.getcwd())


def deeptutor_mcp_config_path(home: str) -> str:
    """DeepTutor 部署级 MCP 配置路径（`<home>/data/user/settings/mcp.json`）。"""
    return os.path.join(os.path.abspath(home), DEEPTUTOR_DATA_SUBDIR,
                        DEEPTUTOR_MCP_FILENAME)


def _opencode_root(home: str | None = None) -> str:
    base = os.path.abspath(os.path.expanduser(home)) if home else os.path.expanduser("~")
    return os.path.join(base, OPENCODE_CONFIG_SUBDIR)


def opencode_config_path(home: str | None = None) -> str:
    return os.path.join(_opencode_root(home), "opencode.json")


def opencode_skill_dir(home: str | None = None) -> str:
    return os.path.join(_opencode_root(home), "skills", OPENCODE_SKILL_DIRNAME)


# --------------------------------------------------------------------- 内容构造

def daemon_url(host: str = MCP_HTTP_HOST, port: int = MCP_HTTP_PORT,
               path: str = MCP_HTTP_PATH) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return f"http://{host}:{port}{path}"


def build_server_entry(url: str, *, tool_timeout: int = DEFAULT_TOOL_TIMEOUT) -> dict:
    """DeepTutor `MCPServerConfig` 形状的一条 streamableHttp 服务定义。

    字段与 `deeptutor.services.mcp.config.MCPServerConfig` 对齐；`enabled_tools`
    只给读工具（D17 只读边界）。
    """
    return {
        "type": "streamableHttp",
        "url": url,
        "tool_timeout": tool_timeout,
        "enabled_tools": list(READONLY_TOOLS),
        "enabled": True,
    }


def read_json_object(path: str) -> object | None:
    """读 JSON；不存在 / 非法 / 非对象都返回 None（调用方当空配置处理）。"""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def merge_mcp_config(existing: object | None, name: str,
                     entry: dict) -> tuple[dict, bool]:
    """把 `name` 合并进 DeepTutor 的 `{"servers": {...}}` 配置。

    保留其它服务与未知顶层键；对已有同名单条**只覆盖我们写的键**（用户额外字段
    不丢）。返回 `(新配置, changed)`——`changed=False` 表示磁盘无需改动（幂等）。
    """
    config = dict(existing) if isinstance(existing, dict) else {}
    servers = config.get("servers")
    servers = dict(servers) if isinstance(servers, dict) else {}

    previous = servers.get(name)
    merged = {**(previous if isinstance(previous, dict) else {}), **entry}
    changed = previous != merged
    servers[name] = merged
    config["servers"] = servers
    return config, changed


def atomic_write_json(path: str, data) -> None:
    """原子落盘（临时文件 + os.replace）——写坏会让 DeepTutor 静默丢全部服务。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".", suffix=".tmp", dir=os.path.dirname(path)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


# --------------------------------------------------------------------- 核验

def default_proxy_command() -> list[str]:
    """opencode 该注册的代理命令：优先本仓 `venv` 解释器 + 本仓 `proxy.py`。"""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    for candidate in (os.path.join(root, "venv", "Scripts", "python.exe"),
                      os.path.join(root, "venv", "bin", "python")):
        if os.path.isfile(candidate):
            return [candidate, os.path.join(here, "proxy.py")]
    return [sys.executable, os.path.join(here, "proxy.py")]


def opencode_registration(config: object | None) -> dict:
    """核验 opencode 是否已把 memory-agent 注册指向 `proxy.py`（只读，不写）。"""
    entry = None
    if isinstance(config, dict) and isinstance(config.get("mcp"), dict):
        entry = config["mcp"].get(SERVER_NAME)
    command = entry.get("command") if isinstance(entry, dict) else None
    proxy_ok = (
        isinstance(command, list)
        and any(str(part).endswith("proxy.py") for part in command)
    )
    local_ok = isinstance(entry, dict) and entry.get("type") == "local"
    ok = bool(isinstance(entry, dict) and local_ok and proxy_ok and entry.get("enabled"))
    if ok:
        detail = "已注册（type=local → proxy.py，enabled）"
    elif entry is None:
        detail = "未注册 memory-agent"
    else:
        detail = f"注册存在但形状不对：{json.dumps(entry, ensure_ascii=False)}"
    patch = {"mcp": {SERVER_NAME: {
        "type": "local",
        "command": default_proxy_command(),
        "enabled": True,
        "timeout": DEFAULT_OPENCODE_TIMEOUT_MS,
    }}}
    return {"ok": ok, "detail": detail, "patch": patch}


def skill_status(skill_dir: str) -> bool:
    return os.path.isfile(os.path.join(skill_dir, "SKILL.md"))


def install_skill(source_dir: str, dest_dir: str, *, dry_run: bool = False) -> str:
    """把随包发布的 skill 落位到全局 skills 目录（幂等，差分才写）。

    返回 `created` / `updated` / `unchanged` / `missing-source`。
    """
    source_file = os.path.join(source_dir, "SKILL.md")
    if not os.path.isfile(source_file):
        return "missing-source"
    dest_file = os.path.join(dest_dir, "SKILL.md")
    if os.path.isfile(dest_file):
        with open(source_file, "r", encoding="utf-8") as handle:
            new_text = handle.read()
        with open(dest_file, "r", encoding="utf-8") as handle:
            if handle.read() == new_text:
                return "unchanged"
        action = "updated"
    else:
        action = "created"
    if not dry_run:
        os.makedirs(dest_dir, exist_ok=True)
        shutil.copyfile(source_file, dest_file)
    return action


# --------------------------------------------------------------------- CLI

def _print_plan(report: dict) -> None:
    print("== #41 一步安装：第二消费者 (DeepTutor) → 共享 daemon ==")
    print(f"   DeepTutor home : {report['deeptutor_home']}")
    print(f"   MCP 配置       : {report['mcp_config_path']} "
          f"[{'需写入' if report['mcp_changed'] else '已是最新'}]")
    print(f"   daemon URL     : {report['url']}")
    print(f"   只读白名单     : {', '.join(READONLY_TOOLS)}")
    print(f"   opencode 注册  : {report['opencode']['detail']}")
    print(f"   skill 落位     : {report['skill_dir']} [{report['skill_action']}]")
    if not report["opencode"]["ok"]:
        print("\n   opencode 未注册 / 形状不符 —— 请手动合并下面这段到 "
              f"{report['opencode_config_path']}（安装器不擅自改用户配置）：")
        print(json.dumps(report["opencode"]["patch"], ensure_ascii=False, indent=2))


def run(*, deeptutor_home: str | None = None, url: str | None = None,
        opencode_home: str | None = None, dry_run: bool = False,
        ensure: bool = True, skill_source: str | None = None) -> int:
    home = resolve_deeptutor_home(deeptutor_home)
    mcp_path = deeptutor_mcp_config_path(home)
    url = url or daemon_url()

    existing = read_json_object(mcp_path)
    config, changed = merge_mcp_config(existing, SERVER_NAME, build_server_entry(url))

    opencode_cfg = read_json_object(opencode_config_path(opencode_home))
    registration = opencode_registration(opencode_cfg)
    skill_dir = opencode_skill_dir(opencode_home)
    source_dir = skill_source or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "skill")

    report = {
        "deeptutor_home": home,
        "mcp_config_path": mcp_path,
        "mcp_changed": changed,
        "url": url,
        "opencode": registration,
        "opencode_config_path": opencode_config_path(opencode_home),
        "skill_dir": skill_dir,
        "skill_action": install_skill(source_dir, skill_dir, dry_run=True),
    }
    _print_plan(report)

    if not dry_run:
        if changed:
            atomic_write_json(mcp_path, config)
            print(f"\n[写入] {mcp_path}")
        else:
            print("\n[跳过] DeepTutor MCP 配置已是最新（幂等）")
        action = install_skill(source_dir, skill_dir, dry_run=False)
        if action != "unchanged":
            print(f"[写入] skill → {skill_dir} ({action})")

    if not registration["ok"]:
        print("\n结论：未完成——opencode 侧需手动注册（见上面的 patch）。", file=sys.stderr)
        return 1

    if ensure and not dry_run:
        from memory_agent.proxy import ensure_daemon
        host, port, path = MCP_HTTP_HOST, MCP_HTTP_PORT, MCP_HTTP_PATH
        up = ensure_daemon(host, port, path)
        print(f"\n[daemon] {host}:{port}{path} {'up' if up else 'DOWN'}")
        if not up:
            print("结论：DeepTutor 已登记，但 daemon 未就绪，请查看 memory_agent/vector_db/daemon.log",
                  file=sys.stderr)
            return 1

    print("\n结论：PASS（DeepTutor 部署级 mcp.json 已登记到共享 daemon）")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory-agent-connect",
        description="一步安装第二个消费者（DeepTutor）→ 共享 memory-agent daemon",
    )
    parser.add_argument("--deeptutor-home", default=None,
                        help="DeepTutor 运行时 home（缺省：$DEEPTUTOR_HOME，否则 CWD）")
    parser.add_argument("--url", default=None,
                        help="共享 daemon 的 MCP URL（缺省 http://127.0.0.1:8765/mcp）")
    parser.add_argument("--opencode-home", default=None,
                        help="opencode 配置所在用户目录（缺省：当前用户 home；测试用）")
    parser.add_argument("--skill-source", default=None,
                        help="skill 源目录（缺省：本包 skill/；测试用）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只预览将要写入的内容，不落盘、不起 daemon")
    parser.add_argument("--no-daemon", action="store_true",
                        help="只写配置 / 核验，不确保 daemon 在跑")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return run(
        deeptutor_home=args.deeptutor_home,
        url=args.url,
        opencode_home=args.opencode_home,
        dry_run=args.dry_run,
        ensure=not args.no_daemon,
        skill_source=args.skill_source,
    )


if __name__ == "__main__":
    raise SystemExit(main())
