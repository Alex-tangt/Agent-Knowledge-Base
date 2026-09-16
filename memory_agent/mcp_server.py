"""memory-agent MCP server：读路径最小闭环（issue #10）+ 写路径（#11/#12）+ 索引维护（#13）。

暴露 memory_search / memory_get / memory_add / memory_supersede / memory_archive /
memory_reindex / memory_index_status。真相源是 Markdown；索引是派生物。破坏性变更
（supersede / archive）需 confirm=true。

两种传输（issue #19）：
- `stdio`（默认）：每个 opencode 会话各拉起一份进程——简单，但每份各加载一份嵌入模型。
- `http`：`--transport http --host 127.0.0.1 --port 8765` 起一个常驻单实例，多个会话经
  `proxy.py`（stdio↔HTTP 转发）共享同一份模型（约 3.9GB 只付一次）。HTTP 模式默认
  eager 预热（常驻才有意义），并用 DNS-rebinding 防护把 Host 限到本机。
"""
from __future__ import annotations

import argparse
import asyncio
import atexit
import os
import threading

from memory_agent.runtime import (  # noqa: E402  (导入即配 stderr 日志)
    get_admission,
    get_index,
    get_writer,
    reindex,
)
from memory_agent.gateway import (  # noqa: E402
    AuditLog,
    AuthenticationError,
    AuthorizationError,
    GatewayAuthnMiddleware,
    Identity,
    can_read,
    can_write,
    current_identity,
    effective_filter,
    load_auth_config,
    resolve_identity,
)
from memory_agent.memory.admission import AdmissionError  # noqa: E402
from memory_agent.memory.errors import IndexConsistencyError  # noqa: E402
from memory_agent.memory.writer import MemoryWriteError  # noqa: E402
from memory_agent.settings import (  # noqa: E402
    AUDIT_LOG,
    MCP_HTTP_HOST,
    MCP_HTTP_PATH,
    MCP_HTTP_PORT,
    daemon_pid_path,
    warmup_on_start,
)

from mcp.server import MCPServer  # noqa: E402
# 工具里的「预期失败」必须 raise ToolError，不能 raise ValueError：后者被 SDK 当崩溃，
# 客户端只看到 `Error executing tool <name>`，写入网关那些面向调用方的提示（去重候选 /
# 校验失败 / 只读拒写）全被吞掉。#17 验收时实测并修正。
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from ragcore.config.config import LOCAL_EMBEDDING_MODEL  # noqa: E402
from ragcore.services.embedding_provider import get_local_embedding_service  # noqa: E402
from ragcore.utils.logger import logger  # noqa: E402

DEFAULT_HOST = MCP_HTTP_HOST
DEFAULT_PORT = MCP_HTTP_PORT
DEFAULT_PATH = MCP_HTTP_PATH

audit_log = AuditLog(path=AUDIT_LOG)

mcp = MCPServer(
    "memory-agent",
    version="0.1.0",
    middleware=[GatewayAuthnMiddleware(load_auth_config(), audit=audit_log)],
    instructions=(
        "Agent 长期记忆：memory_search 语义检索条目，memory_get 读回真实 Markdown，"
        "memory_add 写入新条目（先去重，命中近似则不写并返回候选）；"
        "memory_supersede 替代旧条目（新旧双向标注），memory_archive 只标记退役、不删文件。"
        "memory_search 的 exclude_retired=True 会排除已退役条目（取新弃旧）；默认 False。"
        "supersede / archive 是破坏性变更，先看 preview，再以 confirm=true 重试。"
        "收录（哪些只读文件进基表）走 memory_ingest_list / memory_ingest_include /"
        "memory_ingest_exclude：改 overlay 免重启，移除默认只预览、confirm 才生效。"
        "写入只落真相源，索引由下一次 memory_search 的指纹检查惰性追平（D13）；"
        "索引不自洽时用 memory_reindex 分块全量重建（拿 cursor 续调到 done=true），"
        "memory_index_status 查当前代与自洽性。"
        "只读语料（writable=false）不可写入；没有裸文件写工具。"
    ),
)


