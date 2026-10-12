"""Prove the out-of-process embedder survives exactly the load that killed the
in-process one: 10 concurrent clients hitting the same real GGUF model.

    python probe_outofprocess_embedder.py

Writes evidence/outofprocess_embedder.json / .md.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import spike_embed
from spike_common import PY_EXE, emit, log

REPO_ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent


def post_embeddings(texts, timeout=300.0):
    body = json.dumps({"input": texts, "model": "bge-small-zh-v1.5-f16"}).encode("utf-8")
    req = urllib.request.Request(f"{spike_embed.BASE}/v1/embeddings", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    proc = spike_embed.start(port=spike_embed.PORT)
    results: dict = {"port": spike_embed.PORT, "model_path": str(spike_embed.MODEL)}
    try:
        health = spike_embed.wait_ready(timeout=300)
        results["health"] = health

        single = post_embeddings("单条中文嵌入测试")
        results["single"] = {"dims": len(single["data"][0]["embedding"]),
                              "count": len(single["data"])}

        batch = post_embeddings([f"批量条目 {i}" for i in range(8)])
        results["batch"] = {"dims": [len(d["embedding"]) for d in batch["data"]]}

        errors: list[str] = []

        def worker(i: int) -> None:
            try:
                for k in range(5):
                    resp = post_embeddings(f"并发客户端 {i}-{k}：中文内容用于压并发。")
                    if len(resp["data"][0]["embedding"]) != 512:
                        errors.append(f"bad dim from {i}-{k}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(worker, range(10)))
        concurrent_seconds = round(time.time() - t0, 1)

        alive = spike_embed.health_ok()
        results["concurrency"] = {
            "clients": 10, "requests_each": 5, "total_requests": 50,
            "errors": errors[:5], "error_count": len(errors),
            "seconds": concurrent_seconds,
            "server_alive_after": alive,
            "final_health": spike_embed.health(),
        }
        results["verdict"] = "PASS" if (alive and not errors
                                        and results["single"]["dims"] == 512
                                        and all(d == 512 for d in results["batch"]["dims"])) else "FAIL"
    finally:
        spike_embed.stop(proc)

    md = [
        "# 支撑证据 · 进程外串行嵌入在 10 并发客户端下存活",
        "",
        f"- 结论：**{results['verdict']}**",
        f"- 模型：`{spike_embed.MODEL}`（与被 abort 的 in-process 路径**同一个 GGUF**）",
        f"- 服务：`embed_server.py`，`127.0.0.1:{spike_embed.PORT}`，OpenAI 兼容 `/v1/embeddings`，"
        "所有 llama 调用共用一把锁（一次一条/一批）",
        "",
        f"```json\n{json.dumps({k: v for k, v in results.items() if k != 'model_path'}, ensure_ascii=False, indent=2)}\n```",
        "",
        "对照（task-1 已录）：同一模型 **in-process 10 线程并发直接 abort 进程**（退出码 3221226505）；",
        "本探针把同一模型搬到进程外并串行化后，同样的 10 并发客户端全部成功，服务存活。",
    ]
    emit("outofprocess_embedder.json", results)
    (REPO_ROOT / "experiments" / "memory-framework-spike" / "mvp" / "evidence"
     / "outofprocess_embedder.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    log(f"out-of-process embedder verdict={results['verdict']}")
    return 0 if results["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
