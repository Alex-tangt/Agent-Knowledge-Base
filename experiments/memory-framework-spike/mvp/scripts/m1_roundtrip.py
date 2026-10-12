"""M1 · 应用输入原样保存、跨消费者共享（真实链路）

Checks, all against the isolated instance on 127.0.0.1:8781:
  1. submit through the public entry (POST /api/v1/content/write, mode=create)
  2. original bytes land on disk unchanged            (disk-face comparison)
  3. original bytes read back through the write/read API unchanged (interface-face)
  4. a second, independent consumer reads the same reference -> same bytes
  5. survives a real service restart
  6. re-submitting the same request does not create a second entry

    python m1_roundtrip.py [--uri URI] [--reset] [--skip-restart]

Writes evidence/m1_roundtrip.json and evidence/m1_roundtrip.md.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import spike_server
from spike_common import (
    ApiError,
    BASE_URL,
    LOGS,
    OV_EXE,
    WORKSPACE,
    api,
    disk_path_for_uri,
    emit,
    http_status,
    log,
    ov_json,
    read_content,
    sanitized_env,
    sha256_bytes,
    sha256_file,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PAYLOAD = REPO_ROOT / "experiments" / "memory-framework-spike" / "mvp" / "payload" / "m1-app-submit.md"
DEFAULT_URI = "viking://user/default/resources/mvp-spike/2026-10-10/note/m1-app-submit.md"

checks: list[dict] = []


def check(cid: str, claim: str, passed: bool, detail: dict) -> bool:
    checks.append({"id": cid, "claim": claim, "verdict": "PASS" if passed else "FAIL", "detail": detail})
    log(f"{cid} {'PASS' if passed else 'FAIL'} :: {claim}")
    return passed


def list_entries(parent_uri: str) -> list[dict]:
    payload = ov_json("ls", parent_uri, "-o", "json", "-r")
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, list):
        return []
    return [item for item in result if not item.get("isDir")]


def consumer_cli(uri: str) -> bytes:
    proc = subprocess.run([str(OV_EXE), "read", uri, "-o", "json"], capture_output=True,
                          env=sanitized_env(), timeout=180)
    text = proc.stdout.decode("utf-8", "replace").strip()
    line = [ln for ln in text.splitlines() if ln.startswith("{")][-1]
    return json.loads(line)["result"].encode("utf-8")


def consumer_download(uri: str) -> bytes:
    return api("GET", "/api/v1/content/download", params={"uri": uri}, raw=True, timeout=180)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", default=DEFAULT_URI)
    ap.add_argument("--payload", default=str(DEFAULT_PAYLOAD))
    ap.add_argument("--reset", action="store_true", help="delete the test subtree first")
    ap.add_argument("--skip-restart", action="store_true")
    args = ap.parse_args()

    uri = args.uri
    parent = uri.rsplit("/", 1)[0]
    payload_path = Path(args.payload)
    raw = payload_path.read_bytes()
    text = raw.decode("utf-8")
    payload_sha = sha256_bytes(raw)
    log(f"payload={payload_path} bytes={len(raw)} sha256={payload_sha}")

    if args.reset:
        subprocess.run([str(OV_EXE), "rm", parent, "--recursive", "--wait", "--timeout", "120", "-o", "json"],
                       capture_output=True, env=sanitized_env(), timeout=300)

    started = time.time()

    # ---- 1. submit through the public entry ---------------------------------
    submit = api("POST", "/api/v1/content/write",
                 body={"uri": uri, "content": text, "mode": "create", "wait": True, "timeout": 300},
                 timeout=330)
    check("M1.1-submit", "公开入口提交成功且报告写入字节数", submit.get("status") == "ok",
          {"request": {"method": "POST", "path": "/api/v1/content/write",
                       "body": {"uri": uri, "mode": "create", "wait": True,
                                "content_bytes": len(raw), "content_sha256": payload_sha}},
           "response": submit})

    # ---- 2. disk face -------------------------------------------------------
    disk = disk_path_for_uri(uri)
    disk_bytes = disk.read_bytes() if disk and disk.exists() else b""
    check("M1.2-disk-bytes", "磁盘上存下来的字节 == 提交输入字节",
          disk_bytes == raw,
          {"disk_path": str(disk), "disk_bytes": len(disk_bytes), "input_bytes": len(raw),
           "disk_sha256": sha256_bytes(disk_bytes), "input_sha256": payload_sha,
           "byte_identical": disk_bytes == raw})

    # ---- 3. interface face --------------------------------------------------
    api_bytes = read_content(uri)
    check("M1.3-api-bytes", "写/读接口取回的正文 == 提交输入字节",
          api_bytes == raw,
          {"endpoint": "GET /api/v1/content/read?raw=true", "bytes": len(api_bytes),
           "sha256": sha256_bytes(api_bytes), "byte_identical": api_bytes == raw})

    download_bytes = consumer_download(uri)
    check("M1.4-download-bytes", "原始字节下载端点 == 提交输入字节",
          download_bytes == raw,
          {"endpoint": "GET /api/v1/content/download", "bytes": len(download_bytes),
           "sha256": sha256_bytes(download_bytes), "byte_identical": download_bytes == raw})

    # ---- 4. second consumer -------------------------------------------------
    cli_bytes = consumer_cli(uri)
    check("M1.5-second-consumer", "第二消费者（独立进程 ov CLI）读同一引用得到同一正文",
          cli_bytes == raw,
          {"consumer": "ov read <uri> -o json (separate process)", "bytes": len(cli_bytes),
           "sha256": sha256_bytes(cli_bytes), "byte_identical": cli_bytes == raw})

    # ---- 5. restart ---------------------------------------------------------
    if not args.skip_restart:
        before_entries = list_entries(parent)
        spike_server.stop()
        t0 = time.time()
        spike_server.start()
        restart_seconds = round(time.time() - t0, 1)
        status, _ = http_status("/health", timeout=10)
        after_restart = read_content(uri)
        check("M1.6-survives-restart", "服务重启后同一引用仍读出同一正文",
              status == 200 and after_restart == raw,
              {"stop_start_ok": True, "restart_seconds": restart_seconds,
               "health_after": status, "bytes": len(after_restart),
               "sha256": sha256_bytes(after_restart), "byte_identical": after_restart == raw,
               "entries_before_restart": len(before_entries),
               "entries_after_restart": len(list_entries(parent))})
    else:
        check("M1.6-survives-restart", "服务重启后同一引用仍读出同一正文", False,
              {"skipped": True})

    # ---- 6. duplicate submission -------------------------------------------
    entries_before = list_entries(parent)
    dup_create_error = None
    try:
        api("POST", "/api/v1/content/write",
            body={"uri": uri, "content": text, "mode": "create", "wait": True, "timeout": 300},
            timeout=330)
        dup_create_status = 200
    except ApiError as exc:
        dup_create_status = exc.status
        dup_create_error = exc.json()

    replace = api("POST", "/api/v1/content/write",
                  body={"uri": uri, "content": text, "mode": "replace", "wait": True, "timeout": 300},
                  timeout=330)
    entries_after = list_entries(parent)
    after_dup_bytes = read_content(uri)

    check("M1.7-duplicate-no-second-entry", "同一请求重复提交不产生第二条",
          dup_create_status in (409, 400, 200) and len(entries_before) == len(entries_after) == 1
          and after_dup_bytes == raw,
          {"entries_before": len(entries_before), "entries_after": len(entries_after),
           "entries": [e.get("uri") for e in entries_after],
           "duplicate_mode_create": {"http_status": dup_create_status, "error": dup_create_error},
           "duplicate_mode_replace": {"status": replace.get("status"),
                                      "written_bytes": replace.get("result", {}).get("written_bytes")},
           "body_unchanged_after_duplicate": after_dup_bytes == raw,
           "body_sha256_after_duplicate": sha256_bytes(after_dup_bytes)})

    # ---- processing side effects / cost ------------------------------------
    queue = submit.get("result", {}).get("queue_status", {})
    side_effects = {
        "semantic_status": submit.get("result", {}).get("semantic_status"),
        "vector_status": submit.get("result", {}).get("vector_status"),
        "written_bytes": submit.get("result", {}).get("written_bytes"),
        "embedding_items_processed": queue.get("Embedding", {}).get("processed"),
        "semantic_items_processed": queue.get("Semantic", {}).get("processed"),
        "cloud_model_calls": 0,
        "cloud_model_cost": 0.0,
        "note": ("embedding provider = local GGUF (bge-small-zh-v1.5-f16); no VLM provider is "
                 "configured, so the semantic/overview step completes without any model call and "
                 "writes '[Directory overview is not ready]' placeholders. 原文保存与读取不经过 LLM。"),
    }

    evidence = {
        "unit": "M1",
        "claim": "应用输入原样保存、跨消费者共享、重启后可读、重复提交不产生第二条",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "uri": uri,
        "payload": {"path": str(payload_path), "bytes": len(raw), "sha256": payload_sha},
        "verdict": "PASS" if all(c["verdict"] == "PASS" for c in checks) else "FAIL",
        "checks": checks,
        "processing_side_effects": side_effects,
        "elapsed_seconds": round(time.time() - started, 1),
    }

    md_lines = [
        "# M1 · 应用输入原样保存、跨消费者共享",
        "",
        f"- 结论：**{evidence['verdict']}**",
        f"- 生成时间：{evidence['generated_at']}",
        f"- 引用：`{uri}`",
        f"- 输入：`{payload_path}` · {len(raw)} bytes · sha256 `{payload_sha}`",
        "- 环境：隔离实例 http://127.0.0.1:8781（本地 GGUF embedding，无云模型）",
        "",
        "| 检查 | 结论 | 关键证据 |",
        "|---|---|---|",
    ]
    for c in checks:
        key = {k: v for k, v in c["detail"].items() if k in
               ("disk_sha256", "sha256", "byte_identical", "bytes", "entries_after",
                "http_status", "restart_seconds", "body_unchanged_after_duplicate")}
        md_lines.append(f"| {c['id']} {c['claim']} | **{c['verdict']}** | `{json.dumps(key, ensure_ascii=False)}` |")
    md_lines += [
        "",
        "## 两处原文比对",
        "",
        f"- 磁盘：`{disk}` → {len(disk_bytes)} bytes，sha256 `{sha256_bytes(disk_bytes)}`，与输入逐字节相同："
        f"{disk_bytes == raw}",
        f"- 接口：`GET /api/v1/content/read?raw=true` → {len(api_bytes)} bytes，sha256 `{sha256_bytes(api_bytes)}`，与输入逐字节相同："
        f"{api_bytes == raw}",
        f"- 原始字节端点：`GET /api/v1/content/download` → {len(download_bytes)} bytes，逐字节相同：{download_bytes == raw}",
        "",
        "## 附带处理与成本",
        "",
        f"```json\n{json.dumps(side_effects, ensure_ascii=False, indent=2)}\n```",
        "",
        "## 复现",
        "",
        "```powershell",
        ". ./spike-env.ps1",
        "python spike_server.py start",
        "python m1_roundtrip.py --reset",
        "```",
    ]
    emit("m1_roundtrip.json", evidence)
    (REPO_ROOT / "experiments" / "memory-framework-spike" / "mvp" / "evidence" / "m1_roundtrip.md").write_text(
        "\n".join(md_lines) + "\n", encoding="utf-8")
    log(f"M1 verdict={evidence['verdict']} in {evidence['elapsed_seconds']}s")
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
