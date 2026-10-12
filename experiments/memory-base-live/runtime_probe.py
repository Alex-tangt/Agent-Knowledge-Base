"""Exercise owned lifecycle, concurrent native embeddings and failure recovery."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request

from memory_base.credentials import get_secret


def request(url: str, body: dict, token: str | None = None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=90) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path.home() / ".agent-memory-base")
    parser.add_argument("--uri", required=True)
    parser.add_argument("--expected-hash", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads((args.home / "deployment.json").read_text(encoding="utf-8"))
    assert Path(config["python"]).resolve() == Path(sys.executable).resolve(), "Use the dedicated runtime"
    gateway = f"http://127.0.0.1:{config['ports']['gateway']}"
    embed = f"http://127.0.0.1:{config['ports']['embed']}"
    reader = get_secret("host-reader", args.home)
    assert reader
    report = {"checks": [], "versions": config["versions"], "runtime": config["python"],
              "source": config["source"], "uri": args.uri}

    def lifecycle(command: str, *extra: str) -> dict:
        process = subprocess.run([sys.executable, "-m", "memory_base", command, "--home", str(args.home), *extra],
                                 capture_output=True, text=True, encoding="utf-8", timeout=300)
        assert process.returncode == 0, "Owned lifecycle command failed; inspect runtime logs"
        return json.loads(process.stdout)

    def exact_readback() -> None:
        status, body = request(gateway + "/api/memory/get", {"uri": args.uri}, reader)
        assert status == 200 and body["uri"] == args.uri
        assert hashlib.sha256(body["content"].encode()).hexdigest() == args.expected_hash

    initial = lifecycle("status")
    assert initial["status"] == "ready"
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: request(embed + "/v1/embeddings", {
            "model": "bge-small-zh-v1.5-f16", "input": [f"并发验收 {i}", "保存与读取知识"]}), range(4)))
    assert all(status == 200 and len(body["data"]) == 2 and
               all(len(item["embedding"]) == 512 for item in body["data"]) for status, body in results)
    report["checks"].append("four_concurrent_embedding_batches_eight_vectors")
    try:
        lifecycle("stop", "--components", "upstream")
        status, body = request(gateway + "/api/memory/search", {"query": "记忆"}, reader)
        assert status == 503 and "error" in body, "Unavailable must not look like an empty search"
        report["upstream_outage"] = {"http_status": status, "code": body["error"]["code"]}
        report["checks"].append("upstream_outage_explicit_error")
    finally:
        assert lifecycle("start", "--components", "upstream")["status"] == "ready"
    exact_readback()
    report["checks"].append("upstream_recovery_exact_content")
    stopped = lifecycle("stop")
    assert stopped["remaining"] == []
    restarted = lifecycle("start")
    assert restarted["status"] == "ready"
    assert all(restarted["services"][name]["pid"] != initial["services"][name]["pid"]
               for name in initial["services"])
    exact_readback()
    report["checks"].append("all_services_restart_same_uri_and_content")
    report["content_hash"] = args.expected_hash
    report["final_status"] = restarted
    report["passed"] = True
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
