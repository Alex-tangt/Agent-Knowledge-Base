"""Loopback OpenAI embedding endpoint: one locked llama.cpp context per process."""

from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODEL_ID = "bge-small-zh-v1.5-f16"
DEFAULT_MODEL = Path.home() / ".cache/openviking/models/bge-small-zh-v1.5-f16.gguf"
MAX_REQUEST_BYTES = 2 * 1024 * 1024


class EmbeddingState:
    def __init__(self, model):
        self.model = model
        self.lock = threading.Lock()
        self.calls = 0
        self.items = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        with self.lock:
            result = self.model.create_embedding(texts)
            self.calls += 1
            self.items += len(texts)
            return [entry["embedding"] for entry in result["data"]]


def handler_for(state: EmbeddingState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def send_json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self.send_json(200, {"status": "ok", "service": "memory-base-embedding",
                                     "model": MODEL_ID, "serialized": True, "pid": os.getpid(),
                                     "calls": state.calls, "items": state.items})
            elif self.path == "/v1/models":
                self.send_json(200, {"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]})
            else:
                self.send_json(404, {"error": {"message": "not found"}})

        def do_POST(self):
            if self.path != "/v1/embeddings":
                self.send_json(404, {"error": {"message": "not found"}})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_REQUEST_BYTES:
                    raise ValueError("request size is outside the allowed range")
                payload = json.loads(self.rfile.read(length))
                texts = payload.get("input")
                if isinstance(texts, str):
                    texts = [texts]
                if not isinstance(texts, list) or not texts or not all(isinstance(t, str) and t.strip() for t in texts):
                    raise ValueError("input must contain nonempty strings")
                vectors = state.embed(texts)
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                self.send_json(400, {"error": {"message": "invalid embedding request"}})
                return
            except Exception:
                self.send_json(500, {"error": {"message": "embedding failed; consult service logs"}})
                return
            self.send_json(200, {"object": "list", "model": MODEL_ID,
                                 "data": [{"object": "embedding", "index": i, "embedding": vector}
                                          for i, vector in enumerate(vectors)],
                                 "usage": {"prompt_tokens": 0, "total_tokens": 0}})

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8796)
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    args = parser.parse_args()
    from llama_cpp import Llama
    state = EmbeddingState(Llama(model_path=args.model, embedding=True, verbose=False))
    ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(state)).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