@mcp.custom_route("/health", methods=["GET"])
async def _health(_request):
    """就绪/存活探测：HTTP 模式下 proxy 与运维脚本用它判断 daemon 是否在跑。"""
    return JSONResponse({"status": "ok", "service": "memory-agent"})


def _embedding_error(message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": "invalid_request_error"}},
        status_code=400,
    )


@mcp.custom_route("/v1/embeddings", methods=["POST"])
async def _embeddings(request):
    """OpenAI 兼容 embeddings 端点（#43）：把守护进程里那份 BGE-M3 暴露给同机消费者。

    复用 `get_local_embedding_service()`——与索引 store **同一个实例**（不新增模型副本）。
    authn 与 `/mcp` 同源（ADR-0018 D2）：未配 token 时零配置直连；配了 token 则要 Bearer。
    只支持 `input`（string | [string...]）与 float 向量。
    """
    try:
        resolve_identity(request.headers.get("authorization"), load_auth_config())
    except AuthenticationError as exc:
        return JSONResponse(
            {"error": {"message": str(exc), "type": "authentication_error"}},
            status_code=401,
        )
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - 非法 JSON 由调用方负责
        return _embedding_error("请求体不是合法 JSON")
    if not isinstance(payload, dict):
        return _embedding_error("请求体应为 JSON 对象")
    raw_input = payload.get("input")
    if isinstance(raw_input, str):
        texts = [raw_input]
    elif (isinstance(raw_input, list) and raw_input
          and all(isinstance(item, str) for item in raw_input)):
        texts = list(raw_input)
    else:
        return _embedding_error("`input` 应为字符串或非空字符串数组")
    if payload.get("encoding_format") not in (None, "", "float"):
        return _embedding_error("仅支持 float 向量（encoding_format 省略或 float）")

    model = str(payload.get("model") or LOCAL_EMBEDDING_MODEL)
    service = get_local_embedding_service()
    vectors = await asyncio.to_thread(service.embed_documents, texts)
    data = [
        {"object": "embedding", "index": index, "embedding": vector}
        for index, vector in enumerate(vectors)
    ]
    return JSONResponse({
        "object": "list",
        "data": data,
        "model": model,
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    })


def _require_writer() -> Identity:
    """写 / 维护类工具的角色门槛。"""
    identity = current_identity()
    if not can_write(identity):
        raise ToolError(
            f"角色 {identity.role!r} 无写入权限（需要 owner / admin / writer）"
        )
    return identity


def _require_visible(identity: Identity, entry_id: str) -> None:
    """生命周期工具的越权预检：目标条目必须在身份授权范围内。"""
    try:
        entry = get_index().get(entry_id)
    except (KeyError, FileNotFoundError):
        return  # 未知 / 孤儿条目交给后续写入网关报错
    if not can_read(identity, entry):
        raise ToolError(f"条目 {entry_id} 不在当前身份的授权范围内")


def _resolve_section(section: str | None, domain: str | None, *, tool: str) -> str:
    """解析分区参数（#45 / ADR-0025 D19）：正名 `section`，`domain` 为弃用别名。

    二者语义相同（全局知识库内的目录分区），只允许给一个；都不给或相互冲突即报错。
    内部（frontmatter / 写入器 / 文件布局）仍沿用历史字段名 `domain`——本轮只改工具面。
    """
    section = (section or "").strip()
    domain = (domain or "").strip()
    if section and domain and section != domain:
        raise ToolError(
            f"{tool}: `section` 与已弃用的别名 `domain` 同时给出且不一致；"
            f"请只用 `section`"
        )
    resolved = section or domain
    if not resolved:
        raise ToolError(
            f"{tool}: 缺少 `section`（topics | decisions | projects/<slug>）"
        )
    return resolved


