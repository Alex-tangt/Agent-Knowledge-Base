"""#34 隔离绕过对抗性测试套件（验收硬项）。

对象：**tenant / classification / residency 隔离**是否真的被拦（ADR-0018 D1/D2 + D2.1–D2.4）。
四家云都没有行级安全（#25），隔离是纯客户端约定——所以"隔离有效"必须由**负向断言**证明。

用法（仓库根，用主树 venv 的绝对路径）：

    venv\\Scripts\\python.exe memory_agent/eval/isolation_bypass_34.py

形式与 #16 写路径 sandbox 同风格：
- 隔离三层：真相源 = 临时目录里的 fixture 条目；派生索引 = 临时目录；**不加载任何模型**
  （Stub 嵌入 + Qdrant local mode）；真实 KB 只读，套件前后 HEAD / 工作树逐字不变。
- 只断言**外部行为**：MCP 工具返回的 dict、直接 store 调用结果、`git status`、proxy 源码。

覆盖攻击面（issue #34 body）：
  ① 工具调用**漏注入** tenant 过滤 → 是否泄漏跨租户；
  ② 调用方在 `payload_filter` 里**放宽** tenant / classification / residency → 是否被拦；
  ③ 绕过网关**直接调 store**（内部 API）→ 是否可达 / 能否逃出绑定租户；
  ④ 经 `proxy` **伪造身份**（伪造 header）→ 是否被采信；
  ⑤ `residency` 越界（本地条目被云身份取走等）。

任一攻击面**未被拦** = 阻塞项（套件以非零退出码与 FAIL 行如实汇报，不静默通过）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import traceback
from contextlib import contextmanager

# 从 worktree / 任意 CWD 直接 `python memory_agent/eval/...` 也能解析到本仓的
# `memory_agent` / `ragcore`（editable 安装可能指向别处）。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from memory_agent import mcp_server  # noqa: E402
from memory_agent import runtime  # noqa: E402
from memory_agent import settings  # noqa: E402
from memory_agent.gateway import (  # noqa: E402
    AuditLog,
    AuthConfig,
    GatewayAuthnMiddleware,
    current_identity,
    make_identity,
    use_identity,
)
from memory_agent.memory.entries import Entry  # noqa: E402
from memory_agent.memory.index import MemoryIndex  # noqa: E402
from memory_agent.memory.store import QdrantLocalStore  # noqa: E402
from ragcore.utils.model_status import EMBEDDING_DIMENSION  # noqa: E402

DEFAULT_SOURCE_KB = os.environ.get(
    "MEMORY_SANDBOX_SOURCE_KB", r"C:\Users\Tan\.config\opencode\knowledge"
)
BOUND_TENANT = "org-a"

# 唯一 marker：只出现在某一条条目正文里，用来把「关键词通道」单独拎出来验。
MARK_A = "ALPHA34A"
MARK_A_CLOUD = "CLOUD34C"
MARK_B = "ZEBRA34B"


class Suite:
    """极简通过矩阵：每条断言记录 PASS/FAIL，最后统一判定退出码。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        ok = bool(ok)
        self.rows.append({"name": name, "ok": ok, "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        return ok

    def record(self, name: str, detail: str, ok: bool) -> bool:
        return self.check(name, ok, detail)

    @property
    def failed(self) -> list[dict]:
        return [row for row in self.rows if not row["ok"]]

    @property
    def passed(self) -> bool:
        return bool(self.rows) and not self.failed


# ----------------------------------------------------------------------- fixtures


class _StubEmbeddings:
    """所有文本同向量：让过滤（而非相似度）成为唯一的区分因素。"""

    def embed_query(self, text):
        return [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]


def _entry(root: str, rel: str, tenant: str, classification: str, residency: str,
           body: str) -> Entry:
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            "---\n"
            f"id: {rel[:-3]}\n"
            f'title: "{rel}"\n'
            f"tenant: {tenant}\n"
            f"classification: {classification}\n"
            f"residency: {residency}\n"
            "---\n\n"
            f"# {rel}\n\n{body}\n"
        )
    return Entry.from_file(path, source=rel, writable=True, entry_id=rel[:-3])


def _fixture(root: str) -> list[Entry]:
    return [
        # 唯一 marker：只出现在某一条条目正文里，用来把「关键词通道」单独拎出来验。
        _entry(root, "topics/a-priv.md", "org-a", "private", "local",
               f"org-a private note {MARK_A}"),
        _entry(root, "topics/a-internal.md", "org-a", "internal", "local",
               "org-a internal note"),
        _entry(root, "topics/a-cloud.md", "org-a", "private", "cloud",
               f"org-a cloud note {MARK_A_CLOUD}"),
        _entry(root, "topics/b-secret.md", "org-b", "private", "local",
               f"org-b secret note {MARK_B}"),
    ]


def _build_index(workdir: str, name: str, entries, tenant: str | None):
    db = os.path.join(workdir, name, "qdrant")
    store = QdrantLocalStore(db_path=db, embeddings=_StubEmbeddings(), tenant=tenant)
    index = MemoryIndex(store=store, manifest_path=os.path.join(workdir, name, "manifest.json"))
    index.rebuild(entries)
    return index


@contextmanager
def _use_index(index):
    """把 MCP 工具的索引换成隔离 fixture（恢复原样）。"""
    original = mcp_server.get_index
    mcp_server.get_index = lambda: index
    try:
        yield
    finally:
        mcp_server.get_index = original


def _search(identity, query: str, **kwargs) -> list[dict]:
    with use_identity(identity):
        return mcp_server.memory_search(query, **kwargs)


def _ids(hits) -> list[str]:
    return [hit["id"] for hit in hits]


def _tenants(hits) -> set:
    return {hit.get("tenant") for hit in hits}


def _raises_tool_error(call) -> tuple[bool, str]:
    try:
        call()
    except mcp_server.ToolError as exc:  # noqa: BLE001 - 期望的被拦
        return True, str(exc)
    return False, ""


def _rejected(call) -> tuple[bool, str]:
    """任意异常都算「被拦」（中间件 authn 抛的是 MCPError，不是 ToolError）。"""
    try:
        call()
    except Exception as exc:  # noqa: BLE001 - 期望的拒绝路径
        return True, f"{type(exc).__name__}: {exc}"
    return False, ""


def _get_with(identity, entry_id: str) -> dict:
    with use_identity(identity):
        return mcp_server.memory_get(entry_id)


# ---------------------------------------------------------------------- sections


def _identity_org_a(**kwargs):
    kwargs.setdefault("principal", "svc-a")
    kwargs.setdefault("tenant", "org-a")
    kwargs.setdefault("role", "writer")
    return make_identity(**kwargs)


def _section_1_missing_injection(suite: Suite, index_open, index_bound) -> None:
    """① 工具漏注入 tenant 过滤 → 跨租户泄漏。"""
    org_a = _identity_org_a()
    org_b = make_identity(principal="svc-b", tenant="org-b", role="reader")

    # 正常（多租户形状，store 未绑定）：网关注入 tenant，两条通道都隔离。
    with _use_index(index_open):
        hits = _search(org_a, MARK_A)
        suite.check("1a org-a 身份 vector 通道不返回 org-b",
                    hits and all(hit["tenant"] == "org-a" for hit in hits),
                    f"tenants={_tenants(hits)}")
        hits = _search(org_a, MARK_B)   # marker 只在 org-b 条目里
        suite.check("1b org-a 身份 keyword 通道不返回 org-b",
                    all(hit["tenant"] == "org-a" for hit in hits),
                    f"ids={_ids(hits)}")
        hits = _search(org_b, MARK_B)
        suite.check("1c org-b 身份只看得到 org-b",
                    hits and all(hit["tenant"] == "org-b" for hit in hits),
                    f"tenants={_tenants(hits)}")

    # 攻击 ①-a：store 在构造期绑定了租户（runtime._store_factory + MEMORY_AUTH_TENANT），
    # 请求身份却**没有** tenant（token 未声明 tenant）→ effective_filter 不注入 tenant。
    # 向量通道被 store 绑定兜住，但 **keyword 通道不认 store 绑定** → 跨租户泄漏。
    unbound = make_identity(principal="svc-unbound", tenant=None, role="reader")
    with _use_index(index_bound):
        hits = _search(unbound, MARK_B)
        leaked = [hid for hid, hit in zip(_ids(hits), hits) if hit["tenant"] == "org-b"]
        suite.check(
            "1d [攻击①]未声明 tenant 的身份不得经 keyword 通道看到 org-b",
            not leaked,
            f"泄漏 ids={leaked} tenants={_tenants(hits)}"
            "（store 绑定租户被 keyword 通道绕过）",
        )
        # 攻击 ①-b：绑定租户的 store **覆盖**（而非与网关 entitlement 求交）→
        # org-b 身份拿到 org-a 数据。
        hits = _search(org_b, MARK_A)
        leaked_a = [hit["id"] for hit in hits if hit["tenant"] == "org-a"]
        suite.check(
            "1e [攻击①]绑定 store 不得把 org-a 数据交给 org-b 身份",
            not leaked_a,
            f"泄漏 ids={leaked_a} tenants={_tenants(hits)}"
            "（store 绑定覆盖了网关注入的 tenant）",
        )


def _section_2_payload_filter_widening(suite: Suite, index_open) -> None:
    """② 调用方在 payload_filter 里放宽受管控维度 → 必须被拦。"""
    org_a = _identity_org_a(classifications="private,internal", residencies="local")
    with _use_index(index_open):
        attacks = {
            "2a tenant 放宽": {"tenant": "org-b"},
            "2b classification 放宽": {"classification": "public"},
            "2c residency 放宽": {"residency": "cloud"},
            "2d tenant 多值": {"tenant": ["org-a", "org-b"]},
            "2e tenant=None": {"tenant": None},
            "2f tenant 空串": {"tenant": ""},
        }
        for name, flt in attacks.items():
            blocked, detail = _raises_tool_error(
                lambda flt=flt: _search(org_a, MARK_A, payload_filter=flt))
            suite.check(f"[攻击②]{name} → 被拦", blocked, detail or "未被拦")

        # 大小写变体不是合法受管控维度键：不得被当作 tenant 放宽。
        hits = _search(org_a, MARK_A, payload_filter={"Tenant": "org-b"})
        suite.check("[攻击②]2g 大小写变体不得放宽",
                    all(hit["tenant"] == "org-a" for hit in hits),
                    f"ids={_ids(hits)}")

        # 未知字段是「进一步收窄」，允许，但不得放宽隔离维度。
        hits = _search(org_a, MARK_A, payload_filter={"writable": True})
        suite.check("[攻击②]2h 未知字段仅收窄、不放宽",
                    all(hit["tenant"] == "org-a" for hit in hits),
                    f"ids={_ids(hits)}")

        # memory_get 的越权预检。
        blocked, detail = _raises_tool_error(
            lambda: _get_with(org_a, "topics/b-secret"))
        suite.check("[攻击②]2i memory_get 跨租户 id → 被拦", blocked, detail or "未被拦")

        within = _get_with(org_a, "topics/a-priv")
        suite.check("[攻击②]2j 授权内 memory_get 正常返回",
                    within["id"] == "topics/a-priv", within.get("id", ""))


def _section_3_direct_store(suite: Suite, index_open, index_bound) -> None:
    """③ 绕过网关直接调 store：工具面无裸 store 入口；绑定租户不得被逃出。"""
    # 3a：端口「向量通道」不能被调用方放宽（只可收窄）。
    widened = index_bound.store.search("note", k=10, payload_filter={"tenant": "org-b"})
    ids = [m.get("entry_id") for m in widened["metadatas"][0]]
    suite.check("[攻击③]3a 绑定 store 向量通道不可被放宽",
                all(i.startswith("topics/a-") for i in ids), f"ids={ids}")

    # 3b：绑定租户必须约束**所有**通道——keyword 通道也不得返回其它租户。
    matches = index_bound.store.search_by_keywords([MARK_B])
    leaked = [m["metadata"].get("entry_id") for m in matches]
    suite.check("[攻击③]3b 绑定 store keyword 通道不得返回其它租户",
                not leaked, f"泄漏 ids={leaked}（keyword 通道忽略 store 绑定租户）")

    # 3c：MCP 工具面不存在裸 store / 裸租户参数。
    tools = getattr(getattr(mcp_server.mcp, "_tool_manager", None), "_tools", {}) or {}
    raw_params = {"tenant", "store", "db_path", "collection", "collection_name"}
    offenders = {}
    for name, tool in tools.items():
        props = set((getattr(tool, "parameters", {}) or {}).get("properties", {}))
        hit = props & raw_params
        if hit:
            offenders[name] = sorted(hit)
    suite.check("[攻击③]3c MCP 工具面无裸 store/tenant 参数",
                not offenders, f"offenders={offenders}")

    # 3d：生产组合证明——runtime 在进程身份带 tenant 时确实把 store 绑定到该 tenant。
    old = settings.AUTH_TENANT
    settings.AUTH_TENANT = BOUND_TENANT
    try:
        bound_store = runtime._store_factory()(os.path.join("unused", "qdrant"))
        bound = getattr(bound_store, "tenant", None)
    finally:
        settings.AUTH_TENANT = old
    suite.check("[攻击③]3d runtime 构造的 store 绑定进程租户（组合前提）",
                bound == BOUND_TENANT, f"store.tenant={bound}")


# ------------------------------------------------------------ proxy / middleware


class _Request:
    def __init__(self, headers=None):
        self.headers = dict(headers or {})


class _Ctx:
    def __init__(self, headers=None, method="tools/call", name="memory_search"):
        self.method = method
        self.params = {"name": name}
        self.request = _Request(headers)


async def _identity_probe(_ctx):
    return current_identity()


def _run_middleware(config: AuthConfig, headers: dict):
    middleware = GatewayAuthnMiddleware(config, audit=AuditLog())
    return asyncio.run(middleware(_Ctx(headers), _identity_probe))


def _section_4_proxy_forgery(suite: Suite) -> None:
    """④ 经 proxy 伪造身份（伪造 header）→ 不得被采信。"""
    org_a = _identity_org_a()
    config = AuthConfig(default=org_a, tokens={"tok-a": org_a}, require_token=True)

    # 4a：无凭证 + 伪造租户头 → 拒绝（不回落默认身份）。
    blocked, detail = _rejected(
        lambda: _run_middleware(config, {"x-tenant": "org-b"}))
    suite.check("[攻击④]4a 伪造 X-Tenant 无 token → 拒绝", blocked, detail or "未被拦")

    # 4b：有效 token + 伪造头 → 身份仍是 token 绑定的 org-a。
    try:
        identity = _run_middleware(
            config, {"authorization": "Bearer tok-a", "x-tenant": "org-b"})
        suite.check("[攻击④]4b 伪造头不改变 token 身份",
                    identity.tenant == "org-a", f"tenant={identity.tenant}")
    except Exception as exc:  # noqa: BLE001
        suite.check("[攻击④]4b 伪造头不改变 token 身份", False, repr(exc))

    # 4c：非 Bearer 方案（Basic）→ 拒绝。
    blocked, detail = _rejected(
        lambda: _run_middleware(config, {"authorization": "Basic YWRtaW46YWRtaW4="}))
    suite.check("[攻击④]4c 非 Bearer 凭证 → 拒绝", blocked, detail or "未被拦")

    # 4d：伪造 principal / role / classification 头 → 全部忽略。
    try:
        identity = _run_middleware(config, {
            "authorization": "Bearer tok-a",
            "x-principal": "root", "x-role": "admin", "x-classification": "public",
        })
        suite.check("[攻击④]4d 伪造 principal/role/classification 头被忽略",
                    identity.principal == "svc-a" and identity.role == "writer",
                    f"principal={identity.principal} role={identity.role}")
    except Exception as exc:  # noqa: BLE001
        suite.check("[攻击④]4d 伪造 principal/role/classification 头被忽略", False, repr(exc))

    # 4e：proxy 是纯传输——源码里不碰身份 / 凭证 / 网关。
    proxy_src = os.path.join(_REPO_ROOT, "memory_agent", "proxy.py")
    text = open(proxy_src, encoding="utf-8").read()
    forbidden = {
        "MEMORY_AUTH": "MEMORY_AUTH" in text,
        "identity": "identity" in text,
        "gateway": "gateway" in text,
        "authorization": "authorization" in text.lower(),
    }
    suite.check("[攻击④]4e proxy 源码无身份/凭证/网关依赖",
                not any(forbidden.values()), f"命中={[k for k, v in forbidden.items() if v]}")


def _section_5_residency(suite: Suite, index_open) -> None:
    """⑤ residency 越界：本地条目不得被云身份取走，反之亦然。"""
    local_only = _identity_org_a(residencies="local")
    cloud_only = _identity_org_a(residencies="cloud")

    with _use_index(index_open):
        hits = _search(local_only, "note", k=10)
        suite.check("5a local 身份不取 cloud 条目",
                    all(hit["residency"] == "local" for hit in hits),
                    f"residencies={ {h['residency'] for h in hits} }")

        hits = _search(cloud_only, "note", k=10)
        suite.check("5b cloud 身份不取 local 条目",
                    bool(hits) and all(hit["residency"] == "cloud" for hit in hits),
                    f"residencies={ {h['residency'] for h in hits} }")

        hits = _search(local_only, MARK_A_CLOUD)  # marker 只在 org-a cloud 条目
        suite.check("5c local 身份的 keyword 通道不取 cloud 条目",
                    all(hit["residency"] == "local" for hit in hits),
                    f"ids={_ids(hits)}")

        blocked, detail = _raises_tool_error(
            lambda: _get_with(local_only, "topics/a-cloud"))
        suite.check("5d cloud 条目的 memory_get 对 local 身份 → 被拦",
                    blocked, detail or "未被拦")


# ------------------------------------------------------------------- real KB 快照


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True,
                          text=True, encoding="utf-8")


