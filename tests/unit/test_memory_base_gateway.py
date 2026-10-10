"""Capture acceptance at the authenticated HTTP and MCP boundaries (no models)."""

import asyncio
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from memory_base.gateway import (
    AddRequest, CaptureError, CaptureService, Identity, SCOPE, create_app, parse_tokens,
)
from memory_base.mcp_stdio import GatewayClient, ToolError, create_mcp
from memory_base.upstream import OpenVikingClient, UpstreamError


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

    def health(self):
        return self.online

    def read(self, uri):
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

    def find(self, query, limit, scope):
        if not self.online:
            raise UpstreamError(503)
        self.search_arguments = (query, limit, scope)
        hits = [{"uri": uri, "abstract": "stored excerpt", "score": .8}
                for uri in self.files if uri.endswith("content.md")]
        return self.extra_hits + hits


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
                                     "abstract": "stored excerpt", "score": .8}]


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


def test_mcp_exposes_the_same_three_actions_and_errors(api):
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
        assert {tool.name for tool in tools} == {"memory_add", "memory_search", "memory_get"}
        created = await server.call_tool("memory_add", BODY)
        structured = created[1] if isinstance(created, tuple) else created.structured_content
        read = await server.call_tool("memory_get", {"uri": structured["uri"]})
        read_structured = read[1] if isinstance(read, tuple) else read.structured_content
        assert read_structured["content"] == BODY["content"]
        found = await server.call_tool("memory_search", {"query": "research"})
        found_structured = found[1] if isinstance(found, tuple) else found.structured_content
        assert found_structured["items"][0]["uri"] == structured["uri"]
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
            else:
                result = {"vector_status": "complete"}
            encoded = json.dumps({"status": "ok", "result": result}, ensure_ascii=False).encode()
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
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_stdio_client_has_explicit_unavailable_error():
    client = GatewayClient("http://127.0.0.1:1", "not-logged")
    with pytest.raises(ToolError, match="unavailable") as exc:
        client.call("search", {"query": "query"})
    assert "not-logged" not in str(exc.value)