@mcp.tool()
def memory_search(query: str, k: int = 5, writable_only: bool = False,
                  payload_filter: dict | None = None,
                  exclude_retired: bool = False) -> list[dict]:
    """语义检索长期记忆条目（可写 KB 记忆 + 只读项目语料）。

    返回条目级命中：id / title / source / writable / type / tags / status / owner /
    score / snippet。`owner` 是域所有者（#45 / ADR-0025 D19：读侧域可见；只读条目 =
    来源 label）。score 越大越相关：检索链路是向量 + 关键词混合召回（关键词命中批次
    分数 >1，其余为余弦相似度 ∈[-1,1]）。用 memory_get(id) 读回完整 Markdown。
    writable=false 的是只读参考语料，不可写入。

    `exclude_retired`（#42 / ADR-0025 D19）：置 True 时排除
    `status ∈ {superseded, archived}` 的已退役条目（**取新弃旧**），保留
    `current` / `draft` / 无 status（只读语料常无 status，不会被误伤）。默认 False
    （不静默改行为）；回答依赖持久事实时建议显式开启。

    `payload_filter`（可选）只用于**进一步收窄**——网关会按当前身份无条件注入
    tenant / classification / residency 约束；试图放宽（越权）会被拒绝。
    """
    identity = current_identity()
    try:
        scoped = effective_filter(identity, payload_filter)
    except AuthorizationError as exc:
        raise ToolError(str(exc))
    return get_index().search(query, k=k, writable_only=writable_only,
                              payload_filter=scoped, exclude_retired=exclude_retired)


@mcp.tool()
def memory_get(entry_id: str) -> dict:
    """按 memory_search 返回的 id，读回该条目的真实 Markdown 内容与元数据。"""
    identity = current_identity()
    try:
        entry = get_index().get(entry_id)
    except KeyError:
        raise ToolError(f"未知条目 id：{entry_id}（先用 memory_search 取 id）")
    except FileNotFoundError as exc:
        raise ToolError(f"条目文件已不存在（索引孤儿，需重建）：{exc}")
    if not can_read(identity, entry):
        raise ToolError(f"条目 {entry_id} 不在当前身份的授权范围内")
    return entry


@mcp.tool()
def memory_add(
    title: str,
    body: str,
    type: str,
    tags: list[str],
    *,
    section: str | None = None,
    slug: str | None = None,
    sources: list[str] | None = None,
    status: str = "current",
    allow_duplicate: bool = False,
    domain: str | None = None,
) -> dict:
    """新增一条长期记忆（唯一写入口：不覆盖、不删除、不原地编辑已有条目）。

    写入前先去重——命中近似条目时**不写**，返回候选 id/score，交由调用方决定
    （确认不同则 allow_duplicate=true 重试；要替换则用 memory_supersede, #12）。
    写入 = frontmatter 过 KB 校验 + 一个新 git commit（只含本条目文件）。

    - section: topics | decisions | projects/<slug>（全局知识库内的分区）
    - type: topic | decision | research | project-knowledge（须与 section 匹配）
    - tags: 取 tags.md 的受控标签
    - slug: 英文 slug；省略时从 title 派生（纯中文标题请显式给）
    - domain: **已弃用别名**，等价于 `section`（#45 / ADR-0025 D19）；只给一个
    """
    resolved_section = _resolve_section(section, domain, tool="memory_add")
    identity = _require_writer()
    try:
        return get_writer().add(
            title=title, body=body, domain=resolved_section, type=type, tags=tags,
            slug=slug, sources=sources, status=status,
            allow_duplicate=allow_duplicate,
            payload_filter=effective_filter(identity),
        )
    except AuthorizationError as exc:
        raise ToolError(str(exc))
    except MemoryWriteError as exc:
        raise ToolError(str(exc))


