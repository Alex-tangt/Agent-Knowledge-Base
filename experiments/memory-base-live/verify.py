"""Read-only acceptance of real Bubble summaries through another KB identity."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import urllib.error
import urllib.request


def call(url: str, token: str | None, body: dict | None = None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=90) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def main() -> None:
    from dotenv import dotenv_values
    from memory_base.credentials import get_secret

    parser = argparse.ArgumentParser()
    parser.add_argument("--bubble-dir", type=Path, required=True)
    parser.add_argument("--task-id", action="append", required=True)
    parser.add_argument("--home", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    settings = dotenv_values(args.bubble_dir / ".env")
    bubble = "http://127.0.0.1:" + str(settings.get("PORT") or "8787")
    gateway = settings.get("MEMORY_BASE_URL") or "http://127.0.0.1:8788"
    reader = get_secret("host-reader", args.home)
    if not reader:
        raise RuntimeError("Reader identity not provisioned")
    checks = []
    summaries = []
    status, _ = call(gateway + "/api/memory/search", None, {"query": "验收"})
    assert status == 401, "Unauthenticated search must be refused"
    checks.append("anonymous_refused")
    status, _ = call(gateway + "/api/memory/add", reader, {
        "request_id": "readonly-acceptance", "content": "reader cannot write",
        "title": "Read-only acceptance", "kind": "note",
    })
    assert status == 403, "Reader must not write"
    checks.append("reader_write_refused")
    for task_id in args.task_id:
        status, task = call(bubble + "/api/tasks/" + task_id, settings["AGENT_BUBBLE_TOKEN"])
        assert status == 200 and task["status"] == "completed", "Real Bubble task did not complete"
        save = task.get("memorySave") or {}
        assert save.get("status") == "saved" and save.get("uri"), "KB save not confirmed"
        status, entry = call(gateway + "/api/memory/get", reader, {"uri": save["uri"]})
        assert status == 200 and entry["content"] == task["result"], "Stored summary differs from Bubble result"
        digest = hashlib.sha256(task["result"].encode()).hexdigest()
        assert save["contentHash"] == digest, "Content hash mismatch"
        disk_result = (args.bubble_dir / "data" / "results" / (task_id + ".md")).read_bytes()
        assert disk_result == task["result"].encode(), "Local result and stored content differ"
        status, retry = call(bubble + "/api/tasks/" + task_id + "/memory/retry",
                             settings["AGENT_BUBBLE_TOKEN"], {})
        assert status == 200 and retry["memorySave"]["uri"] == save["uri"], "Retry changed capture identity"
        source = task["source"]
        query = source["title"][:100]
        status, search = call(gateway + "/api/memory/search", reader, {"query": query, "limit": 20})
        assert status == 200 and any(item["uri"] == save["uri"] for item in search["items"]), "Summary not searchable"
        summaries.append({"task_id": task_id, "source_kind": source["kind"], "uri": save["uri"],
                          "content_hash": digest, "characters": len(task["result"]),
                          "exact_readback": True, "searchable": True, "retry_same_uri": True})
    assert len(summaries) >= 2 and any(x["source_kind"] == "video" for x in summaries), "Video evidence missing"
    assert any(x["source_kind"] == "article" for x in summaries), "Article evidence missing"
    checks.extend(["article_and_video_real_pi", "separate_reader_exact_content", "summary_search", "retry_same_capture"])
    report = {"checks": checks, "summaries": summaries, "passed": True}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
