"""Small authenticated HTTP client for the pinned OpenViking API.

This module deliberately imports neither OpenViking nor the frozen RAG engine.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any


class UpstreamError(Exception):
    """A sanitized error; upstream messages and credentials never reach callers."""

    def __init__(self, status: int, code: str = "UPSTREAM_UNAVAILABLE"):
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class OpenVikingClient:
    url: str
    key: str = field(repr=False)
    account: str = "personal"
    user: str = "gateway"
    timeout: float = 75.0

    def __post_init__(self) -> None:
        parsed = urllib.parse.urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("MEMORY_BASE_OV_URL must be an HTTP URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("MEMORY_BASE_OV_URL cannot contain credentials, query or fragment")
        if not self.key:
            raise ValueError("MEMORY_BASE_OV_KEY is required")

    @classmethod
    def from_env(cls) -> "OpenVikingClient":
        return cls(
            os.environ.get("MEMORY_BASE_OV_URL", "http://127.0.0.1:8786"),
            os.environ.get("MEMORY_BASE_OV_KEY", ""),
            os.environ.get("MEMORY_BASE_OV_ACCOUNT", "personal"),
            os.environ.get("MEMORY_BASE_OV_USER", "gateway"),
        )

    def call(self, method: str, path: str, *, body: dict | None = None,
             params: dict | None = None) -> Any:
        url = self.url.rstrip("/") + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {
            "Accept": "application/json",
            "X-API-Key": self.key,
            "X-OpenViking-Account": self.account,
            "X-OpenViking-User": self.user,
        }
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        # Local service calls must not be diverted through a machine proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            codes = {404: "NOT_FOUND", 409: "ALREADY_EXISTS", 403: "UPSTREAM_FORBIDDEN"}
            raise UpstreamError(exc.code, codes.get(exc.code, "UPSTREAM_UNAVAILABLE")) from None
        except (OSError, ValueError, urllib.error.URLError):
            raise UpstreamError(503) from None
        if not isinstance(payload, dict) or payload.get("status") not in {"ok", "success"}:
            raise UpstreamError(503, "UPSTREAM_INVALID_RESPONSE")
        if "result" not in payload:
            raise UpstreamError(503, "UPSTREAM_INVALID_RESPONSE")
        return payload["result"]

    def read(self, uri: str) -> str:
        result = self.call("GET", "/api/v1/content/read",
                           params={"uri": uri, "raw": "true", "offset": 0, "limit": -1})
        if not isinstance(result, str):
            raise UpstreamError(503, "UPSTREAM_INVALID_RESPONSE")
        return result

    def create(self, uri: str, content: str, *, capture: bool = False) -> dict:
        result = self.call("POST", "/api/v1/content/write", body={
            "uri": uri, "content": content, "mode": "create", "wait": True,
            "timeout": 60, "processing_mode": "vectors_only",
            "tags": ["memory_base=capture"] if capture else ["memory_base=metadata"],
        })
        if not isinstance(result, dict):
            raise UpstreamError(503, "UPSTREAM_INVALID_RESPONSE")
        return result

    def reindex(self, uri: str) -> dict:
        result = self.call("POST", "/api/v1/content/reindex", body={
            "uri": uri, "mode": "vectors_only", "force": True,
            "wait": True, "recursive": False, "tags": ["memory_base=capture"],
        })
        if not isinstance(result, dict):
            raise UpstreamError(503, "UPSTREAM_INVALID_RESPONSE")
        return result

    def find(self, query: str, limit: int, scope: str) -> list[dict]:
        result = self.call("POST", "/api/v1/search/find", body={
            "query": query, "target_uri": scope, "limit": limit,
            "level": 2, "tags": ["memory_base=capture"],
        })
        if not isinstance(result, dict) or not isinstance(result.get("resources"), list):
            raise UpstreamError(503, "UPSTREAM_INVALID_RESPONSE")
        return result["resources"]

    def health(self) -> bool:
        request = urllib.request.Request(self.url.rstrip("/") + "/health")
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=5) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError):
            return False