@mcp.tool()
def memory_supersede(
    old_id: str,
    title: str,
    body: str,
    type: str,
    tags: list[str],
    *,
    section: str | None = None,
    slug: str | None = None,
    sources: list[str] | None = None,
    confirm: bool = False,
    domain: str | None = None,
) -> dict:
    """用新条目替代一条旧记忆（破坏性：旧条目被标注退役，但**文件保留**）。

    confirm=False（默认）不落盘，只返回 `{status:"confirmation_required", preview}`；
    调用方必须先把它给用户看、得到明确同意，再以 confirm=True 重试。
    落盘 = 新条目（frontmatter 带 supersedes=<old_id>）+ 旧条目改
    status: superseded / superseded_by=<new_id>，两个文件在同一个 commit 里。

    `section` 是全局知识库内的分区（正名）；`domain` 是**已弃用别名**（#45 / D19）。
    """
    resolved_section = _resolve_section(section, domain, tool="memory_supersede")
    identity = _require_writer()
    _require_visible(identity, old_id)
    try:
        return get_writer().supersede(
            old_id=old_id, title=title, body=body, domain=resolved_section, type=type,
            tags=tags, slug=slug, sources=sources, confirm=confirm,
        )
    except MemoryWriteError as exc:
        raise ToolError(str(exc))


@mcp.tool()
def memory_archive(entry_id: str, reason: str, confirm: bool = False) -> dict:
    """把一条记忆标记退役（破坏性：改 status 为 archived，**文件永不删除**）。

    `reason` 会写进 frontmatter 的 archive_reason 字段（归档必须留下为什么）。
    confirm=False（默认）不落盘，只返回 `{status:"confirmation_required", preview}`；
    得到用户明确同意后，再以 confirm=True 重试。
    """
    identity = _require_writer()
    _require_visible(identity, entry_id)
    try:
        return get_writer().archive(entry_id=entry_id, reason=reason, confirm=confirm)
    except MemoryWriteError as exc:
        raise ToolError(str(exc))


@mcp.tool()
def memory_ingest_list() -> dict:
    """列出当前收录（基表 extent）：注册表默认来源 + 显式 overlay + 逐文件解析结果。

    这是**只读**的收录全景：`sources` 里 `origin=default` 是来源注册表默认，
    `origin=explicit` 是 overlay 显式收录；`overlay` 给出 include/exclude 清单；
    `resolved` 是实际选中的只读文件（`origin` 逐条区分默认 / 显式）。
    """
    return get_admission().list()


@mcp.tool()
def memory_ingest_include(pattern: str, owner: str | None = None,
                          label: str | None = None, confirm: bool = False) -> dict:
    """把一条路径模式加进 overlay 收录清单（**DDL，不是写记忆**）。

    - `pattern`：精确文件路径或窄 glob（收整棵子树请再配 `memory_ingest_exclude`）。
    - `owner` / `label`：显式收录的域与来源标签；省略时 owner 默认按来源继承。
    - confirm=false（默认）只返回匹配到的文件预览，不落盘；confirm=true 才写 overlay。
    新增收录不改动已有条目；下次 `memory_search` 的惰性刷新会纳入新文件。
    """
    identity = _require_writer()
    try:
        return get_admission().include(
            pattern=pattern, owner=owner, label=label, identity=identity, confirm=confirm,
        )
    except AdmissionError as exc:
        raise ToolError(str(exc))


@mcp.tool()
def memory_ingest_exclude(pattern: str, confirm: bool = False) -> dict:
    """把一条路径模式加进 overlay 的 exclude，从收录范围移除（收窄 DDL）。

    移除会让命中的条目离开收录范围，下一次惰性刷新将删除其派生点（**真相源文件
    不动**）——因此默认只返回预览，确认后以 confirm=true 重试；不会误删他人条目。
    """
    identity = _require_writer()
    try:
        return get_admission().exclude(pattern=pattern, identity=identity, confirm=confirm)
    except AdmissionError as exc:
        raise ToolError(str(exc))


@mcp.tool()
def memory_reindex(cursor: dict | None = None, batch: int = 16) -> dict:
    """分块全量重建派生索引（从 Markdown 真相源恢复；新代 + 原子切指针）。

    单次调用只嵌入 batch 条（默认 16），避免超过 MCP 调用超时。`cursor=None` 开始
    新一轮，返回 `{done,total,processed,cursor,gen}`；拿返回的 `cursor` 原样续调，
    直到 `done=true`（此时指针已切换，新索引生效）。
    中断安全：完成前指针不动，旧索引继续服务；核对 manifest 条数 == 点数，不等则报错。
    """
    _require_writer()
    try:
        return reindex(cursor=cursor, batch=batch)
    except IndexConsistencyError as exc:
        raise ToolError(str(exc))