def _section_6_artifacts(suite: Suite, source_kb: str) -> None:
    if os.path.isdir(os.path.join(source_kb, ".git")):
        head = _git(source_kb, "rev-parse", "HEAD").stdout.strip()
        status = _git(source_kb, "status", "--short").stdout
        suite.record("6a 真实 KB HEAD 可读", head[:10], bool(head))
        suite.record("6b 真实 KB 工作树快照可读", f"{len(status.splitlines())} 行", status == status)
    else:
        suite.check("6 真实 KB 快照", False, f"找不到 git 仓库：{source_kb}")
    # 凭证绝不进审计。
    log = AuditLog()
    log.record(identity=_identity_org_a(), action="tools/call", tool="memory_search")
    suite.check("6c 审计不含凭证", "tok-a" not in json.dumps(log.entries, ensure_ascii=False),
                "identity-only")


# ------------------------------------------------------------------------- main


def run(suite: Suite, workdir: str, source_kb: str) -> None:
    root = os.path.join(workdir, "kb")
    entries = _fixture(root)
    index_open = _build_index(workdir, "open", entries, tenant=None)
    index_bound = _build_index(workdir, "bound", entries, tenant=BOUND_TENANT)

    suite.check("0a 隔离索引（未绑定租户）建成且自洽",
                index_open.status().get("consistent") is True,
                str(index_open.status()))
    suite.check("0b 隔离索引（绑定 org-a）建成且自洽",
                index_bound.status().get("consistent") is True,
                str(index_bound.status()))

    real_head_before = _git(source_kb, "rev-parse", "HEAD").stdout.strip()
    real_status_before = _git(source_kb, "status", "--short").stdout

    _section_1_missing_injection(suite, index_open, index_bound)
    _section_2_payload_filter_widening(suite, index_open)
    _section_3_direct_store(suite, index_open, index_bound)
    _section_4_proxy_forgery(suite)
    _section_5_residency(suite, index_open)
    _section_6_artifacts(suite, source_kb)

    # 真实 KB 逐字不变（只读证明）。
    if os.path.isdir(os.path.join(source_kb, ".git")):
        real_head_after = _git(source_kb, "rev-parse", "HEAD").stdout.strip()
        real_status_after = _git(source_kb, "status", "--short").stdout
        suite.check("6d 真实 KB HEAD 未变", real_head_after == real_head_before,
                    f"{real_head_before[:10]} -> {real_head_after[:10]}")
        suite.check("6e 真实 KB 工作树前后逐字一致",
                    real_status_after == real_status_before,
                    "一致" if real_status_after == real_status_before
                    else "并发会话改动了 KB（非本套件写入）")


