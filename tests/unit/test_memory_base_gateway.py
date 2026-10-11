"""Capture acceptance at the authenticated HTTP and MCP boundaries (no models)."""

import asyncio
import hashlib
import json
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from memory_base.gateway import (
    AddRequest, CaptureError, CaptureService, Identity, SCOPE, create_app, parse_tokens,
)
from memory_base.mcp_stdio import GatewayClient, ToolError, create_mcp
from memory_base.upstream import OpenVikingClient, UpstreamError
from memory_base.sources import SourceRegistry


class StoredUpstream:
    """Persistent test store with failure injection at the upstream boundary."""

    def __init__(self):
        self.files = {}
        self.lock = threading.Lock()
        self.online = True
        self.fail_after_create = None
        self.fail_before_content = False
        self.reindex_fails = False
        self.search_arguments = None
        self.extra_hits = []
        self.find_allowlist = None
        self.list_page_size = None
        self.read_log = []

    def health(self):
        return self.online

    def read(self, uri):
        self.read_log.append(uri)
        if not self.online:
            raise UpstreamError(503)
        with self.lock:
            if uri not in self.files:
                raise UpstreamError(404, "NOT_FOUND")
            return self.files[uri]

    def create(self, uri, content, *, capture=False):
        if not self.online or (capture and self.fail_before_content):
            raise UpstreamError(503)
        with self.lock:
            if uri in self.files:
                raise UpstreamError(409, "ALREADY_EXISTS")
            self.files[uri] = content
        if self.fail_after_create and uri.endswith(self.fail_after_create):
            raise UpstreamError(503)
        return {"vector_status": "complete"}

    def reindex(self, uri):
        if self.reindex_fails:
            raise UpstreamError(503)
        return {"status": "completed", "failed_records": 0, "rebuilt_records": 1}

    def ls(self, uri, offset=0, limit=1000):
        if not self.online:
            raise UpstreamError(503)
        entries = [{"uri": uri} for uri in sorted(self.files)]
        limit = min(limit, self.list_page_size or limit)
        return {"entries": entries[offset:offset + limit], "has_more": offset + limit < len(entries)}

    def find(self, query, limit, scope, allowed_uris=None):
        if not self.online:
            raise UpstreamError(503)
        self.search_arguments = (query, limit, scope)
        self.find_allowlist = allowed_uris
        hits = [{"uri": uri, "abstract": "stored excerpt", "score": .8}
                for uri in self.files if uri.endswith("content.md")]
        if allowed_uris is not None:
            hits = [hit for hit in hits if hit["uri"] in allowed_uris]
        return self.extra_hits + hits[:limit]


TOKENS = {
    "writer-token": Identity("bubble", "writer"),
    "other-token": Identity("codex", "writer"),
    "reader-token": Identity("reader", "reader"),
}
HEADERS = {"Authorization": "Bearer writer-token"}
BODY = {
    "request_id": "article-2026-10-11-1", "title": "研究笔记 原始标题 Case Sensitive",
    "kind": "article_summary", "source_url": "https://example.org/article?id=Original",
    "source_id": "ArticleID", "conversation_id": "ConversationID",
    "content": "# 标题\r\n\r\n  第一段，保留缩进。\n第二段。\n",
}


@pytest.fixture
def api():
    upstream = StoredUpstream()
    client = TestClient(create_app(upstream, TOKENS))
    return upstream, client


def add(client, body=None, headers=HEADERS):
    return client.post("/api/memory/add", headers=headers, json=body or BODY)


def test_original_trace_and_idempotency_survive_gateway_restart(api):
    upstream, client = api
    created = add(client)
    assert created.status_code == 200
    result = created.json()
    assert result["status"] == "created"
    assert result["identity"] == {"principal": "bubble", "role": "writer"}
    assert result["content_hash"] == hashlib.sha256(BODY["content"].encode()).hexdigest()
    assert result["index"] == {"status": "ready"}
    assert client.post("/api/memory/get", headers=HEADERS,
                       json={"uri": result["uri"]}).json()["content"] == BODY["content"]
    metadata = json.loads(upstream.files[result["uri"].replace("content.md", "capture.json")])
    assert metadata["source_url"] == BODY["source_url"]
    assert metadata["principal"] == "bubble"
    assert metadata["request_id"] == BODY["request_id"]
    assert metadata["title"] == BODY["title"]
    assert "created_at" in metadata and "content" not in metadata
    restarted = TestClient(create_app(upstream, TOKENS))
    retry = add(restarted).json()
    assert retry["status"] == "exists"
    assert retry["uri"] == result["uri"]
    assert len(upstream.files) == 2


