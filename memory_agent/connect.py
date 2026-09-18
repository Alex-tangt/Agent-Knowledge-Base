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

共享边界（D19 **修订** D17）：全局知识库是**所有 agent 可读可写的共享域**，第二消费者
**读写同一 KB**——安装器在 DeepTutor 侧用 `enabled_tools` 白名单同时放开读工具与内容写工具
（add / supersede / archive）；索引维护（reindex）不进白名单，边界落在配置里、不靠对方自觉。

用法：

    venv\\Scripts\\python.exe -m memory_agent.connect --dry-run
    venv\\Scripts\\python.exe -m memory_agent.connect --deeptutor-home D:\\Study\\my-deeptutor

零配置、无 token：本机单部署者下网关 `require_token` 默认关（ADR-0018 D2）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from memory_agent.opencode_config import (  # noqa: F401  (re-export 给旧调用方)
    DEFAULT_OPENCODE_TIMEOUT_MS,
    OPENCODE_CONFIG_SUBDIR,
    OPENCODE_SKILL_DIRNAME,
    atomic_write_json,
    build_opencode_entry,
    default_proxy_command,
    install_skill,
    merge_opencode_config,
    opencode_config_path,
    opencode_skill_dir,
    skill_status,
    write_opencode_registration,
)
from memory_agent.settings import MCP_HTTP_HOST, MCP_HTTP_PATH, MCP_HTTP_PORT

#: 第二条消费者在 DeepTutor 侧的服务名（两个消费者共用一个 daemon，但各自登记名字）。
SERVER_NAME = "memory-agent"

#: 第二消费者可用工具白名单（#44 / ADR-0025 **D19 修订 D17**）。
#: 全局知识库是**所有 agent 可读可写的共享域**：
#: - 读 = 整张基表（search / get / index_status / ingest_list）；
#: - 写 = 全局 KB 内容（add / supersede / archive），经本产品写入网关 + 单 daemon
#:   进程内 `WRITE_LOCK` 串行化——多 agent 写同一 KB 安全（D19）。
#: 写入的问责面 = agent 身份：daemon 审计对每个 `tools/call` 记身份（见 `gateway/middleware.py`）。
SHARED_TOOLS = (
    "memory_search",
    "memory_get",
    "memory_add",
    "memory_supersede",
    "memory_archive",
    "memory_index_status",
    "memory_ingest_list",
)

#: **不**纳入共享白名单的工具（维护 / 收录 DDL），附理由：
#: - `memory_reindex`：换代重建**全消费者共用**的派生索引、切换指针（影响所有消费者），
#:   不是内容写入；D19 共享的是「全局 KB 内容」，索引一致性是部署者的维护动作，
#:   日常新鲜度已由 D13 惰性刷新覆盖。暴露它会让第二个 agent 扰动共享派生索引。
#: - `memory_ingest_include` / `memory_ingest_exclude`：只读语料收录范围（本地部署者的
#:   收录 DDL），不属于「写全局 KB 内容」；#44 只放开内容写，收录决策不在本票范围。
RESTRICTED_TOOLS = (
    "memory_reindex",
    "memory_ingest_include",
    "memory_ingest_exclude",
)

#: DeepTutor 部署级配置在 `<runtime-home>/data/user/settings/` 下。
DEEPTUTOR_DATA_SUBDIR = os.path.join("data", "user", "settings")
DEEPTUTOR_MCP_FILENAME = "mcp.json"
DEEPTUTOR_MODEL_CATALOG_FILENAME = "model_catalog.json"

#: DeepTutor 的 embedding profile（#43）：`binding=vllm` = OpenAI 兼容、local、
#: 免 api_key，base_url 必须以 `/embeddings` 结尾。
EMBEDDINGS_PATH = "/v1/embeddings"
EMBEDDING_BINDING = "vllm"
EMBEDDING_PROFILE_ID = "embedding-profile-bge-m3"
EMBEDDING_MODEL_ID = "embedding-model-bge-m3"
EMBEDDING_MODEL_NAME = "BAAI/bge-m3"
EMBEDDING_DIMENSION = 1024

DEFAULT_TOOL_TIMEOUT = 30


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


