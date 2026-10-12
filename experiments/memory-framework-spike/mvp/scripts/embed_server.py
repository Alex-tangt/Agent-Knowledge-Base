"""Minimal out-of-process, serialized, OpenAI-compatible embedding server.

Why: OpenViking 0.4.23's built-in local GGUF embedder shares one llama.cpp
context and is **not safe under concurrency** -- concurrent calls trigger a
native GGML_ASSERT and abort the whole server process (proved standalone in
probe_local_embedder_concurrency.py).  Running the very same GGUF behind an
HTTP boundary, with one lock around every llama call, keeps the model real while
removing the unsafe sharing.

    python embed_server.py [--port 8791] [--model <gguf path>]

Endpoints:
    POST /v1/embeddings   {"input": str | [str], "model": "..."} -> OpenAI shape
    GET  /health          {"status":"ok"}
    GET  /v1/models       {"object":"list","data":[{"id":...}]}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_MODEL = Path(os.path.expanduser("~")) / ".cache" / "openviking" / "models" / "bge-small-zh-v1.5-f16.gguf"
MODEL_ID = "bge-small-zh-v1.5-f16"


def log(msg: str) -> None:
    print(f"[embed-server] {msg}", file=sys.stderr, flush=True)


class State:
    llm = None
    lock = threading.Lock()  # serializes every llama call
    calls = 0
    items = 0


def embed(texts: list[str]) -> list[list[float]]:
    """One llama call per request, holding the lock for the whole batch."""
    with State.lock:
        out = State.llm.create_embedding(texts)
        State.calls += 1
        State.items += len(texts)
        return [d["embedding"] for d in out["data"]]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # stderr only, keep it quiet
        log(fmt % args)

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/health"):
            self._send(200, {"status": "ok", "calls": State.calls, "items": State.items,
                             "serialized": True, "model": MODEL_ID})
        elif self.path.startswith("/v1/models"):
            self._send(200, {"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]})
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):  # noqa: N802
        if not self.path.startswith("/v1/embeddings"):
            self._send(404, {"error": {"message": "not found"}})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._send(400, {"error": {"message": f"bad json: {exc}"}})
            return
        raw_input = payload.get("input")
        if isinstance(raw_input, str):
            texts = [raw_input]
        elif isinstance(raw_input, list):
            texts = [t if isinstance(t, str) else str(t) for t in raw_input]
        else:
            self._send(400, {"error": {"message": "input must be a string or list of strings"}})
            return
        try:
            vectors = embed(texts)
        except Exception as exc:  # noqa: BLE001
            log(f"embedding failed: {type(exc).__name__}: {exc}")
            self._send(500, {"error": {"message": str(exc)}})
            return
        self._send(200, {
            "object": "list",
            "data": [{"object": "embedding", "index": i, "embedding": v}
                     for i, v in enumerate(vectors)],
            "model": MODEL_ID,
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        })


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("SPIKE_EMBED_PORT", "8791")))
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    args = ap.parse_args()

    from llama_cpp import Llama
    log(f"loading {args.model}")
    State.llm = Llama(model_path=args.model, embedding=True, verbose=False)
    log(f"ready on 127.0.0.1:{args.port} (single lock around every llama call)")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
