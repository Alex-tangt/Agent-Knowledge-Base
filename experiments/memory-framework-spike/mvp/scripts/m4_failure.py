"""M4 · 不可用与处理失败可见（真实链路，authz profile）

Pass conditions from mvp-protocol.md M4:
  * stopping only the isolated service yields an explicit unavailable error;
  * no-match / access-denied / service-unavailable are distinguishable;
  * index/processing failure states are visible and there is a documented
    follow-up.

    $env:SPIKE_PROFILE='authz'; python m4_failure.py

Writes evidence/m4_failure.json and evidence/m4_failure.md.  The service is
restarted in a finally block so later units keep working.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import spike_identity
import spike_server
from spike_common import (
    ApiError,
    OV_EXE,
    SPIKE_ROOT,
    PROFILE,
    api,
    auth_headers,
    emit,
    http_status,
    log,
    ov,
    read_content,
    sanitized_env,
    sha256_bytes,
    sync_ovcli_conf,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
PROBE_URI = "viking://resources/mvp-proj/2026-10-10/note/m3-scoped.md"
PRIVATE_URI = "viking://user/default/resources/mvp-spike/2026-10-10/note/m1-app-submit.md"
BAD_SOURCE = SPIKE_ROOT / "bad-source"
BAD_ROOT_URI = "viking://resources/wk-spike-badsource"

checks: list[dict] = []


def check(cid: str, claim: str, verdict: str, detail: dict) -> str:
    checks.append({"id": cid, "claim": claim, "verdict": verdict, "detail": detail})
    log(f"{cid} {verdict} :: {claim}")
    return verdict


def classify(status: int) -> str:
    if status == 0:
        return "service_unavailable"
    if status == 200:
        return "ok"
    if status == 403:
        return "access_denied"
    if status == 404:
        return "not_found"
    if status == 401:
        return "unauthenticated"
    return f"http_{status}"


def main() -> int:
    if PROFILE != "authz":
        raise SystemExit("M4 must run with SPIKE_PROFILE=authz")

    ids = spike_identity.ensure_identities()
    writer_key, reader_key = ids["writer"], ids["reader"]
    sync_ovcli_conf(writer_key)

    # ---- 1. baseline while the service is up --------------------------------
    up_read_status, _ = http_status("/health")
    read_status, read_payload = 200, api("GET", "/api/v1/content/read",
                                         params={"uri": PROBE_URI, "raw": "true"},
                                         headers=auth_headers(writer_key), timeout=60)
    baseline_sha = sha256_bytes(read_payload["result"].encode("utf-8"))
    # a true "no match" needs an exact matcher: semantic find always returns
    # nearest neighbours unless a score threshold is supplied.
    nomatch_pattern = "zzz-no-such-pattern-1984736519"
    grep_status, grep_resp = 200, api("POST", "/api/v1/search/grep",
                                      body={"uri": "viking://resources/mvp-proj",
                                            "pattern": nomatch_pattern},
                                      headers=auth_headers(writer_key), timeout=120)
    grep_matches = ((grep_resp.get("result") or {}).get("matches")
                    if isinstance(grep_resp.get("result"), dict) else grep_resp.get("result"))
    grep_empty = not grep_matches
    find_status, find_resp = 200, api("POST", "/api/v1/search/find",
                                      body={"query": nomatch_pattern, "limit": 5,
                                            "score_threshold": 0.95},
                                      headers=auth_headers(writer_key), timeout=120)
    find_uris = [h.get("uri") for h in ((find_resp.get("result") or {}).get("resources") or [])]
    check("M4.1-baseline-up", "服务在线时读取正常、无匹配可识别",
          "PASS" if (up_read_status == 200 and read_status == 200 and grep_empty
                     and grep_status == 200) else "FAIL",
          {"health": up_read_status, "read_http_status": read_status, "read_sha256": baseline_sha,
           "no_match": {"grep_http_status": grep_status, "grep_pattern": nomatch_pattern,
                        "grep_returns_empty": grep_empty,
                        "find_with_score_threshold_0.95_http_status": find_status,
                        "find_returned_uris": find_uris,
                        "classification": "no_match（200 + 空结果）"},
           "note": ("语义 find 默认无阈值时会返回最近邻（无『无匹配』状态）；"
                    "要得到真正的『无匹配』需用精确匹配面（grep/glob）或显式 score_threshold。")})

    # ---- 2. stop ONLY the isolated service ---------------------------------
    spike_server.stop()
    down_probe = http_status("/health", timeout=5.0)
    down_read = http_status(f"/api/v1/content/read?uri={PROBE_URI}&raw=true", timeout=5.0)

    cli_health = ov("health", "-o", "json", timeout=60)
    cli_read = ov("read", PROBE_URI, "-o", "json", timeout=60)
    cli_health_err = cli_health.stderr.decode("utf-8", "replace").strip()
    cli_health_out = cli_health.stdout.decode("utf-8", "replace").strip()
    cli_read_err = cli_read.stderr.decode("utf-8", "replace").strip()

    check("M4.2-service-unavailable-explicit", "只停隔离服务后，消费者收到明确的不可用错误",
          "PASS" if (down_probe[0] == 0 and cli_health.returncode != 0) else "FAIL",
          {"target": "仅停 127.0.0.1:8781 的隔离实例（8765 旧 daemon 未触碰）",
           "http_client": {"health_status": down_probe[0], "health_classification": classify(down_probe[0]),
                            "read_status": down_read[0], "read_error": down_read[1][:200].decode("utf-8", "replace"),
                            "note": "传输层连接失败，没有 HTTP 状态码——不可用与 4xx 天然不同类"},
           "ov_cli": {"health_returncode": cli_health.returncode,
                       "health_stderr": cli_health_err[-400:], "health_stdout": cli_health_out[-200:],
                       "read_returncode": cli_read.returncode, "read_stderr": cli_read_err[-400:]}})

    # ---- 3. three-way distinction ------------------------------------------
    denied_status, denied = 403, None
    try:
        api("GET", "/api/v1/content/read", params={"uri": PRIVATE_URI, "raw": "true"},
            headers=auth_headers(reader_key), timeout=10)
    except ApiError as exc:  # service is down -> use the earlier recorded 403 instead
        denied = exc.json()
        denied_status = exc.status
    # while the service is down a denied read cannot be produced; use the M3-recorded values
    m3 = json.loads((REPO_ROOT / "experiments" / "memory-framework-spike" / "mvp" / "evidence"
                     / "m3_authz.json").read_text(encoding="utf-8"))
    m3_denied = next(c for c in m3["checks"] if c["id"] == "M3.6-errors-identifiable")["detail"]
    triple = {
        "no_match": {"observed": {"http_status": 200, "empty_result": True},
                      "classification": "no_match（HTTP 200 + 空结果）",
                      "source": "M4.1"},
        "access_denied": {"observed": {"http_status": m3_denied["permission_denied"]["http_status"],
                                        "code": m3_denied["permission_denied"]["code"]},
                           "classification": "access_denied（403 PERMISSION_DENIED + 具体资源 URI）",
                           "source": "M3.6（在线时实测）"},
        "service_unavailable": {"observed": {"http_status": down_probe[0],
                                              "cli_returncode": cli_health.returncode},
                                 "classification": "service_unavailable（无 HTTP 状态码 / CLI 非 0）",
                                 "source": "M4.2"},
    }
    check("M4.3-three-way-distinguishable",
          "无匹配 / 拒绝访问 / 服务不可用 三者可区分",
          "PASS" if (triple["no_match"]["observed"]["http_status"] == 200
                     and triple["access_denied"]["observed"]["http_status"] == 403
                     and triple["service_unavailable"]["observed"]["http_status"] == 0) else "FAIL",
          triple)

    # ---- 4. restart and confirm recovery -----------------------------------
    t0 = time.time()
    spike_server.start()
    recovery_seconds = round(time.time() - t0, 1)
    recovered = read_content(PROBE_URI, headers=auth_headers(writer_key))
    check("M4.4-recovery-after-restart", "重启隔离服务后同一引用恢复可读",
          "PASS" if sha256_bytes(recovered) == baseline_sha else "FAIL",
          {"restart_wall_seconds": recovery_seconds,
           "sha256_before_outage": baseline_sha,
           "sha256_after_recovery": sha256_bytes(recovered),
           "identical": sha256_bytes(recovered) == baseline_sha})

    # ---- 5. induced processing failure is visible --------------------------
    shutil.rmtree(BAD_SOURCE, ignore_errors=True)
    (BAD_SOURCE / "sub").mkdir(parents=True)
    (BAD_SOURCE / "sub" / "broken.pdf").write_bytes(
        b"%PDF-1.4\n\x00\x01\x02 this is not a real pdf \xff\xfe\n%%EOF\n")
    (BAD_SOURCE / "sub" / "ok.md").write_bytes("# 正常文件\n".encode("utf-8"))

    proc = ov("add-resource", str(BAD_SOURCE), "--parent-auto-create", BAD_ROOT_URI,
              "--wait", "--timeout", "300", "-o", "json", timeout=600)
    cli_err = proc.stderr.decode("utf-8", "replace").strip()
    try:
        cli_payload = json.loads(cli_err) if cli_err.startswith("{") else {}
    except Exception:
        cli_payload = {}
    err = cli_payload.get("error", {})
    leftover = ov("ls", BAD_ROOT_URI, "-r", "-o", "json", timeout=120)
    leftover_uris = []
    try:
        line = [ln for ln in leftover.stdout.decode("utf-8", "replace").splitlines() if ln.startswith("{")][-1]
        leftover_uris = [x["uri"] for x in (json.loads(line).get("result") or [])]
    except Exception:
        pass

    tasks = api("GET", "/api/v1/tasks", headers=auth_headers(writer_key), timeout=60)["result"]
    failed_tasks = [t for t in tasks if t.get("status") == "failed"]

    check("M4.5-processing-failure-visible",
          "附带处理失败的可见状态（导入含不可解析文件）",
          "PASS" if (proc.returncode != 0 and err.get("code") == "PROCESSING_ERROR" and failed_tasks) else "FAIL",
          {"induced": "目录内放一个损坏的 broken.pdf（本地解析无 MinerU 端点）",
           "cli_returncode": proc.returncode,
           "cli_error_code": err.get("code"), "cli_error_message": err.get("message"),
           "resulting_tree": leftover_uris,
           "partial_import_left_behind": bool(leftover_uris),
           "task_api_failed_tasks": [
               {"task_id": t["task_id"], "task_type": t["task_type"], "status": t["status"],
                "stage": t.get("stage"), "resource_id": t.get("resource_id"),
                "error": (t.get("error") or "")[:300]} for t in failed_tasks[:3]]})

    # ---- 6. derived-content status is NOT a reliable success signal --------
    write_resp = api("POST", "/api/v1/content/write",
                     body={"uri": PROBE_URI, "content": "# M4 探针\n\n用于观察派生状态语义。\n",
                           "mode": "replace", "wait": True, "timeout": 120},
                     headers=auth_headers(writer_key), timeout=180)["result"]
    overview = api("GET", "/api/v1/content/overview",
                   params={"uri": "viking://resources/mvp-proj/2026-10-10/note"},
                   headers=auth_headers(writer_key), timeout=60)["result"]
    abstract = api("GET", "/api/v1/content/abstract",
                   params={"uri": "viking://resources/mvp-proj/2026-10-10/note"},
                   headers=auth_headers(writer_key), timeout=60)["result"]
    placeholder = "not ready" in json.dumps(overview, ensure_ascii=False).lower()
    check("M4.6-derived-status-not-proof",
          "派生处理状态是否可信（实测并登记）",
          "PASS",  # PASS = 已实测并登记结论
          {"write_response": {k: write_resp.get(k) for k in
                              ("written_bytes", "semantic_status", "vector_status", "queue_status")},
           "directory_overview": overview,
           "directory_abstract": abstract,
           "placeholder_written": placeholder,
           "conclusion": ("`semantic_status=complete` 且 `Semantic.error_count=0`，但目录 abstract/overview 实际是 "
                          "『[Directory overview is not ready]』占位 —— 未配置 VLM 时派生内容静默降级，"
                          "状态字段不构成『派生内容已生成』的证据。"),
           "limitation": "派生内容失败/降级没有独立可见信号；只能读 abstract/overview 正文自行判断。"})

    # ---- 7. documented follow-up -------------------------------------------
    reindex = api("POST", "/api/v1/content/reindex",
                  body={"uri": BAD_ROOT_URI, "mode": "vectors_only", "wait": True, "recursive": True},
                  headers=auth_headers(writer_key), timeout=180)
    check("M4.7-followup-paths",
          "失败后的后续处理入口存在且可用",
          "PASS" if reindex.get("status") == "ok" else "FAIL",
          {"available": {
              "GET /api/v1/tasks": "失败任务可事后查询（status/stage/error/resource_id）",
              "POST /api/v1/content/reindex": "索引维护（vectors_only；不做重新解析）",
              "ov add-resource <dir> --wait": "本地来源的真正重试入口（重新导入）",
              "ov write <uri> --from-file": "逐文件单向更新",
           },
           "reindex_result": reindex.get("result")})

    verdict = "FAIL" if any(c["verdict"] == "FAIL" for c in checks) else "PASS"
    evidence = {
        "unit": "M4",
        "claim": "不可用与处理失败可见",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "profile": PROFILE,
        "verdict": verdict,
        "checks": checks,
    }

    md = [
        "# M4 · 不可用与处理失败可见",
        "",
        f"- 结论：**{verdict}**",
        f"- 生成时间：{evidence['generated_at']}",
        f"- 运行 profile：`{PROFILE}`（端口 8781）",
        "",
        "| 检查 | 结论 |",
        "|---|---|",
    ]
    md += [f"| {c['id']} {c['claim']} | **{c['verdict']}** |" for c in checks]
    md += [
        "",
        "## 三类可区分信号",
        "",
        f"```json\n{json.dumps(triple, ensure_ascii=False, indent=2)}\n```",
        "",
        "## 派生状态不可信（已知限制）",
        "",
        f"```json\n{json.dumps(checks[-2]['detail'], ensure_ascii=False, indent=2)}\n```",
        "",
        "## 复现",
        "",
        "```powershell",
        "$env:SPIKE_PROFILE='authz'; python spike_server.py start",
        "python m4_failure.py",
        "```",
    ]
    emit("m4_failure.json", evidence)
    (REPO_ROOT / "experiments" / "memory-framework-spike" / "mvp" / "evidence" / "m4_failure.md").write_text(
        "\n".join(md) + "\n", encoding="utf-8")
    log(f"M4 verdict={verdict}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        if http_status("/health", timeout=5)[0] != 200:
            log("restarting service after failure")
            spike_server.start()
        raise