def deeptutor_model_catalog_path(home: str) -> str:
    """DeepTutor 模型目录路径（`<home>/data/user/settings/model_catalog.json`）。"""
    return os.path.join(os.path.abspath(home), DEEPTUTOR_DATA_SUBDIR,
                        DEEPTUTOR_MODEL_CATALOG_FILENAME)


# --------------------------------------------------------------------- 内容构造

def daemon_url(host: str = MCP_HTTP_HOST, port: int = MCP_HTTP_PORT,
               path: str = MCP_HTTP_PATH) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return f"http://{host}:{port}{path}"


def daemon_embeddings_url(host: str = MCP_HTTP_HOST,
                          port: int = MCP_HTTP_PORT) -> str:
    """共享 daemon 的 OpenAI 兼容 embeddings 端点（#43）。"""
    return f"http://{host}:{port}{EMBEDDINGS_PATH}"


def build_server_entry(url: str, *, tool_timeout: int = DEFAULT_TOOL_TIMEOUT) -> dict:
    """DeepTutor `MCPServerConfig` 形状的一条 streamableHttp 服务定义。

    字段与 `deeptutor.services.mcp.config.MCPServerConfig` 对齐；`enabled_tools`
    给共享工具（读 + 内容写，D19），**不含**索引维护 `RESTRICTED_TOOLS`。
    """
    return {
        "type": "streamableHttp",
        "url": url,
        "tool_timeout": tool_timeout,
        "enabled_tools": list(SHARED_TOOLS),
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


def build_embedding_profile(url: str, *, dimension: int = EMBEDDING_DIMENSION,
                            model_name: str = EMBEDDING_MODEL_NAME) -> dict:
    """DeepTutor `model_catalog.json` 里的一条 embedding profile（#43）。

    `binding="vllm"` = OpenAI 兼容 + local（免 api_key），`base_url` 指向共享 daemon
    的 `/v1/embeddings`。字段与 DeepTutor 的 llm profile 形状对齐。
    """
    return {
        "id": EMBEDDING_PROFILE_ID,
        "name": "Local BGE-M3 (memory-agent daemon)",
        "binding": EMBEDDING_BINDING,
        "base_url": url,
        "api_key": "",
        "api_version": "",
        "extra_headers": {},
        "models": [{
            "id": EMBEDDING_MODEL_ID,
            "name": "bge-m3",
            "model": model_name,
            "dimension": dimension,
        }],
        "api_format": "auto",
        "wire_api": "auto",
    }


def merge_embedding_catalog(existing: object | None, profile: dict, *,
                            profile_id: str = EMBEDDING_PROFILE_ID,
                            model_id: str = EMBEDDING_MODEL_ID) -> tuple[dict, bool]:
    """把 embedding profile 合并进 DeepTutor 的 `model_catalog.json` 并设为 active。

    保留其它服务（尤其 llm profile 与其 api_key）与未知键；已有同 id profile 时
    **只覆盖我们写的键**。返回 `(新目录, changed)`（幂等）。
    """
    catalog = dict(existing) if isinstance(existing, dict) else {}
    services = catalog.get("services")
    services = dict(services) if isinstance(services, dict) else {}
    embedding = services.get("embedding")
    embedding = dict(embedding) if isinstance(embedding, dict) else {}

    raw_profiles = embedding.get("profiles")
    profiles = [p for p in raw_profiles if isinstance(p, dict)] \
        if isinstance(raw_profiles, list) else []
    previous = next((p for p in profiles if p.get("id") == profile_id), None)
    merged = {**(previous or {}), **profile}

    new_profiles: list[dict] = []
    for item in profiles:
        new_profiles.append(merged if item.get("id") == profile_id else item)
    if previous is None:
        new_profiles.append(merged)

    changed = (
        previous != merged
        or embedding.get("active_profile_id") != profile_id
        or embedding.get("active_model_id") != model_id
        or new_profiles != profiles
    )
    embedding["profiles"] = new_profiles
    embedding["active_profile_id"] = profile_id
    embedding["active_model_id"] = model_id
    services["embedding"] = embedding
    catalog["services"] = services
    return catalog, changed


# --------------------------------------------------------------------- 核验

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
    patch = {"mcp": {SERVER_NAME: build_opencode_entry()}}
    return {"ok": ok, "detail": detail, "patch": patch}


# --------------------------------------------------------------------- CLI

def _print_plan(report: dict) -> None:
    print("== 一步安装：第二消费者 (DeepTutor) → 共享 daemon ==")
    print(f"   DeepTutor home : {report['deeptutor_home']}")
    print(f"   MCP 配置       : {report['mcp_config_path']} "
          f"[{'需写入' if report['mcp_changed'] else '已是最新'}]")
    print(f"   daemon URL     : {report['url']}")
    print(f"   共享工具       : {', '.join(SHARED_TOOLS)}")
    print(f"   不开放         : {', '.join(RESTRICTED_TOOLS)}（维护 / 收录 DDL）")
    if report["embedding"]:
        print(f"   embedding 端点 : {report['embeddings_url']}")
        print(f"   模型目录       : {report['model_catalog_path']} "
              f"[{'需写入' if report['catalog_changed'] else '已是最新'}]")
    else:
        print("   embedding 绑定 : 跳过（--no-embedding）")
    print(f"   opencode 注册  : {report['opencode']['detail']}")
    print(f"   skill 落位     : {report['skill_dir']} [{report['skill_action']}]")
    if not report["opencode"]["ok"]:
        print("\n   opencode 未注册 / 形状不符 —— 请手动合并下面这段到 "
              f"{report['opencode_config_path']}（安装器不擅自改用户配置）：")
        print(json.dumps(report["opencode"]["patch"], ensure_ascii=False, indent=2))


def run(*, deeptutor_home: str | None = None, url: str | None = None,
        embeddings_url: str | None = None, opencode_home: str | None = None,
        dry_run: bool = False, ensure: bool = True, embedding: bool = True,
        skill_source: str | None = None) -> int:
    home = resolve_deeptutor_home(deeptutor_home)
    mcp_path = deeptutor_mcp_config_path(home)
    url = url or daemon_url()
    embeddings_url = embeddings_url or daemon_embeddings_url()

    existing = read_json_object(mcp_path)
    config, changed = merge_mcp_config(existing, SERVER_NAME, build_server_entry(url))

    model_catalog_path = deeptutor_model_catalog_path(home)
    profile = build_embedding_profile(embeddings_url)
    catalog, catalog_changed = merge_embedding_catalog(
        read_json_object(model_catalog_path), profile)

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
        "embedding": embedding,
        "embeddings_url": embeddings_url,
        "model_catalog_path": model_catalog_path,
        "catalog_changed": catalog_changed,
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
        if embedding:
            if catalog_changed:
                atomic_write_json(model_catalog_path, catalog)
                print(f"[写入] {model_catalog_path}（embedding → BGE-M3）")
            else:
                print("[跳过] DeepTutor 模型目录已是最新（幂等）")
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

    print("\n结论：PASS（DeepTutor 已登记共享 daemon；embedding 绑定见上）")
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
    parser.add_argument("--embeddings-url", default=None,
                        help="共享 daemon 的 embeddings URL（缺省 http://127.0.0.1:8765/v1/embeddings）")
    parser.add_argument("--opencode-home", default=None,
                        help="opencode 配置所在用户目录（缺省：当前用户 home；测试用）")
    parser.add_argument("--skill-source", default=None,
                        help="skill 源目录（缺省：本包 skill/；测试用）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只预览将要写入的内容，不落盘、不起 daemon")
    parser.add_argument("--no-daemon", action="store_true",
                        help="只写配置 / 核验，不确保 daemon 在跑")
    parser.add_argument("--no-embedding", action="store_true",
                        help="不写 DeepTutor 的 embedding profile（只接 MCP）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return run(
        deeptutor_home=args.deeptutor_home,
        url=args.url,
        embeddings_url=args.embeddings_url,
        opencode_home=args.opencode_home,
        dry_run=args.dry_run,
        ensure=not args.no_daemon,
        embedding=not args.no_embedding,
        skill_source=args.skill_source,
    )


if __name__ == "__main__":
    raise SystemExit(main())