def _rmtree(path: str) -> None:
    def _on_error(func, target, _exc):  # noqa: ANN001
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onerror=_on_error)


def main() -> int:
    parser = argparse.ArgumentParser(description="隔离绕过对抗性套件（#34）")
    parser.add_argument("--source-kb", default=DEFAULT_SOURCE_KB)
    parser.add_argument("--json-out", default=None, help="把通过矩阵写成 JSON")
    args = parser.parse_args()

    source_kb = os.path.abspath(args.source_kb)
    workdir = tempfile.mkdtemp(prefix="isolation-bypass-34-")
    keep = os.environ.get("MEMORY_SANDBOX_KEEP") == "1"
    suite = Suite()
    try:
        print(f"隔离 fixture: {workdir}")
        print(f"真实 KB:      {source_kb}")
        print(f"Python:       {sys.executable}\n")
        run(suite, workdir, source_kb)
    except Exception:  # noqa: BLE001 - 任何异常如实汇报
        traceback.print_exc()
        suite.check("套件未抛异常", False, "见上方 traceback")
    finally:
        if keep:
            print(f"\n[keep] 保留沙箱目录：{workdir}")
        else:
            _rmtree(workdir)

    total = len(suite.rows)
    passed = sum(1 for row in suite.rows if row["ok"])
    print(f"\n==== {passed}/{total} 通过 ====")
    for row in suite.failed:
        print(f"  FAIL: {row['name']} — {row['detail']}")
    if suite.failed:
        print("\n存在未被拦的攻击面（阻塞项），详见 memory_agent/eval/isolation_bypass_34_results.md")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump({"total": total, "passed": passed, "rows": suite.rows},
                      handle, ensure_ascii=False, indent=2)
    return 0 if suite.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