@pytest.mark.parametrize("change", [
    {"content": "changed"}, {"title": "changed title"}, {"kind": "video_summary"},
    {"source_url": "https://example.org/changed"}, {"conversation_id": "other"},
])
def test_reusing_id_with_different_request_cannot_replace_original(api, change):
    upstream, client = api
    original = add(client).json()
    before = dict(upstream.files)
    conflict = add(client, {**BODY, **change})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "REQUEST_ID_CONFLICT"
    assert upstream.files == before
    assert upstream.files[original["uri"]] == BODY["content"]


def test_identity_scopes_ids_but_readers_share_content(api):
    upstream, client = api
    first = add(client).json()
    other = add(client, headers={"Authorization": "Bearer other-token"}).json()
    assert first["uri"] != other["uri"]
    reader = {"Authorization": "Bearer reader-token"}
    assert add(client, headers=reader).status_code == 403
    fetched = client.post("/api/memory/get", headers=reader, json={"uri": first["uri"]})
    assert fetched.status_code == 200 and fetched.json()["content"] == BODY["content"]


@pytest.mark.parametrize("authorization", [None, "Bearer missing", "Basic writer-token", "Bearer "])
def test_authentication_is_required(api, authorization):
    upstream, client = api
    headers = {"Authorization": authorization} if authorization else {}
    assert add(client, headers=headers).status_code == 401
    assert not upstream.files


@pytest.mark.parametrize("uri", [
    "viking://resources/outside/content.md", SCOPE + "/../outside/content.md",
    SCOPE + "/" + "a" * 64 + "/capture.json",
    SCOPE + "/" + "a" * 64 + "/content.md?x=1",
    SCOPE + "/" + "a" * 64 + "/%63ontent.md",
])
def test_get_cannot_leave_capture_content_scope(api, uri):
    upstream, client = api
    assert client.post("/api/memory/get", headers=HEADERS, json={"uri": uri}).status_code == 422


def test_search_uses_shared_vectors_and_filters_untrusted_references(api):
    upstream, client = api
    result = add(client).json()
    upstream.extra_hits = [
        {"uri": "viking://resources/outside/secret.md", "score": 100},
        {"uri": result["uri"].replace("content.md", "capture.json"), "score": 100},
    ]
    hits = client.post("/api/memory/search", headers=HEADERS,
                       json={"query": "文章研究", "limit": 3})
    assert hits.status_code == 200
    assert upstream.search_arguments == ("文章研究", 3, SCOPE)
    assert hits.json()["items"] == [{"uri": result["uri"], "title": BODY["title"],
                                     "abstract": "stored excerpt", "score": .8,
                                     "path": result["path"], "revision": result["revision"],
                                     "method": "capture_vector"}]


def test_metadata_reservation_retry_and_derived_failure_are_explicit(api):
    upstream, client = api
    upstream.fail_after_create = "capture.json"
    upstream.fail_before_content = True
    rejected = add(client)
    assert rejected.status_code == 503
    assert len(upstream.files) == 1  # Immutable reservation is retained.
    assert add(client, {**BODY, "content": "changed"}).status_code == 409
    upstream.fail_before_content = False
    upstream.fail_after_create = "content.md"
    accepted = add(client)
    assert accepted.status_code == 200
    assert accepted.json()["index"] == {"status": "pending"}
    uri = accepted.json()["uri"]
    assert upstream.files[uri] == BODY["content"]
    upstream.fail_after_create = None
    assert add(client).json()["index"] == {"status": "ready"}


def test_existing_content_is_not_failed_when_reindex_is_unavailable(api):
    upstream, client = api
    add(client)
    upstream.reindex_fails = True
    retry = add(client)
    assert retry.status_code == 200
    assert retry.json()["status"] == "exists"
    assert retry.json()["index"] == {"status": "pending"}


def test_upstream_failure_is_503_not_empty_memory(api):
    upstream, client = api
    upstream.online = False
    search = client.post("/api/memory/search", headers=HEADERS, json={"query": "article"})
    assert search.status_code == 503 and "items" not in search.json()
    assert add(client).status_code == 503
    health = client.get("/health")
    assert health.status_code == 503
    assert health.json()["service"] == "memory-base-gateway"
    assert isinstance(health.json()["pid"], int)