@mcp.tool()
def memory_index_status() -> dict:
    """查当前索引代：`{built, gen, entries, points, consistent, built_at, path}`。

    `consistent=false` 表示 manifest 条数 ≠ 集合点数（需 memory_reindex 重建）。
    """
    return get_index().status()


def _warmup() -> None:
    try:
        index = get_index()
        index.store.warmup()
        logger.info("memory index warmup done")
    except Exception as exc:  # noqa: BLE001 - 预热失败不阻断服务
        logger.warning(f"memory index warmup failed: {exc}")


def _start_warmup(*, enabled: bool) -> bool:
    """按需启动预热线程，返回是否真的启动了。

    默认不启动（issue #19）：BGE-M3 约 3.9GB 私有内存，而且每个 opencode 会话都会拉起
    一份 MCP——无条件预热 = 每会话白付 3.9GB。改为首次真正检索时才加载。

    但 **HTTP 常驻单实例**例外：全局只有一份，eager 预热才不冷启动（由 `--warmup` /
    `--transport http` 决定，见 `_resolve_warmup`）。
    """
    if not enabled:
        logger.info("skip warmup：惰性加载，首次检索时才载入嵌入模型（#19）")
        return False
    threading.Thread(target=_warmup, name="memory-warmup", daemon=True).start()
    return True


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="memory-agent MCP server")
    parser.add_argument(
        "--transport", choices=["stdio", "http"], default="stdio",
        help="stdio（默认，每会话一进程）或 http（常驻单实例，供 proxy 转发）",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="HTTP 绑定地址（默认仅本机）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--path", default=DEFAULT_PATH, help="streamable-http 端点路径")
    warm = parser.add_mutually_exclusive_group()
    warm.add_argument("--warmup", dest="warmup", action="store_true", default=None,
                      help="启动即加载嵌入模型")
    warm.add_argument("--no-warmup", dest="warmup", action="store_false", default=None,
                      help="惰性加载（首次检索才载入）")
    return parser


def _resolve_warmup(args: argparse.Namespace) -> bool:
    """预热决策：显式 flag > HTTP 模式默认 eager > 环境变量（stdio 默认惰性）。"""
    if args.warmup is not None:
        return args.warmup
    if args.transport == "http":
        return True
    return warmup_on_start()


def _transport_security(host: str, port: int) -> TransportSecuritySettings:
    """把 Host 限到本机，挡浏览器 DNS rebinding（SDK 该路径默认关闭该防护）。"""
    allowed = [f"{host}:{port}"]
    for loopback in ("127.0.0.1", "localhost"):
        entry = f"{loopback}:{port}"
        if entry not in allowed:
            allowed.append(entry)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=allowed,
    )


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    _start_warmup(enabled=_resolve_warmup(args))

    if args.transport == "http":
        logger.info(
            f"memory-agent HTTP daemon: http://{args.host}:{args.port}{args.path} "
            f"(health: /health, warmup: {_resolve_warmup(args)})"
        )
        _write_pid_file(args.port)
        mcp.run(
            transport="streamable-http",
            host=args.host,
            port=args.port,
            streamable_http_path=args.path,
            transport_security=_transport_security(args.host, args.port),
        )
        return

    mcp.run(transport="stdio")


def _write_pid_file(port: int) -> None:
    """记下 daemon PID，供 `proxy.py --stop` 手动停止（不搞自动空闲卸载）。"""
    path = daemon_pid_path(port)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        atexit.register(_remove_pid_file, path)
    except OSError as exc:  # noqa: BLE001 - PID 文件失败不该挡住服务
        logger.warning(f"无法写 daemon PID 文件 {path}: {exc}")


def _remove_pid_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


if __name__ == "__main__":
    main()
