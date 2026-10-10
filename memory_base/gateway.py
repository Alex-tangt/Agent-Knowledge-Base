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
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from memory_base.upstream import OpenVikingClient, UpstreamError

SCOPE = "viking://resources/captures"
CONTENT_URI = re.compile(r"^viking://resources/captures/[0-9a-f]{64}/content\.md$")


class AddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1, max_length=2_000_000)
    title: str = Field(min_length=1, max_length=500)
    kind: Literal["note", "article_summary", "video_summary"] = "note"
    source_url: str | None = Field(default=None, max_length=4096)
    source_id: str | None = Field(default=None, max_length=512)
    conversation_id: str | None = Field(default=None, max_length=512)

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

    @field_validator("query")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class GetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uri: str

    @field_validator("uri")
    @classmethod
    def capture_uri(cls, value: str) -> str:
        if not CONTENT_URI.fullmatch(value):
            raise ValueError("uri must name a capture content.md")
        return value


@dataclass(frozen=True)
class Identity:
    principal: str
    role: Literal["writer", "reader"]

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
            result[token] = Identity(principal, role)
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
    def __init__(self, upstream: OpenVikingClient):
        self.upstream = upstream
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
        request_data = request.model_dump()
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
            return {"status": status, "uri": uri, "content_hash": content_hash,
                    "identity": identity.public(), "index": {"status": index}}

    def search(self, request: SearchRequest, identity: Identity) -> dict:
        hits = self.upstream.find(request.query, request.limit, SCOPE)
        items, seen = [], set()
        for hit in hits:
            uri = hit.get("uri") if isinstance(hit, dict) else None
            if not isinstance(uri, str) or not CONTENT_URI.fullmatch(uri) or uri in seen:
                continue
            metadata = self._metadata(uri)
            score = hit.get("score", 0.0)
            if not isinstance(score, (int, float)) or not math.isfinite(score):
                score = 0.0
            abstract = str(hit.get("abstract") or "")
            if not abstract:
                abstract = self.upstream.read(uri)[:512]
            items.append({"uri": uri, "title": metadata["title"],
                          "abstract": abstract, "score": score})
            seen.add(uri)
        return {"items": items[:request.limit]}

    def get(self, request: GetRequest, identity: Identity) -> dict:
        return {"uri": request.uri, "content": self.upstream.read(request.uri)}


def create_app(upstream: OpenVikingClient | None = None,
               tokens: dict[str, Identity] | None = None) -> FastAPI:
    service = CaptureService(upstream or OpenVikingClient.from_env())
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

    return app


def main() -> None:
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("MEMORY_BASE_PORT", "8788")))


if __name__ == "__main__":
    main()
