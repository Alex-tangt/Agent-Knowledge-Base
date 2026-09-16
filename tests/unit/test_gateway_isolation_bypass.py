"""#34 隔离绕过对抗性单测（锁外部可观察行为，不加载模型）。

与 `memory_agent/eval/isolation_bypass_34.py` 同口径的快速回归：覆盖 tenant / classification /
residency 的负向断言 + proxy 伪造身份 + 工具面无裸 store 入口。三个**已知未被拦**的攻击面
（store 绑定租户被 keyword 通道绕过 / store 绑定覆盖网关注入 tenant）用 `xfail(strict=True)`
标出——它们是 #34 的阻塞项，修好后会 XPASS 提醒移除标记。
"""
import asyncio
import os

import pytest

from memory_agent import mcp_server
from memory_agent.gateway import (
    AuditLog,
    AuthConfig,
    GatewayAuthnMiddleware,
    current_identity,
    make_identity,
    use_identity,
)
from memory_agent.memory.entries import Entry
from memory_agent.memory.index import MemoryIndex
from memory_agent.memory.store import QdrantLocalStore
from ragcore.utils.model_status import EMBEDDING_DIMENSION

MARK_A = "ALPHA34A"
MARK_B = "ZEBRA34B"
BOUND_TENANT = "org-a"


class StubEmbeddings:
    def embed_query(self, text):
        return [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]


def _entry(base, name, tenant, classification, residency, body):
    path = os.path.join(str(base), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            "---\n"
            f"id: {name[:-3]}\n"
            f'title: "{name}"\n'
            f"tenant: {tenant}\n"
            f"classification: {classification}\n"
            f"residency: {residency}\n"
            "---\n\n"
            f"# {name}\n\n{body}\n"
        )
    return Entry.from_file(path, source=name, writable=True, entry_id=name[:-3])


def _fixture(tmp_path):
    return [
        _entry(tmp_path, "topics/a-priv.md", "org-a", "private", "local",
               f"org-a private note {MARK_A}"),
        _entry(tmp_path, "topics/a-internal.md", "org-a", "internal", "local",
               "org-a internal note"),
        _entry(tmp_path, "topics/a-cloud.md", "org-a", "private", "cloud",
               "org-a cloud note CLOUD34C"),
        _entry(tmp_path, "topics/b-secret.md", "org-b", "private", "local",
               f"org-b secret note {MARK_B}"),
    ]


def _index(tmp_path, name, entries, tenant=None):
    store = QdrantLocalStore(db_path=str(tmp_path / name / "q"),
                             embeddings=StubEmbeddings(), tenant=tenant)
    index = MemoryIndex(store=store, manifest_path=str(tmp_path / name / "manifest.json"))
    index.rebuild(entries)
    return index


def _org_a(**kwargs):
    kwargs.setdefault("principal", "svc-a")
    kwargs.setdefault("tenant", "org-a")
    kwargs.setdefault("role", "writer")
    return make_identity(**kwargs)


def _search(identity, query, **kwargs):
    with use_identity(identity):
        return mcp_server.memory_search(query, **kwargs)


def _hit_ids(hits):
    return {hit["id"] for hit in hits}


# ---------------------------------------------------------- ① tenant 注入 / 隔离

def test_org_a_search_is_isolated_on_both_channels(tmp_path, monkeypatch):
    index = _index(tmp_path, "open", _fixture(tmp_path))
    monkeypatch.setattr(mcp_server, "get_index", lambda: index)

    vector = _search(_org_a(), MARK_A)
    assert vector and {h["tenant"] for h in vector} == {"org-a"}

    # marker 只在 org-b 条目里：keyword 通道也必须被网关注入的 tenant 过滤掉
    keyword = _search(_org_a(), MARK_B)
    assert all(h["tenant"] == "org-a" for h in keyword)


def test_memory_search_without_filter_still_injects_tenant(tmp_path, monkeypatch):
    index = _index(tmp_path, "open", _fixture(tmp_path))
    monkeypatch.setattr(mcp_server, "get_index", lambda: index)
    hits = _search(_org_a(), "note")
    assert hits and all(h["tenant"] == "org-a" for h in hits)


# ------------------------------------------------------------- ② payload_filter 放宽

@pytest.mark.parametrize("widening", [
    {"tenant": "org-b"},
    {"classification": "public"},
    {"residency": "cloud"},
    {"tenant": ["org-a", "org-b"]},
    {"tenant": None},
    {"tenant": ""},
])
def test_payload_filter_widening_is_rejected(tmp_path, monkeypatch, widening):
    index = _index(tmp_path, "open", _fixture(tmp_path))
    monkeypatch.setattr(mcp_server, "get_index", lambda: index)
    identity = _org_a(classifications="private,internal", residencies="local")
    with pytest.raises(mcp_server.ToolError):
        _search(identity, MARK_A, payload_filter=widening)


def test_payload_filter_case_variant_does_not_widen(tmp_path, monkeypatch):
    index = _index(tmp_path, "open", _fixture(tmp_path))
    monkeypatch.setattr(mcp_server, "get_index", lambda: index)
    hits = _search(_org_a(), MARK_A, payload_filter={"Tenant": "org-b"})
    assert all(h["tenant"] == "org-a" for h in hits)


