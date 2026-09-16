"""#32 网关：`/mcp` 边界 authn + 工具层 authz（强制过滤注入）。

锁外部可观察行为（不加载 BGE-M3）：
- 身份解析（进程配置 / Bearer token；无效即拒绝；凭证绝不回显）；
- effective filter 白名单构造、**只可收窄**（负向：尝试放宽被拦）；
- ABAC（classification/residency）+ tenant 都在网关落地；
- 工具层无条件注入（memory_search / get / 写工具）；
- 多值 ABAC 经端口 MatchAny 真正下沉到 Qdrant 过滤。
"""
import asyncio

import pytest

from memory_agent import mcp_server
from memory_agent.gateway import (
    AuditLog,
    AuthConfig,
    AuthenticationError,
    AuthorizationError,
    GatewayAuthnMiddleware,
    can_own,
    can_read,
    can_write,
    effective_filter,
    make_identity,
    resolve_identity,
    use_identity,
)
from memory_agent.memory.store import QdrantLocalStore
from memory_agent.memory.entries import Entry, point_id_for
from ragcore.utils.model_status import EMBEDDING_DIMENSION


def _owner(**kwargs):
    kwargs.setdefault("principal", "owner")
    kwargs.setdefault("role", "owner")
    return make_identity(**kwargs)


# ------------------------------------------------------------- identity / authn

def test_resolve_identity_defaults_to_process_config():
    config = AuthConfig(default=_owner())
    assert resolve_identity(None, config).principal == "owner"


def test_resolve_identity_uses_valid_bearer_token():
    org_a = make_identity(principal="svc-a", tenant="org-a", role="writer")
    config = AuthConfig(default=_owner(), tokens={"s3cret": org_a}, require_token=True)
    assert resolve_identity("Bearer s3cret", config) is org_a


def test_resolve_identity_rejects_unknown_or_missing_token():
    config = AuthConfig(default=_owner(), tokens={"s3cret": _owner()}, require_token=True)
    with pytest.raises(AuthenticationError):
        resolve_identity("Bearer nope", config)
    with pytest.raises(AuthenticationError):
        resolve_identity(None, config)


def test_authentication_error_never_echoes_the_token():
    secret = "top-secret-token-value"
    config = AuthConfig(default=_owner(), tokens={"other": _owner()}, require_token=True)
    with pytest.raises(AuthenticationError) as excinfo:
        resolve_identity(f"Bearer {secret}", config)
    assert secret not in str(excinfo.value)


# ------------------------------------------------------------- authz: tenant

def test_tenant_is_injected_when_bound_and_cannot_be_changed():
    identity = _owner(tenant="org-a")
    assert effective_filter(identity) == {"tenant": "org-a"}
    assert effective_filter(identity, {"tenant": "org-a"}) == {"tenant": "org-a"}
    with pytest.raises(AuthorizationError):
        effective_filter(identity, {"tenant": "org-b"})


def test_unbound_tenant_lets_caller_narrow_but_not_invent_isolation():
    identity = _owner()  # tenant=None
    assert effective_filter(identity) is None
    assert effective_filter(identity, {"tenant": "org-a"}) == {"tenant": "org-a"}


# ------------------------------------------------------- authz: ABAC narrowing

def test_unrestricted_abac_produces_no_clause():
    assert effective_filter(_owner()) is None


def test_caller_can_narrow_abac_within_entitlement():
    identity = _owner()
    assert effective_filter(identity, {"classification": "public"}) == {
        "classification": "public"
    }


def test_caller_cannot_widen_abac_beyond_entitlement():
    identity = _owner(classifications="private,internal", residencies="local")
    with pytest.raises(AuthorizationError):
        effective_filter(identity, {"classification": "public"})
    with pytest.raises(AuthorizationError):
        effective_filter(identity, {"residency": "cloud"})


def test_multi_value_abac_emits_match_any_clause():
    identity = _owner(classifications="private,internal")
    flt = effective_filter(identity)
    assert flt == {"classification": ["internal", "private"]}


def test_unknown_field_passes_through_as_narrowing():
    identity = _owner()
    assert effective_filter(identity, {"writable": True}) == {"writable": True}


# ------------------------------------------------------------- authz: read/write

def test_can_read_honours_tenant_and_abac():
    identity = _owner(tenant="org-a", classifications="private,internal")
    assert can_read(identity, {"tenant": "org-a", "classification": "private"})
    assert not can_read(identity, {"tenant": "org-b", "classification": "private"})
    assert not can_read(identity, {"tenant": "org-a", "classification": "public"})
    # 缺省值按 ADR-0019：classification 缺省 private、residency 缺省 local
    assert can_read(identity, {"tenant": "org-a"})


def test_can_write_roles():
    assert can_write(_owner(role="owner"))
    assert can_write(_owner(role="writer"))
    assert not can_write(_owner(role="reader"))


def test_owned_domains_default_to_unrestricted():
    assert _owner().owned_domains is None
    assert make_identity(principal="p", owners="").owned_domains is None


def test_owned_domains_parse_and_gate_ingestion():
    identity = _owner(owners="me, team-x")
    assert identity.owned_domains == frozenset({"me", "team-x"})
    assert can_own(identity, "me")
    assert can_own(identity, "team-x")
    assert not can_own(identity, "other")
    assert not can_own(identity, None)
    assert can_own(_owner(), None)  # 未限制


def test_audit_reports_owners_without_credentials():
    event = _owner(owners="me").to_audit()
    assert event["owners"] == ["me"]


# ------------------------------------------------------------- audit / middleware

