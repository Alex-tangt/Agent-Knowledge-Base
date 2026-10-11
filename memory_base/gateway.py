"""Person-owned capture API shared by HTTP consumers and MCP transports."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from memory_base.upstream import OpenVikingClient, UpstreamError
from memory_base.paths import (CAPTURE_URI, allowed, capture_path, contains, effective_scopes,
                               logical_path, observed_date, overlaps)
from memory_base.sources import SourceError, SourceRegistry, lexical_score

SCOPE = "viking://resources/captures"
CONTENT_URI = CAPTURE_URI
CAPTURE_PATH = re.compile(r"^captures/(unknown|\d{4}-\d{2}-\d{2})/(note|article_summary|video_summary)/([0-9a-f]{64})/content\.md$")


def _scope(value: str | None) -> str | None:
    return logical_path(value, root=True) if value is not None else None


class AddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1, max_length=2_000_000)
    title: str = Field(min_length=1, max_length=500)
    kind: Literal["note", "article_summary", "video_summary"] = "note"
    source_url: str | None = Field(default=None, max_length=4096)
    source_id: str | None = Field(default=None, max_length=512)
    conversation_id: str | None = Field(default=None, max_length=512)
    observed_on: str | None = None
    scope: str | None = None

    _observed = field_validator("observed_on")(observed_date)
    _scope = field_validator("scope")(_scope)

    @field_validator("request_id", "content", "title")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value  # Validation never rewrites the original content.


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=8192)
    limit: int = Field(default=5, ge=1, le=20)
    scope: str | None = None
    _scope = field_validator("scope")(_scope)

    @field_validator("query")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class GetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uri: str
    scope: str | None = None
    expected_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    _scope = field_validator("scope")(_scope)

    @field_validator("uri")
    @classmethod
    def capture_uri(cls, value: str) -> str:
        if CONTENT_URI.fullmatch(value):
            return value
        logical_path(value)
        if not CAPTURE_PATH.fullmatch(value) and not (value.startswith("projects/") and value.endswith(".md") and len(value.split("/")) >= 3):
            raise ValueError("uri must name a capture or project Markdown file")
        return value


class ListRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: str | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    cursor: str | None = Field(default=None, max_length=160, pattern=r"^[0-9a-f]{64}:[0-9]+$")
    _scope = field_validator("scope")(_scope)


@dataclass(frozen=True)
class Identity:
    principal: str
    role: Literal["writer", "reader"]
    read_scopes: tuple[str, ...] | None = None
    write_scopes: tuple[str, ...] | None = None

    def __post_init__(self):
        for scopes in (self.read_scopes, self.write_scopes):
            if scopes is not None:
                for scope in scopes:
                    if logical_path(scope, root=True) != scope:
                        raise ValueError("identity scopes must use canonical paths")

    def public(self) -> dict:
        return {"principal": self.principal, "role": self.role}


class CaptureError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def parse_tokens(raw: str) -> dict[str, Identity]:
    try:
        entries = json.loads(raw)
        if not isinstance(entries, dict) or not entries:
            raise ValueError()
        result = {}
        for token, item in entries.items():
            if not isinstance(token, str) or not token or not isinstance(item, dict):
                raise ValueError()
            principal, role = item.get("principal"), item.get("role")
            if not isinstance(principal, str) or not principal.strip() or len(principal) > 128:
                raise ValueError()
            if role not in {"writer", "reader"}:
                raise ValueError()
            if set(item) - {"principal", "role", "read_scopes", "write_scopes"}:
                raise ValueError()
            def grants(key):
                value = item.get(key)
                if value is None:
                    return None
                if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
                    raise ValueError()
                return tuple(logical_path(s, root=True) for s in value)
            result[token] = Identity(principal, role, grants("read_scopes"), grants("write_scopes"))
        return result
    except (ValueError, TypeError):
        raise ValueError("MEMORY_BASE_TOKENS must map tokens to principal and writer/reader role") from None


def _json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _index_ready(outcome: dict) -> bool:
    if outcome.get("vector_status") == "complete":
        return True
    # content/reindex has a different contract from content/write in OV 0.4.23.
    return (outcome.get("status") == "completed"
            and outcome.get("failed_records") == 0
            and isinstance(outcome.get("rebuilt_records"), int)
            and outcome["rebuilt_records"] > 0)


class CaptureService:
    def __init__(self, upstream: OpenVikingClient, sources: SourceRegistry | None = None):
        self.upstream = upstream
        self.sources = sources or SourceRegistry(Path(os.environ.get("MEMORY_BASE_HOME", str(Path.home() / ".agent-memory-base"))) / "sources.json")
        self._locks: dict[str, tuple[threading.Lock, int]] = {}
        self._locks_guard = threading.Lock()

    @contextmanager
    def _request_lock(self, key: str):
        with self._locks_guard:
            lock, count = self._locks.get(key, (threading.Lock(), 0))
            self._locks[key] = (lock, count + 1)
        try:
            with lock:
                yield
        finally:
            with self._locks_guard:
                lock, count = self._locks[key]
                if count == 1:
                    del self._locks[key]
                else:
                    self._locks[key] = (lock, count - 1)

    def _read_optional(self, uri: str) -> str | None:
        try:
            return self.upstream.read(uri)
        except UpstreamError as exc:
            if exc.status == 404:
                return None
            raise

    def _metadata(self, uri: str) -> dict:
        try:
            result = json.loads(self.upstream.read(uri.rsplit("/", 1)[0] + "/capture.json"))
        except (ValueError, TypeError):
            raise CaptureError(503, "CAPTURE_METADATA_INVALID") from None
        if not isinstance(result, dict) or not isinstance(result.get("title"), str):
            raise CaptureError(503, "CAPTURE_METADATA_INVALID")
        return result

    def add(self, request: AddRequest, identity: Identity) -> dict:
        if identity.role != "writer":
            raise CaptureError(403, "WRITE_FORBIDDEN")
        capture_id = _sha(_json({"principal": identity.principal, "request_id": request.request_id}))
        root = SCOPE + "/" + capture_id
        uri, metadata_uri = root + "/content.md", root + "/capture.json"
        path = capture_path(uri, {"kind": request.kind, "observed_on": request.observed_on})
        if not allowed(path, identity.write_scopes) or not contains(request.scope if request.scope is not None else "captures", path):
            raise CaptureError(403, "WRITE_SCOPE_FORBIDDEN")
        request_data = request.model_dump(exclude={"scope"})
        if request.observed_on is None:
            request_data.pop("observed_on")  # Preserve old request hashes and retry contracts.
        request_hash = _sha(_json({"principal": identity.principal, **request_data}))
        content_hash = _sha(request.content)
        with self._request_lock(capture_id):
            metadata_text = self._read_optional(metadata_uri)
            if metadata_text is None:
                metadata = {
                    "schema_version": 1, "principal": identity.principal,
                    "request_hash": request_hash, "content_hash": content_hash,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    **{key: value for key, value in request_data.items() if key != "content"},
                }
                # Reserve the complete immutable request before writing its body.
                # The deployment runs one gateway process; the request lock spans
                # reservation and content creation. Native create also rejects an
                # existing target without granting callers any replace operation.
                try:
                    self.upstream.create(metadata_uri, _json(metadata))
                except UpstreamError:
                    # A timeout may occur after the reservation was persisted.
                    metadata_text = self._read_optional(metadata_uri)
                    if metadata_text is None:
                        raise
                else:
                    metadata_text = _json(metadata)
            try:
                metadata = json.loads(metadata_text)
            except (ValueError, TypeError):
                raise CaptureError(503, "CAPTURE_METADATA_INVALID") from None
            if not isinstance(metadata, dict) or metadata.get("request_hash") != request_hash:
                raise CaptureError(409, "REQUEST_ID_CONFLICT")

            existing = self._read_optional(uri)
            status = "exists" if existing is not None else "created"
            index = "pending"
            if existing is not None:
                if existing != request.content:
                    raise CaptureError(409, "CAPTURE_CONTENT_CONFLICT")
                # Retrying an accepted write also retries incomplete derived work.
                try:
                    outcome = self.upstream.reindex(uri)
                    index = "ready" if _index_ready(outcome) else "pending"
                except UpstreamError:
                    pass  # The already stored original remains a successful write.
            else:
                try:
                    outcome = self.upstream.create(uri, request.content, capture=True)
                    index = "ready" if _index_ready(outcome) else "pending"
                except UpstreamError as exc:
                    persisted = self._read_optional(uri)
                    if persisted is None:
                        raise
                    if persisted != request.content:
                        raise CaptureError(409, "CAPTURE_CONTENT_CONFLICT") from None
                    if exc.status == 409:
                        status = "exists"
                    # Content is committed even when embeddings fail or time out.
                    index = "pending"
            return {"status": status, "uri": uri, "path": path, "revision": content_hash, "content_hash": content_hash,
                    "identity": identity.public(), "index": {"status": index}}

    def search(self, request: SearchRequest, identity: Identity) -> dict:
        scope, grants = self._read_scope(request.scope, identity)
        capture_grants = [grant for grant in grants if overlaps(grant, "captures")]
        project_grants = [grant for grant in grants if overlaps(grant, "projects")]
        # The unrestricted legacy query keeps its existing native vector path.
        unrestricted = scope == "captures" and identity.read_scopes is None
        roster = None if unrestricted else self._captures(capture_grants) if capture_grants else []
        permitted = None if roster is None else {entry["uri"]: entry for entry in roster}
        hits = []
        if capture_grants:
            if unrestricted:
                hits = self.upstream.find(request.query, request.limit, SCOPE)
            elif permitted:
                hits = self.upstream.find(request.query, request.limit, SCOPE, allowed_uris=list(permitted))
        items, seen = [], set()
        for hit in hits:
            uri = hit.get("uri") if isinstance(hit, dict) else None
            if not isinstance(uri, str) or not CONTENT_URI.fullmatch(uri) or uri in seen:
                continue
            if permitted is not None and uri not in permitted:
                continue  # Defend against an upstream ignoring the injected allowlist.
            metadata = self._metadata(uri)
            path = self._capture_path(uri, metadata)
            if not any(contains(grant, path) for grant in grants):
                continue
            score = hit.get("score", 0.0)
            if not isinstance(score, (int, float)) or not math.isfinite(score):
                score = 0.0
            abstract = str(hit.get("abstract") or "")
            if not abstract:
                abstract = self.upstream.read(uri)[:512]
            items.append({"uri": uri, "title": metadata["title"],
                          "abstract": abstract, "score": score, "path": path,
                          "revision": metadata.get("content_hash"), "method": "capture_vector"})
            seen.add(uri)
        projects = []
        if project_grants:
            for entry in self.sources.iter_entries(project_grants):
                score = lexical_score(request.query, entry["path"] + "\n" + entry["content"])
                if score:
                    projects.append({"uri": entry["uri"], "path": entry["path"],
                                     "title": entry["source"]["relative_path"],
                                     "abstract": entry["content"][:512], "score": score,
                                     "revision": entry["revision"], "source": entry["source"],
                                     "freshness": "live", "method": "project_lexical"})
            projects.sort(key=lambda entry: (-entry["score"], entry["path"]))
        # Scores have different meanings. Preserve each ranking and interleave
        # explicitly rather than pretending they are comparable probabilities.
        merged = []
        for index in range(max(len(items), len(projects))):
            if index < len(items):
                merged.append(items[index])
            if index < len(projects):
                merged.append(projects[index])
        return {"items": merged[:request.limit], "effective_scope": grants,
                "method": "mixed_round_robin" if capture_grants and project_grants else
                          "project_lexical" if project_grants else "capture_vector",
                "completeness": {"source_scan": "complete" if project_grants else "not_requested",
                                 "capture_enumeration": "complete" if roster is not None and capture_grants else "not_requested",
                                 "capture_index": "coverage_unknown" if capture_grants else "not_requested",
                                 "results_limited": len(merged) > request.limit}}

    def get(self, request: GetRequest, identity: Identity) -> dict:
        if request.uri.startswith("projects/"):
            self._authorize_read(request.uri, request.scope, identity)
            result = self.sources.read(request.uri, request.expected_revision)
            return {**result, "effective_scope": [request.uri]}
        physical_uri = request.uri
        self._read_scope("captures", identity)
        if not CONTENT_URI.fullmatch(physical_uri):
            self._authorize_read(request.uri, request.scope, identity)
            physical_uri = SCOPE + "/" + CAPTURE_PATH.fullmatch(request.uri).group(3) + "/content.md"
        metadata = self._metadata(physical_uri)
        path = self._capture_path(physical_uri, metadata)
        self._authorize_read(path, request.scope, identity)
        if request.uri != physical_uri and request.uri != path:
            raise CaptureError(404, "CAPTURE_ALIAS_NOT_FOUND")
        content = self.upstream.read(physical_uri)
        revision = _sha(content)
        if metadata.get("content_hash") != revision:
            raise CaptureError(503, "CAPTURE_CONTENT_CORRUPT")
        if request.expected_revision is not None and request.expected_revision != revision:
            raise CaptureError(409, "REVISION_MISMATCH")
        return {"uri": physical_uri, "path": path, "content": content, "revision": revision,
                "content_hash": revision, "effective_scope": [path]}

    @staticmethod
    def _capture_path(uri: str, metadata: dict) -> str:
        try:
            return capture_path(uri, metadata)
        except ValueError:
            raise CaptureError(503, "CAPTURE_METADATA_INVALID") from None

    @staticmethod
    def _authorize_read(path: str, requested: str | None, identity: Identity) -> None:
        if not allowed(path, identity.read_scopes) or (requested is not None and not contains(requested, path)):
            raise CaptureError(403, "READ_SCOPE_FORBIDDEN")

    @staticmethod
    def _read_scope(requested: str | None, identity: Identity) -> tuple[str, list[str]]:
        scope = requested if requested is not None else "captures"
        grants = effective_scopes(scope, identity.read_scopes)
        if not grants:
            raise CaptureError(403, "READ_SCOPE_FORBIDDEN")
        return scope, grants

    def _captures(self, grants: list[str]) -> list[dict]:
        if not grants:
            return []
        offset, seen, uris = 0, set(), []
        while True:
            try:
                page = self.upstream.ls(SCOPE, offset=offset, limit=1000)
            except UpstreamError as exc:
                if exc.status == 404 and offset == 0:
                    return []
                raise
            entries = page["entries"]
            if page["has_more"] and not entries:
                raise CaptureError(503, "CAPTURE_SCAN_INCOMPLETE")
            for entry in entries:
                uri = entry.get("uri") if isinstance(entry, dict) else None
                if not isinstance(uri, str) or uri in seen:
                    raise CaptureError(503, "CAPTURE_SCAN_INCOMPLETE")
                seen.add(uri)
                if CONTENT_URI.fullmatch(uri):
                    uris.append(uri)
            if not page["has_more"]:
                break
            offset += len(entries)
        result = []
        for uri in uris:
            metadata = self._metadata(uri)
            path = self._capture_path(uri, metadata)
            if any(contains(grant, path) for grant in grants):
                revision = metadata.get("content_hash")
                if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision):
                    raise CaptureError(503, "CAPTURE_METADATA_INVALID")
                result.append({"uri": uri, "path": path, "title": metadata["title"],
                               "revision": revision, "kind": metadata.get("kind", "note"),
                               "observed_on": metadata.get("observed_on"), "method": "capture_original"})
        return sorted(result, key=lambda entry: entry["path"])

    def list(self, request: ListRequest, identity: Identity) -> dict:
        _, grants = self._read_scope(request.scope, identity)
        entries = self._captures([g for g in grants if overlaps(g, "captures")])
        project_grants = [g for g in grants if overlaps(g, "projects")]
        for entry in self.sources.iter_entries(project_grants) if project_grants else []:
            entries.append({key: value for key, value in entry.items() if key != "content"})
        entries.sort(key=lambda entry: entry["path"])
        fingerprint = _sha(_json({"scope": grants, "entries": entries}))
        offset = 0
        if request.cursor:
            expected, offset_text = request.cursor.split(":")
            if expected != fingerprint:
                raise CaptureError(409, "LIST_CHANGED_RESTART")
            offset = int(offset_text)
            if offset > len(entries):
                raise CaptureError(422, "CURSOR_INVALID")
        page = entries[offset:offset + request.limit]
        next_offset = offset + len(page)
        cursor = f"{fingerprint}:{next_offset}" if next_offset < len(entries) else None
        return {"items": page, "next_cursor": cursor, "effective_scope": grants,
                "completeness": {"enumeration": "complete", "snapshot": "per_request",
                                 "total": len(entries), "has_more": cursor is not None}}


def create_app(upstream: OpenVikingClient | None = None,
               tokens: dict[str, Identity] | None = None,
               sources: SourceRegistry | None = None) -> FastAPI:
    service = CaptureService(upstream or OpenVikingClient.from_env(), sources)
    identities = tokens if tokens is not None else parse_tokens(os.environ.get("MEMORY_BASE_TOKENS", ""))
    if not identities:
        raise ValueError("At least one MEMORY_BASE_TOKENS identity is required")
    app = FastAPI(title="Personal Memory Base", docs_url=None, redoc_url=None)
    app.state.capture_service = service

    def authenticate(request: Request) -> Identity:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, detail="BEARER_TOKEN_REQUIRED", headers={"WWW-Authenticate": "Bearer"})
        for expected, identity in identities.items():
            if secrets.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
                return identity
        raise HTTPException(401, detail="INVALID_TOKEN", headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(CaptureError)
    def capture_error(request: Request, exc: CaptureError):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code}})

    @app.exception_handler(UpstreamError)
    def upstream_error(request: Request, exc: UpstreamError):
        status = 404 if exc.status == 404 else 503
        return JSONResponse(status_code=status, content={"error": {"code": exc.code}})

    @app.exception_handler(SourceError)
    def source_error(request: Request, exc: SourceError):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code}})

    @app.get("/health")
    def health():
        ready = service.upstream.health()
        return JSONResponse(status_code=200 if ready else 503, content={
            "service": "memory-base-gateway", "pid": os.getpid(),
            "status": "ready" if ready else "upstream_unavailable",
            "scope": SCOPE,
        })

    @app.post("/api/memory/add")
    def memory_add(request: AddRequest, identity: Identity = Depends(authenticate)):
        return service.add(request, identity)

    @app.post("/api/memory/search")
    def memory_search(request: SearchRequest, identity: Identity = Depends(authenticate)):
        return service.search(request, identity)

    @app.post("/api/memory/get")
    def memory_get(request: GetRequest, identity: Identity = Depends(authenticate)):
        return service.get(request, identity)

    @app.post("/api/memory/list")
    def memory_list(request: ListRequest, identity: Identity = Depends(authenticate)):
        return service.list(request, identity)

    return app


def main() -> None:
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("MEMORY_BASE_PORT", "8788")))


if __name__ == "__main__":
    main()