def test_memory_get_rejects_cross_tenant_id(tmp_path, monkeypatch):
    index = _index(tmp_path, "open", _fixture(tmp_path))
    monkeypatch.setattr(mcp_server, "get_index", lambda: index)
    with use_identity(_org_a()):
        with pytest.raises(mcp_server.ToolError):
            mcp_server.memory_get("topics/b-secret")
        assert mcp_server.memory_get("topics/a-priv")["id"] == "topics/a-priv"


# ---------------------------------------------------------------------- ③ 直调 store

def test_bound_store_vector_channel_cannot_be_widened(tmp_path):
    index = _index(tmp_path, "bound", _fixture(tmp_path), tenant=BOUND_TENANT)
    widened = index.store.search("note", k=10, payload_filter={"tenant": "org-b"})
    ids = {m["entry_id"] for m in widened["metadatas"][0]}
    assert ids and all(i.startswith("topics/a-") for i in ids)


def test_mcp_tool_surface_has_no_raw_store_parameters():
    tools = mcp_server.mcp._tool_manager._tools
    raw = {"tenant", "store", "db_path", "collection", "collection_name"}
    offenders = {
        name: sorted(set(tool.parameters.get("properties", {})) & raw)
        for name, tool in tools.items()
        if set(tool.parameters.get("properties", {})) & raw
    }
    assert offenders == {}


# ------------------------------------------------------------------ ④ proxy 伪造身份

class _Request:
    def __init__(self, headers):
        self.headers = dict(headers)


class _Ctx:
    def __init__(self, headers, name="memory_search"):
        self.method = "tools/call"
        self.params = {"name": name}
        self.request = _Request(headers)


def _resolve(config, headers):
    async def probe(_ctx):
        return current_identity()

    return asyncio.run(GatewayAuthnMiddleware(config, audit=AuditLog())(_Ctx(headers), probe))


def _config():
    return AuthConfig(default=_org_a(), tokens={"tok-a": _org_a()}, require_token=True)


def test_forged_headers_are_not_trusted():
    with pytest.raises(Exception):
        _resolve(_config(), {"x-tenant": "org-b"})

    identity = _resolve(_config(), {
        "authorization": "Bearer tok-a",
        "x-tenant": "org-b", "x-principal": "root", "x-role": "admin",
        "x-classification": "public",
    })
    assert identity.tenant == "org-a"
    assert identity.principal == "svc-a"
    assert identity.role == "writer"


@pytest.mark.parametrize("headers", [
    {"authorization": "Basic YWRtaW46YWRtaW4="},
    {},
])
def test_missing_or_non_bearer_credentials_are_rejected(headers):
    with pytest.raises(Exception):
        _resolve(_config(), headers)


def test_proxy_is_transport_only():
    proxy_path = os.path.join(os.path.dirname(mcp_server.__file__), "proxy.py")
    with open(proxy_path, encoding="utf-8") as handle:
        text = handle.read()
    assert "MEMORY_AUTH" not in text
    assert "identity" not in text
    assert "gateway" not in text
    assert "authorization" not in text.lower()


# ---------------------------------------------------------------------- ⑤ residency

def test_residency_entitlement_isolates_search_and_get(tmp_path, monkeypatch):
    index = _index(tmp_path, "open", _fixture(tmp_path))
    monkeypatch.setattr(mcp_server, "get_index", lambda: index)

    local_only = _org_a(residencies="local")
    hits = _search(local_only, "note", k=10)
    assert hits and {h["residency"] for h in hits} == {"local"}

    cloud_only = _org_a(residencies="cloud")
    hits = _search(cloud_only, "note", k=10)
    assert hits and {h["residency"] for h in hits} == {"cloud"}

    keyword = _search(local_only, "CLOUD34C")
    assert all(h["residency"] == "local" for h in keyword)

    with use_identity(local_only):
        with pytest.raises(mcp_server.ToolError):
            mcp_server.memory_get("topics/a-cloud")


# --------------------------------------------------- #34 阻塞项（已知未被拦，xfail）

@pytest.mark.xfail(strict=True, reason="#34 阻塞：keyword 通道不认 store 绑定租户，跨租户泄漏")
def test_bound_store_keyword_channel_honours_tenant(tmp_path):
    index = _index(tmp_path, "bound", _fixture(tmp_path), tenant=BOUND_TENANT)
    matches = index.store.search_by_keywords([MARK_B])
    assert matches == []


@pytest.mark.xfail(strict=True, reason="#34 阻塞：store 绑定租户覆盖网关注入的 tenant，org-b 拿到 org-a")
def test_bound_store_does_not_override_gateway_tenant(tmp_path, monkeypatch):
    index = _index(tmp_path, "bound", _fixture(tmp_path), tenant=BOUND_TENANT)
    monkeypatch.setattr(mcp_server, "get_index", lambda: index)
    org_b = make_identity(principal="svc-b", tenant="org-b", role="reader")
    hits = _search(org_b, MARK_A)
    assert _hit_ids(hits) == set()


@pytest.mark.xfail(strict=True, reason="#34 阻塞：身份未声明 tenant 时 keyword 通道漏 org-b")
def test_unbound_identity_on_bound_store_does_not_leak(tmp_path, monkeypatch):
    index = _index(tmp_path, "bound", _fixture(tmp_path), tenant=BOUND_TENANT)
    monkeypatch.setattr(mcp_server, "get_index", lambda: index)
    unbound = make_identity(principal="svc-unbound", tenant=None, role="reader")
    hits = _search(unbound, MARK_B)
    assert all(h["tenant"] == "org-a" for h in hits)