def test_audit_records_identity_without_credentials():
    log = AuditLog()
    log.record(identity=_owner(tenant="org-a"), action="tools/call", tool="memory_search")
    (event,) = log.entries
    assert event["tool"] == "memory_search"
    assert event["identity"]["tenant"] == "org-a"
    assert "s3cret" not in str(event)


class _Request:
    def __init__(self, authorization=None):
        self.headers = {"authorization": authorization} if authorization else {}


class _Ctx:
    def __init__(self, authorization=None, method="tools/call", name="memory_search"):
        self.method = method
        self.params = {"name": name}
        self.request = _Request(authorization)


def _run(coro):
    return asyncio.run(coro)


def test_middleware_binds_identity_for_the_request_then_resets():
    config = AuthConfig(default=_owner(), tokens={"good": _owner(tenant="org-a")},
                        require_token=True)
    seen = {}

    async def call_next(ctx):
        from memory_agent.gateway import current_identity
        seen["identity"] = current_identity()
        return {"ok": True}

    middleware = GatewayAuthnMiddleware(config, audit=AuditLog())
    result = _run(middleware(_Ctx("Bearer good"), call_next))

    assert result == {"ok": True}
    assert seen["identity"].tenant == "org-a"
    from memory_agent.gateway import current_identity
    assert current_identity().tenant is None  # 请求结束即复位


def test_middleware_rejects_bad_token_before_tool_runs():
    config = AuthConfig(default=_owner(), tokens={"good": _owner()}, require_token=True)
    ran = {"called": False}

    async def call_next(ctx):
        ran["called"] = True
        return {}

    middleware = GatewayAuthnMiddleware(config, audit=AuditLog())
    with pytest.raises(Exception):
        _run(middleware(_Ctx("Bearer bad"), call_next))
    assert ran["called"] is False


# ------------------------------------------------------------- tool-layer authz

class _FakeIndex:
    def __init__(self, hits=None, entries=None):
        self.search_calls = []
        self._hits = hits or []
        self._entries = entries or {}

    def search(self, query, k=5, writable_only=False, payload_filter=None,
               exclude_retired=False):
        self.search_calls.append(payload_filter)
        self.last_exclude_retired = exclude_retired
        return self._hits

    def get(self, entry_id):
        if entry_id not in self._entries:
            raise KeyError(entry_id)
        return self._entries[entry_id]


def test_memory_search_injects_entitlement_and_denies_widening(monkeypatch):
    fake = _FakeIndex()
    monkeypatch.setattr(mcp_server, "get_index", lambda: fake)
    identity = _owner(tenant="org-a", classifications="private")

    with use_identity(identity):
        mcp_server.memory_search("q", k=3)
        assert fake.search_calls[-1] == {"tenant": "org-a", "classification": "private"}

        with pytest.raises(mcp_server.ToolError):
            mcp_server.memory_search("q", payload_filter={"classification": "public"})


def test_memory_search_default_identity_has_no_clause(monkeypatch):
    fake = _FakeIndex()
    monkeypatch.setattr(mcp_server, "get_index", lambda: fake)
    with use_identity(_owner()):
        mcp_server.memory_search("q")
    assert fake.search_calls[-1] is None


def test_memory_search_passes_exclude_retired_through(monkeypatch):
    """#42：读侧退役过滤是显式开关，且不干扰网关注入的授权过滤。"""
    fake = _FakeIndex()
    monkeypatch.setattr(mcp_server, "get_index", lambda: fake)
    with use_identity(_owner(tenant="org-a")):
        mcp_server.memory_search("q", exclude_retired=True)
    assert fake.last_exclude_retired is True
    assert fake.search_calls[-1] == {"tenant": "org-a"}


def test_memory_get_denies_entry_outside_entitlement(monkeypatch):
    fake = _FakeIndex(entries={
        "secret": {"id": "secret", "classification": "public", "residency": "local"},
    })
    monkeypatch.setattr(mcp_server, "get_index", lambda: fake)
    with use_identity(_owner(classifications="private")):
        with pytest.raises(mcp_server.ToolError):
            mcp_server.memory_get("secret")


def test_write_tools_require_write_role(monkeypatch):
    monkeypatch.setattr(mcp_server, "get_writer", lambda: _FakeIndex())
    with use_identity(_owner(role="reader")):
        with pytest.raises(mcp_server.ToolError):
            mcp_server.memory_add(title="t", body="b", section="topics",
                                  type="topic", tags=["x"])


# ------------------------------------------- 多值 ABAC 真正下沉到 Qdrant（无模型）

class _StubEmbeddings:
    def embed_query(self, text):
        return [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]


def _entry(tmp_path, name, classification, entry_id):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\nid: {entry_id}\ntitle: "{entry_id}"\nclassification: {classification}\n---\n\n# {entry_id}\n',
        encoding="utf-8",
    )
    return Entry.from_file(str(path), source=name, writable=True, entry_id=entry_id)


def test_multi_value_abac_filter_is_enforced_by_qdrant(tmp_path):
    store = QdrantLocalStore(db_path=str(tmp_path / "q"), embeddings=_StubEmbeddings())
    store.add(
        ["priv doc", "internal doc", "public doc"],
        metadata_list=[{"entry_id": "a", "classification": "private"},
                       {"entry_id": "b", "classification": "internal"},
                       {"entry_id": "c", "classification": "public"}],
        ids=[point_id_for("a"), point_id_for("b"), point_id_for("c")],
    )

    identity = _owner(classifications="private,internal")
    result = store.search("doc", k=5, payload_filter=effective_filter(identity))

    returned = {m["entry_id"] for m in result["metadatas"][0]}
    assert returned == {"a", "b"}
    assert "c" not in returned
