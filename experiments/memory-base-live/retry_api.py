"""Replay actual Bubble capture requests through the gateway after restart."""
import argparse
import json
from pathlib import Path

from dotenv import dotenv_values
from memory_base.credentials import get_secret
from verify import call


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bubble-dir", type=Path, required=True)
    parser.add_argument("--task-id", action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    settings = dotenv_values(args.bubble_dir / ".env")
    bubble = "http://127.0.0.1:" + str(settings.get("PORT") or "8787")
    token = get_secret("host-agent-bubble", settings.get("MEMORY_BASE_HOME"))
    assert token
    report = {"retries": [], "passed": True}
    for task_id in args.task_id:
        status, task = call(bubble + "/api/tasks/" + task_id, settings["AGENT_BUBBLE_TOKEN"])
        assert status == 200
        source, saved = task["source"], task["memorySave"]
        status, receipt = call(settings["MEMORY_BASE_URL"] + "/api/memory/add", token, {
            "request_id": saved["requestId"], "content": task["result"], "title": source["title"],
            "kind": "video_summary" if source["kind"] == "video" else "article_summary",
            "source_url": source.get("url"), "source_id": source["id"], "conversation_id": task["conversationId"],
        })
        assert status == 200 and receipt["status"] == "exists"
        assert receipt["uri"] == saved["uri"] and receipt["content_hash"] == saved["contentHash"]
        assert receipt["index"]["status"] == "ready"
        report["retries"].append({"task_id": task_id, "uri": receipt["uri"],
                                  "status": "exists", "index": "ready", "same_content_hash": True})
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