def test_concurrent_duplicate_writes_create_one_original():
    upstream = StoredUpstream()
    service = CaptureService(upstream)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: service.add(AddRequest(**BODY), TOKENS["writer-token"]), range(12)))
    assert sum(item["status"] == "created" for item in results) == 1
    assert len({item["uri"] for item in results}) == 1
    assert len(upstream.files) == 2


def test_concurrent_changed_requests_cannot_overwrite_reserved_request():
    upstream = StoredUpstream()
    service = CaptureService(upstream)
    barrier = threading.Barrier(2)

    def write(index):
        barrier.wait()
        try:
            return service.add(AddRequest(**{**BODY, "title": str(index)}), TOKENS["writer-token"])
        except CaptureError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, range(2)))
    assert sum(item == 409 for item in results) == 1
    assert len(upstream.files) == 2


@pytest.mark.parametrize("raw", ["", "{}", '[]', '{"secret":{"principal":"p","role":"root"}}'])
def test_invalid_token_config_fails_closed_without_echoing_secrets(raw):
    with pytest.raises(ValueError) as exc:
        parse_tokens(raw)
    assert "secret" not in str(exc.value)


def test_preferences_and_unrecognized_fields_are_not_promoted(api):
    upstream, client = api
    assert add(client, {**BODY, "kind": "global_preference"}).status_code == 422
    assert add(client, {**BODY, "promote": True}).status_code == 422
    assert not upstream.files


def test_mcp_exposes_original_actions_plus_list_and_explicit_errors(api):
    upstream, client = api

    class HTTPBoundary:
        def call(self, tool, arguments):
            response = client.post("/api/memory/" + tool, headers=HEADERS, json=arguments)
            if response.status_code != 200:
                raise ToolError("Memory gateway HTTP " + str(response.status_code))
            return response.json()

    server = create_mcp(HTTPBoundary())

    async def exercise():
        tools = await server.list_tools()
        assert {tool.name for tool in tools} == {"memory_add", "memory_search", "memory_get", "memory_list"}
        for tool in tools:
            assert tool.model_dump(by_alias=True)["inputSchema"]["additionalProperties"] is False
            assert tool.annotations.model_dump(by_alias=True, exclude_none=True) == {
                "readOnlyHint": tool.name != "memory_add",
                "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
            }
        created = await server.call_tool("memory_add", BODY)
        structured = created[1] if isinstance(created, tuple) else created.structured_content
        read = await server.call_tool("memory_get", {"uri": structured["uri"]})
        read_structured = read[1] if isinstance(read, tuple) else read.structured_content
        assert read_structured["content"] == BODY["content"]
        found = await server.call_tool("memory_search", {"query": "research"})
        found_structured = found[1] if isinstance(found, tuple) else found.structured_content
        assert found_structured["items"][0]["uri"] == structured["uri"]
        listed = await server.call_tool("memory_list", {"limit": 1})
        listed_structured = listed[1] if isinstance(listed, tuple) else listed.structured_content
        assert listed_structured["items"][0]["uri"] == structured["uri"]
        with pytest.raises(Exception, match="UNKNOWN_MEMORY_TOOL_ARGUMENT"):
            await server.call_tool("memory_search", {"query": "research", "unknown": True})
        upstream.online = False
        with pytest.raises(Exception, match="503"):
            await server.call_tool("memory_search", {"query": "research"})

    asyncio.run(exercise())


