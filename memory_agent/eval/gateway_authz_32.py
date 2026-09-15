"""#32 网关验收：`/mcp` 边界 authn + 工具层 authz（强制过滤注入）。

外部行为断言，可复跑；不加载任何模型（Qdrant local + 桩嵌入）。覆盖：

- authn：进程配置身份 / Bearer token 绑定 / 无效或缺失即拒绝 / 凭证不进审计；
- authz 负向回归（核心）：调用方**尝试放宽** tenant / classification → 被拦；
- 工具层无条件注入：`memory_search` / `memory_get` / 写工具；
- 真实 Qdrant 侧过滤：tenant 隔离 + 多值 ABAC（classification ∈ {private, internal}）；
- 审计：`tools/call` 记录调用 + 身份。

    venv\\Scripts\\python.exe memory_agent/eval/gateway_authz_32.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile

# 从 worktree / 任意 CWD 直接 `python memory_agent/eval/...` 也能解析到本仓的
# `memory_agent` / `ragcore`（editable 安装可能指向别处）。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from memory_agent import mcp_server
from memory_agent.gateway import (
    AuditLog,
    AuthConfig,
    GatewayAuthnMiddleware,
    make_identity,
    use_identity,
)
from memory_agent.memory.entries import Entry
from memory_agent.memory.index import MemoryIndex
from memory_agent.memory.store import QdrantLocalStore
from ragcore.utils.model_status import EMBEDDING_DIMENSION

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(condition), detail))


class _StubEmbeddings:
    """所有文本同向量：让过滤（而非相似度）成为唯一的区分因素。"""

    def embed_query(self, text):
        return [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]


class _Headers(dict):
    pass


class _Request:
    def __init__(self, authorization=None):
        self.headers = _Headers({"authorization": authorization}) if authorization else _Headers()


class _Ctx:
    def __init__(self, authorization=None, method="tools/call", name="memory_search"):
        self.method = method
        self.params = {"name": name}
        self.request = _Request(authorization)


def _entry(root, rel, tenant, classification, residency="local", body="body text"):
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


def _build_index(workdir: str, entries):
    store = QdrantLocalStore(db_path=os.path.join(workdir, "qdrant"),
                             embeddings=_StubEmbeddings())
    index = MemoryIndex(store=store, manifest_path=os.path.join(workdir, "manifest.json"))
    index.rebuild(entries)
    return index


def _run(coro):
    return asyncio.run(coro)


def main() -> int:
    workdir = tempfile.mkdtemp(prefix="gateway-authz-32-")
    try:
        # ---------------------------------------------------------------- authn
        default = make_identity(principal="local", role="owner")
        config = AuthConfig(default=default, tokens={"tok-a": make_identity(
            principal="svc-a", tenant="org-a", role="writer")}, require_token=True)

        check("authn_config_default", default.principal == "local", "进程配置身份")
        try:
            resolved = _run(GatewayAuthnMiddleware(
                AuthConfig(default=default, tokens=config.tokens, require_token=True),
                audit=AuditLog(),
            )(_Ctx("Bearer tok-a"), _identity_probe))
            check("authn_token_binds_identity", resolved == ("svc-a", "org-a"), str(resolved))
        except Exception as exc:  # noqa: BLE001
            check("authn_token_binds_identity", False, repr(exc))

        ran = {"v": False}

        async def _should_not_run(ctx):
            ran["v"] = True
            return {}

        try:
            _run(GatewayAuthnMiddleware(config, audit=AuditLog())(
                _Ctx("Bearer nope"), _should_not_run))
        except Exception:  # noqa: BLE001 - 只验被拒
            pass
        check("authn_bad_token_denied", ran["v"] is False, "无效 token 在工具前被拒")

        # --------------------------------------------------- 工具层 + 真实 Qdrant
        root = os.path.join(workdir, "kb")
        entries = [
            _entry(root, "topics/a.md", "org-a", "private"),
            _entry(root, "topics/b.md", "org-a", "internal"),
            _entry(root, "topics/c.md", "org-a", "public"),
            _entry(root, "topics/z.md", "org-b", "private"),
        ]
        index = _build_index(workdir, entries)
        original = mcp_server.get_index
        mcp_server.get_index = lambda: index
        try:
            writer_identity = make_identity(
                principal="svc-a", tenant="org-a", role="writer",
                classifications="private,internal", residencies="local")
            with use_identity(writer_identity):
                hits = mcp_server.memory_search("body", k=10)
                tenants = {hit["tenant"] for hit in hits}
                classes = {hit["classification"] for hit in hits}
                check("search_tenant_isolation", tenants == {"org-a"}, str(tenants))
                check("search_abac_multi_value",
                      classes == {"private", "internal"}, str(classes))

                try:
                    mcp_server.memory_search("body", payload_filter={"tenant": "org-b"})
                    check("search_widen_tenant_denied", False, "未被拦")
                except mcp_server.ToolError:
                    check("search_widen_tenant_denied", True, "越权 tenant 被拦")

                try:
                    mcp_server.memory_search("body",
                                             payload_filter={"classification": "public"})
                    check("search_widen_classification_denied", False, "未被拦")
                except mcp_server.ToolError:
                    check("search_widen_classification_denied", True, "越权 classification 被拦")

                try:
                    mcp_server.memory_get("topics/z")  # 属于 org-b
                    check("get_other_tenant_denied", False, "未被拦")
                except mcp_server.ToolError:
                    check("get_other_tenant_denied", True, "跨租户 get 被拦")

                ok = mcp_server.memory_get("topics/a")
                check("get_within_entitlement_allowed", ok["id"] == "topics/a", ok.get("id", ""))

            with use_identity(make_identity(principal="svc-a", role="reader")):
                try:
                    mcp_server.memory_add(title="t", body="b", domain="topics",
                                          type="topic", tags=["gateway"])
                    check("reader_write_denied", False, "未被拦")
                except mcp_server.ToolError:
                    check("reader_write_denied", True, "reader 写被拦")
        finally:
            mcp_server.get_index = original

        # --------------------------------------------------------------- 审计
        log = AuditLog()

        async def _probe(ctx):
            return {"ok": True}

        middleware = GatewayAuthnMiddleware(config, audit=log)
        _run(middleware(_Ctx("Bearer tok-a"), _probe))
        events = log.entries
        check("audit_records_call",
              bool(events) and events[-1]["tool"] == "memory_search"
              and events[-1]["identity"]["tenant"] == "org-a",
              json.dumps(events[-1:], ensure_ascii=False))
        check("audit_no_token", "tok-a" not in json.dumps(events, ensure_ascii=False),
              "凭证不进审计")
        # 无效 token 也绝不进审计
        bad = AuditLog()
        try:
            _run(GatewayAuthnMiddleware(config, audit=bad)(_Ctx("Bearer leak-me"), _probe))
        except Exception:  # noqa: BLE001
            pass
        check("audit_no_token_on_reject",
              "leak-me" not in json.dumps(bad.entries, ensure_ascii=False), "拒绝路径不含凭证")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    passed = all(ok for _, ok, _ in CHECKS)
    report = {
        "issue": "#32 gateway authn/authz",
        "checks": [{"name": name, "ok": ok, "detail": detail} for name, ok, detail in CHECKS],
        "total": len(CHECKS),
        "failed": [name for name, ok, _ in CHECKS if not ok],
        "passed": passed,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


async def _identity_probe(_ctx):
    from memory_agent.gateway import current_identity
    identity = current_identity()
    return (identity.principal, identity.tenant)


if __name__ == "__main__":
    raise SystemExit(main())