def test_openviking_wire_contract_preserves_bytes_and_auth():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"null")
            calls.append((self.command, self.path, dict(self.headers), body))
            if self.path.startswith("/api/v1/content/read"):
                result = BODY["content"]
            elif self.path == "/api/v1/search/find":
                result = {"resources": []}
            elif self.path.startswith("/api/v1/fs/ls"):
                result = [{"uri": "viking://resources/captures/test"}]
            else:
                result = {"vector_status": "complete"}
            payload = {"status": "ok", "result": result}
            if self.path.startswith("/api/v1/fs/ls"):
                payload["has_more"] = False
            encoded = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        upstream = OpenVikingClient(f"http://127.0.0.1:{httpd.server_port}", "private-key")
        uri = SCOPE + "/" + "a" * 64 + "/content.md"
        upstream.create(uri, BODY["content"], capture=True)
        assert upstream.read(uri) == BODY["content"]
        upstream.find("query", 5, SCOPE)
        assert "private-key" not in repr(upstream)
        assert calls[0][3]["mode"] == "create"
        assert calls[0][3]["processing_mode"] == "vectors_only"
        assert calls[0][3]["content"] == BODY["content"]
        assert calls[0][2]["X-Api-Key"] == "private-key"
        assert calls[0][2]["X-Openviking-Account"] == "personal"
        assert calls[-1][3]["target_uri"] == SCOPE
        assert calls[-1][3]["tags"] == ["memory_base=capture"]
        upstream.find("query", 5, SCOPE, allowed_uris=[uri])
        assert calls[-1][3]["filter"] == {"op": "must", "field": "uri", "conds": [uri], "para": "-d=0"}
        assert upstream.ls(SCOPE, offset=10, limit=25)["has_more"] is False
        assert "offset=10" in calls[-1][1] and "node_limit=25" in calls[-1][1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_stdio_client_has_explicit_unavailable_error():
    client = GatewayClient("http://127.0.0.1:1", "not-logged")
    with pytest.raises(ToolError, match="unavailable") as exc:
        client.call("search", {"query": "query"})
    assert "not-logged" not in str(exc.value)


def test_scope_is_applied_before_vector_top_k_and_aliases_cannot_bypass_permissions(tmp_path):
    upstream = StoredUpstream()
    tokens = {**TOKENS, "scoped": Identity("limited", "writer", ("captures/2026-10-11/note",),
                                         ("captures/2026-10-11/note",))}
    client = TestClient(create_app(upstream, tokens, SourceRegistry(tmp_path / "sources.json")))
    # Enough disallowed results to exhaust native top-k if filtered afterwards.
    for index in range(26):
        add(client, {**BODY, "request_id": str(index)})
    visible = add(client, {**BODY, "request_id": "allowed", "kind": "note", "observed_on": "2026-10-11"}).json()
    scoped = {"Authorization": "Bearer scoped"}
    upstream.extra_hits = [{"uri": next(uri for uri in upstream.files if uri.endswith("content.md")),
                            "abstract": "must not leak", "score": 100}]
    response = client.post("/api/memory/search", headers=scoped, json={"query": "research", "limit": 1})
    assert response.status_code == 200
    assert [item["uri"] for item in response.json()["items"]] == [visible["uri"]]
    assert upstream.find_allowlist == [visible["uri"]]
    denied = next(uri for uri in upstream.files if uri.endswith("content.md") and uri != visible["uri"])
    assert client.post("/api/memory/get", headers=scoped, json={"uri": denied}).status_code == 403
    forged = visible["path"].replace("2026-10-11", "2026-10-10")
    assert client.post("/api/memory/get", headers=HEADERS, json={"uri": forged}).status_code == 404
    assert client.post("/api/memory/get", headers=scoped, json={"uri": visible["path"]}).json()["content"] == BODY["content"]
    assert add(client, headers=scoped).status_code == 403
    assert add(client, {**BODY, "request_id": "new", "kind": "note", "observed_on": "2026-10-11"}, scoped).status_code == 200
    assert client.post("/api/memory/search", headers=scoped, json={"query": "x", "scope": "projects"}).status_code == 403


def test_complete_capture_list_pagination_unknown_date_and_restart_on_change(api):
    upstream, client = api
    upstream.list_page_size = 7
    for index in range(35):
        add(client, {**BODY, "request_id": str(index)})
    paths, cursor = [], None
    while True:
        response = client.post("/api/memory/list", headers=HEADERS, json={"limit": 6, "cursor": cursor})
        assert response.status_code == 200
        data = response.json()
        assert data["completeness"]["total"] == 35
        paths.extend(item["path"] for item in data["items"])
        cursor = data["next_cursor"]
        if cursor is None:
            break
    assert len(paths) == len(set(paths)) == 35
    assert all(path.startswith("captures/unknown/article_summary/") for path in paths)
    first = client.post("/api/memory/list", headers=HEADERS, json={"limit": 1}).json()
    add(client, {**BODY, "request_id": "changed roster"})
    conflict = client.post("/api/memory/list", headers=HEADERS, json={"limit": 1, "cursor": first["next_cursor"]})
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "LIST_CHANGED_RESTART"
    upstream.online = False
    assert client.post("/api/memory/list", headers=HEADERS, json={}).status_code == 503


def test_live_project_http_scopes_freshness_and_revision_conflict(tmp_path):
    root = tmp_path / "project"
    (root / "docs").mkdir(parents=True)
    target = root / "docs" / "design.md"
    original = "# 记忆基座\r\n只读来源按路径登记，内容哈希校验未提交修改。\r\n"
    target.write_bytes(original.encode())
    (root / "docs" / "secret.md").write_bytes(b"\xff")
    registry = tmp_path / "sources.json"
    registry.write_text(json.dumps({"schema": 1, "projects": [{"name": "demo", "root": str(root)}]}))
    tokens = {"one": Identity("codex", "reader", ("projects/demo/docs/design.md",)),
              "two": Identity("dsh", "reader", ("captures",))}
    upstream = StoredUpstream()
    upstream.online = False  # Project reads and lists have no vector-service dependency.
    client = TestClient(create_app(upstream, tokens, SourceRegistry(registry)))
    headers = {"Authorization": "Bearer one"}
    path = "projects/demo/docs/design.md"
    listed = client.post("/api/memory/list", headers=headers, json={"scope": "projects/demo"})
    assert listed.status_code == 200 and len(listed.json()["items"]) == 1
    found = client.post("/api/memory/search", headers=headers, json={"query": "内容哈希", "scope": "projects/demo"})
    assert found.status_code == 200 and found.json()["method"] == "project_lexical"
    assert found.json()["completeness"]["source_scan"] == "complete"
    first = client.post("/api/memory/get", headers=headers, json={"uri": path}).json()
    assert first["content"].encode() == original.encode()
    assert first["revision"] == hashlib.sha256(original.encode()).hexdigest()
    target.write_text("修改后的原文", encoding="utf-8")
    assert client.post("/api/memory/get", headers=headers,
                       json={"uri": path, "expected_revision": first["revision"]}).status_code == 409
    assert client.post("/api/memory/get", headers={"Authorization": "Bearer two"}, json={"uri": path}).status_code == 403
    root.rename(root.with_name("offline"))
    assert client.post("/api/memory/search", headers=headers, json={"query": "x", "scope": "projects/demo"}).status_code == 503
    root.with_name("offline").rename(root)
    assert client.post("/api/memory/get", headers=headers, json={"uri": path}).status_code == 200


@pytest.mark.parametrize("arguments", [
    {"scope": "../projects", "query": "x"}, {"query": "x", "limit": 1, "unknown": True},
])
def test_unknown_fields_and_invalid_scopes_are_rejected(api, arguments):
    _, client = api
    assert client.post("/api/memory/search", headers=HEADERS, json=arguments).status_code == 422


def test_observed_date_is_immutable_but_scope_does_not_change_idempotency(api):
    upstream, client = api
    body = {**BODY, "observed_on": "2026-10-11"}
    initial = add(client, body).json()
    assert add(client, {**body, "scope": "captures/2026-10-11"}).json()["uri"] == initial["uri"]
    assert add(client, {**body, "observed_on": "2026-10-10"}).status_code == 409
    assert add(client, {**body, "observed_on": "2026-02-30"}).status_code == 422
    assert client.post("/api/memory/get", headers=HEADERS,
                       json={"uri": initial["uri"], "expected_revision": "0" * 64}).status_code == 409


def test_legacy_schema1_request_hash_replays_without_new_fields(api):
    upstream, client = api
    def digest(data):
        return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()
    capture_id = digest({"principal": "bubble", "request_id": BODY["request_id"]})
    uri = SCOPE + "/" + capture_id + "/content.md"
    upstream.files[uri] = BODY["content"]
    old_metadata = {"schema_version": 1, "principal": "bubble",
                    "request_hash": digest({"principal": "bubble", **BODY}),
                    "content_hash": hashlib.sha256(BODY["content"].encode()).hexdigest(),
                    "created_at": "2026-10-11T00:00:00+00:00",
                    **{key: value for key, value in BODY.items() if key != "content"}}
    upstream.files[uri.replace("content.md", "capture.json")] = json.dumps(old_metadata)
    response = add(client, {**BODY, "observed_on": None, "scope": "captures"})
    assert response.status_code == 200 and response.json()["status"] == "exists"
    assert response.json()["uri"] == uri
    assert json.loads(upstream.files[uri.replace("content.md", "capture.json")]) == old_metadata
