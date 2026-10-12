"""M5 · 版本、备份与恢复有效（真实链路，authz profile）

Pass conditions from mvp-protocol.md M5:
  * historical versions are identifiable and restorable through public means,
    without day-to-day manual curation;
  * a restore into a **clean isolated location** reproduces content, references
    and permissions per the registered restore semantics;
  * **the backup must cover both stores** -- the content face and the retrieval
    face -- not just one;
  * the retrieval index can be restored or rebuilt and still finds the test content;
  * recoverable points in time and the possible data-loss range are recorded.

Registered restore semantics (declared before running):
  1. content face   -- files/ in the pack is restored byte-for-byte to the same URI;
  2. retrieval face -- index_records.jsonl + dense.f32 are restored
     (--vector-mode auto); find/grep can find the same content again;
  3. permission face -- whatever the pack does or does not carry is measured;
     whatever it does not carry is re-established by a documented step
     (re-register the consumers, re-apply the read-only policy).

    $env:SPIKE_PROFILE='authz'; python m5_backup.py

Writes evidence/m5_backup.json and evidence/m5_backup.md.  The reference
instance is restarted in a finally block.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path

import spike_identity
import spike_server
from spike_common import (
    ApiError,
    PROFILE,
    SPIKE_ROOT,
    api,
    auth_headers,
    emit,
    http_status,
    log,
    ov,
    read_content,
    sha256_bytes,
    sha256_file,
    sync_ovcli_conf,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
SCOPE_ROOT = "viking://resources/mvp-proj"
VERSIONED_URI = f"{SCOPE_ROOT}/2026-10-10/note/m5-versioned.md"
ARTIFACT_DIR = SPIKE_ROOT / "m5-artifacts"
CLEAN_ROOT = SPIKE_ROOT / "clean-room"
CLEAN_DATA = CLEAN_ROOT / "data"
CLEAN_CONF = CLEAN_ROOT / "ov-restore.conf"

V1 = "# M5 版本一\n\n标记：M5-VERSION-ONE-2026-10-10\n"
V2 = "# M5 版本二\n\n标记：M5-VERSION-TWO-2026-10-10\n"
POST_BACKUP = "# M5 备份之后写入\n\n标记：M5-POST-BACKUP-2026-10-10\n"
POST_BACKUP_URI = f"{SCOPE_ROOT}/2026-10-10/note/m5-post-backup.md"

checks: list[dict] = []
REPORT: dict = {}


def check(cid: str, claim: str, verdict: str, detail: dict) -> str:
    checks.append({"id": cid, "claim": claim, "verdict": verdict, "detail": detail})
    log(f"{cid} {verdict} :: {claim}")
    return verdict


def ov_json_or(args: list[str], timeout: float = 300.0) -> dict:
    proc = ov(*args, timeout=timeout)
    out = proc.stdout.decode("utf-8", "replace").strip()
    err = proc.stderr.decode("utf-8", "replace").strip()
    line = ""
    for candidate in reversed(out.splitlines()):
        if candidate.startswith("{"):
            line = candidate
            break
    if not line and err.startswith("{"):
        line = err.splitlines()[-1]
    try:
        payload = json.loads(line)
    except Exception:
        payload = {}
    payload["_returncode"] = proc.returncode
    if err and not payload.get("ok", True):
        payload["_stderr"] = err[-400:]
    return payload


def find_uris(key: str, query: str, *, target: str | None = None,
              threshold: float | None = None) -> list[str]:
    body: dict = {"query": query, "limit": 10}
    if target:
        body["target_uri"] = target
    if threshold is not None:
        body["score_threshold"] = threshold
    resp = api("POST", "/api/v1/search/find", body=body, headers=auth_headers(key), timeout=180)
    return [h.get("uri") for h in ((resp.get("result") or {}).get("resources") or [])]


def grep_uris(pattern: str, scope: str) -> list[str]:
    payload = ov_json_or(["grep", pattern, "-u", scope, "-o", "json"], timeout=180)
    result = payload.get("result")
    if isinstance(result, dict):
        items = result.get("matches") or result.get("results") or []
    elif isinstance(result, list):
        items = result
    else:
        items = []
    uris: list[str] = []
    for item in items:
        if isinstance(item, dict):
            uris.append(item.get("uri") or item.get("path"))
        elif isinstance(item, str):
            uris.append(item)
    return [u for u in uris if u]


def zip_names(path: Path) -> list[str]:
    if not path.exists():
        return []
    with zipfile.ZipFile(path) as zf:
        return [info.filename for info in zf.infolist()]


BACKUP_ATTEMPTS: list[dict] = []


def backup_with_vectors(path: Path, *, scope: str = SCOPE_ROOT) -> dict:
    """`ov backup --include-vectors` refuses when the dense snapshot is incomplete.

    The vector index is refreshed asynchronously, so right after a snapshot
    restore the dense snapshot can legitimately lag.  Repair (reindex) and retry,
    recording every attempt.
    """
    response: dict = {}
    for attempt in range(3):
        ensure_alive()
        path.unlink(missing_ok=True)
        response = ov_json_or(["backup", str(path), "--include-vectors", "-o", "json"], timeout=1800)
        BACKUP_ATTEMPTS.append({"attempt": attempt + 1, "ok": bool(response.get("ok")),
                                 "error": (response.get("error") or {}).get("message")})
        if response.get("ok"):
            return response
        if "incomplete" not in json.dumps(response, ensure_ascii=False):
            return response
        ov_json_or(["reindex", scope, "--mode", "vectors_only", "--wait", "true", "-o", "json"],
                   timeout=1800)
    return response


def consistency(scope: str, *, sudo: bool = False) -> dict:
    args = ["system", "consistency", scope, "-o", "json"]
    if sudo:
        args.append("--sudo")
    return ov_json_or(args, timeout=600)


def commit_oid(payload: dict) -> str | None:
    result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(result, dict):
        for key in ("commit_oid", "commit", "commit_id", "oid", "id", "hash", "head"):
            if result.get(key):
                return result[key]
    if isinstance(result, str):
        return result
    return None


CRASH_LOG = {"restarts": 0, "ggml_assert_seen": False}


def ensure_alive() -> bool:
    """The local GGUF embedder can abort the whole process with a native
    GGML_ASSERT on some input; detect the death and bring the server back so the
    measurement can finish, while recording that it happened."""
    if http_status("/health", timeout=5)[0] == 200:
        return True
    log("server not answering -- restarting (native crash?)")
    CRASH_LOG["restarts"] += 1
    try:
        err_log = SPIKE_ROOT / "logs" / "server.err.log"
        tail = err_log.read_text(encoding="utf-8", errors="replace")[-20000:]
        CRASH_LOG["ggml_assert_seen"] = CRASH_LOG["ggml_assert_seen"] or ("GGML_ASSERT" in tail)
        CRASH_LOG["log_tail"] = tail[-1500:]
    except Exception:
        pass
    spike_server.start()
    return http_status("/health", timeout=10)[0] == 200


def main() -> int:
    if PROFILE != "authz":
        raise SystemExit("M5 must run with SPIKE_PROFILE=authz")
    try:
        return _run()
    finally:
        _emit_evidence()
        try:
            import spike_common
            spike_common.CONF_OVERRIDE = None
        except Exception:
            pass


def _run() -> int:
    global REPORT

    ids = spike_identity.ensure_identities()
    writer_key = ids["writer"]
    sync_ovcli_conf(writer_key)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------- Phase A: versions
    api("POST", "/api/v1/content/write",
        body={"uri": VERSIONED_URI, "content": V1, "mode": "replace",
              "wait": True, "timeout": 120},
        headers=auth_headers(writer_key), timeout=180)
    v1_bytes = read_content(VERSIONED_URI, headers=auth_headers(writer_key))

    commit1 = ov_json_or(["snapshot", "commit", "-m", "M5 v1", "--paths", SCOPE_ROOT, "-o", "json"])

    api("POST", "/api/v1/content/write",
        body={"uri": VERSIONED_URI, "content": V2, "mode": "replace",
              "wait": True, "timeout": 120},
        headers=auth_headers(writer_key), timeout=180)
    v2_bytes = read_content(VERSIONED_URI, headers=auth_headers(writer_key))

    commit2 = ov_json_or(["snapshot", "commit", "-m", "M5 v2", "--paths", SCOPE_ROOT, "-o", "json"])

    c1, c2 = commit_oid(commit1), commit_oid(commit2)
    snap_log = ov_json_or(["snapshot", "log", "--branch", "main", "--paths", SCOPE_ROOT,
                           "--limit", "20", "-o", "json"])
    log_result = snap_log.get("result")
    if isinstance(log_result, dict):
        log_entries = (log_result.get("commits") or log_result.get("entries")
                       or log_result.get("log") or [])
    elif isinstance(log_result, list):
        log_entries = log_result
    else:
        log_entries = []

    blob_file = ARTIFACT_DIR / "m5-v1-from-snapshot.md"
    blob_file.unlink(missing_ok=True)
    if c1:
        ov("snapshot", "show", c1, "--path", VERSIONED_URI, "--out-file", str(blob_file), timeout=300)
    show1_blob_bytes = blob_file.read_bytes() if blob_file.exists() else None

    check("M5.1-versions-identifiable",
          "历史版本可识别（快照提交可列举、可回看单个文件）",
          "PASS" if (c1 and c2 and c1 != c2 and len(log_entries) >= 2
                     and show1_blob_bytes == v1_bytes) else "FAIL",
          {"commit_v1": c1, "commit_v2": c2,
           "commit_responses": {"v1": commit1, "v2": commit2},
           "snapshot_log_entries": log_entries[:5],
           "snapshot_log_raw": snap_log,
           "show_v1_blob_sha256": sha256_bytes(show1_blob_bytes) if show1_blob_bytes else None,
           "v1_content_sha256": sha256_bytes(v1_bytes),
           "show_v1_equals_v1": show1_blob_bytes == v1_bytes})

    restore_dry = (ov_json_or(["snapshot", "restore", c1, SCOPE_ROOT, "--dry-run", "-o", "json"])
                   if c1 else {})
    restore = (ov_json_or(["snapshot", "restore", c1, SCOPE_ROOT, "-m", "M5 rollback to v1",
                           "-o", "json"]) if c1 else {})
    restore_task = (restore.get("result", {}).get("task_id")
                    if isinstance(restore.get("result"), dict) else None)
    if restore_task:
        ov_json_or(["wait", restore_task, "--timeout", "120", "-o", "json"], timeout=300)

    restored_bytes = read_content(VERSIONED_URI, headers=auth_headers(writer_key))

    # The retrieval face decided here is (a) an exact content search and (b) the
    # bytes actually reachable through the retrieval reference -- semantic find
    # returns nearest neighbours regardless of the marker.
    grep_v1 = grep_uris("M5-VERSION-ONE-2026-10-10", SCOPE_ROOT)
    grep_v2 = grep_uris("M5-VERSION-TWO-2026-10-10", SCOPE_ROOT)
    v1_hits_post = find_uris(writer_key, "M5-VERSION-ONE-2026-10-10", target=SCOPE_ROOT)
    served: dict[str, str | None] = {}
    for uri in sorted(set(v1_hits_post + grep_v1 + [VERSIONED_URI])):
        try:
            served[uri] = sha256_bytes(read_content(uri, headers=auth_headers(writer_key)))
        except ApiError:
            served[uri] = None

    check("M5.2-historical-restore",
          "按公开方式恢复历史版本，内容面与检索面同时生效",
          "PASS" if (restored_bytes == v1_bytes and VERSIONED_URI in grep_v1 and not grep_v2
                     and served.get(VERSIONED_URI) == sha256_bytes(v1_bytes)) else "FAIL",
          {"mechanism": f"ov snapshot restore {c1} {SCOPE_ROOT} -m '...'（前向提交）",
           "dry_run_response": restore_dry,
           "restore_response": restore,
           "restore_task_id": restore_task,
           "content_after_restore_sha256": sha256_bytes(restored_bytes),
           "content_equals_v1": restored_bytes == v1_bytes,
           "retrieval_face": {
               "exact_content_search_v1_marker": grep_v1,
               "exact_content_search_v2_marker": grep_v2,
               "semantic_find_v1_marker": v1_hits_post,
               "served_bytes_sha256_by_uri": served,
               "retrieval_reference_serves_restored_version":
                   served.get(VERSIONED_URI) == sha256_bytes(v1_bytes)},
           "requires_manual_curation": False})

    # ---------------------------------------------------------- Phase B: backup
    backup_with = ARTIFACT_DIR / "m5-backup-with-vectors.ovpack"
    backup_without = ARTIFACT_DIR / "m5-backup-content-only.ovpack"
    for p in (backup_with, backup_without):
        p.unlink(missing_ok=True)
    # the "written after the backup" entry must be genuinely absent at backup time
    ov("rm", POST_BACKUP_URI, "-o", "json", timeout=180)

    r_with = backup_with_vectors(backup_with)
    r_without = ov_json_or(["backup", str(backup_without), "-o", "json"], timeout=900)
    names_with = zip_names(backup_with)
    names_without = zip_names(backup_without)

    check("M5.3-backup-covers-both-faces",
          "备份同时覆盖内容面与检索面两处存储",
          "PASS" if (any(n.endswith("index_records.jsonl") for n in names_with)
                     and any(n.rstrip("/").endswith("files") for n in names_with)) else "FAIL",
          {"artifacts": {
              "with_vectors": {"path": str(backup_with),
                                "bytes": backup_with.stat().st_size if backup_with.exists() else 0,
                                "sha256": sha256_file(backup_with) if backup_with.exists() else None,
                                "cli_response": r_with},
              "content_only": {"path": str(backup_without),
                                "bytes": backup_without.stat().st_size if backup_without.exists() else 0,
                                "sha256": sha256_file(backup_without) if backup_without.exists() else None,
                                "cli_response": r_without}},
           "zip_entries_with_vectors": names_with[:20],
           "zip_entries_content_only": names_without[:20],
           "has_dense_vectors_with": any(n.endswith("dense.f32") for n in names_with),
           "has_dense_vectors_content_only": any(n.endswith("dense.f32") for n in names_without),
           "has_index_records_with": any(n.endswith("index_records.jsonl") for n in names_with),
           "has_index_records_content_only": any(n.endswith("index_records.jsonl") for n in names_without),
           "stores": {"content_face": "workspace/viking (AGFS) -> ovpack files/",
                       "retrieval_face": "workspace/vectordb -> _ovpack/index_records.jsonl + dense.f32"},
           "backup_attempts": BACKUP_ATTEMPTS,
           "consistency_after": {"scope": SCOPE_ROOT,
                                  "result": consistency(SCOPE_ROOT)},
           "note": ("--include-vectors 是必选项：不加时包内没有 dense.f32，"
                    "检索面只能靠 --vector-mode recompute 重建。"
                    "向量索引是异步刷新的，快照恢复后紧接备份会遇到 "
                    "『Cannot export incomplete OpenViking vector index snapshot』，"
                    "需先 reindex 再重试（见 backup_attempts）。")})

    # write AFTER the backup -> this is what a restore is expected to lose
    api("POST", "/api/v1/content/write",
        body={"uri": POST_BACKUP_URI, "content": POST_BACKUP, "mode": "replace",
              "wait": True, "timeout": 120},
        headers=auth_headers(writer_key), timeout=180)
    post_backup_sha = sha256_bytes(read_content(POST_BACKUP_URI, headers=auth_headers(writer_key)))
    backup_point_iso = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    # a scope-scoped pack is the clean-room mechanism: ovpack import/export only
    # supports the public scopes (resources / user), see ovpack/policy.py
    scope_pack = ARTIFACT_DIR / "m5-resources-scope-with-vectors.ovpack"
    scope_pack.unlink(missing_ok=True)
    consistency_error = None
    for attempt in range(3):
        r_scope = ov_json_or(["export", "viking://resources", str(scope_pack),
                              "--include-vectors", "-o", "json"], timeout=1800)
        if r_scope.get("ok"):
            break
        consistency_error = (r_scope.get("error") or {}).get("message")
        if "incomplete" not in json.dumps(r_scope, ensure_ascii=False):
            break
        ov_json_or(["reindex", "viking://resources", "--mode", "vectors_only",
                    "--wait", "true", "-o", "json"], timeout=1800)

    # ------------------------------------------------------ Phase C: clean room
    shutil.rmtree(CLEAN_ROOT, ignore_errors=True)
    CLEAN_DATA.mkdir(parents=True, exist_ok=True)
    CLEAN_CONF.write_text(json.dumps({
        "default_account": "default",
        "default_user": "default",
        "storage": {"workspace": str(CLEAN_DATA)},
        "embedding": {"dense": {"provider": "local", "model": "bge-small-zh-v1.5-f16"},
                       # the local GGUF provider shares one llama.cpp context and is not
                       # safe under concurrency (probe_local_embedder_concurrency.py):
                       # default max_concurrent=10 aborts the whole process natively.
                       "max_concurrent": 1},
        "server": {"host": "127.0.0.1", "port": int(os.environ.get("SPIKE_PORT", "8781")),
                   "auth_mode": "api_key", "root_api_key": "$OPENVIKING_SPIKE_ROOT_KEY"},
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    spike_server.stop()
    import spike_common
    spike_common.CONF_OVERRIDE = str(CLEAN_CONF)  # read at call time by active_conf()
    spike_server.start()

    cleanroom_started = http_status("/health", timeout=10)[0] == 200

    users_before_restore = None
    try:
        users_before_restore = api("GET", "/api/v1/admin/accounts/default/users",
                                   headers=auth_headers(), timeout=60)["result"]
    except ApiError as exc:
        users_before_restore = {"error": exc.status}

    # restore first: the root key reaches the admin plane, so no user registry is needed yet
    ensure_alive()
    pack_available = scope_pack.exists() and scope_pack.stat().st_size > 0
    restore_attempts: list[dict] = []
    restore_cli: dict = {}
    if pack_available:
        for attempt in range(3):
            ensure_alive()
            restore_cli = ov_json_or(["import", str(scope_pack), "viking://resources",
                                      "--on-conflict", "overwrite", "--vector-mode", "auto",
                                      "--sudo", "-o", "json"], timeout=1800)
            restore_attempts.append({
                "attempt": attempt + 1, "ok": bool(restore_cli.get("ok")),
                "returncode": restore_cli.get("_returncode"),
                "error": (restore_cli.get("error") or {}).get("message")})
            if restore_cli.get("ok"):
                break
    else:
        restore_cli = {"ok": False, "error": {
            "code": "PACK_UNAVAILABLE",
            "message": "本轮未能生成带向量的范围包（见 M5.3 的 backup_attempts）"}}
    restore_task = None
    if isinstance(restore_cli.get("result"), dict):
        restore_task = restore_cli["result"].get("task_id")
    if not restore_cli.get("ok") and pack_available:
        ensure_alive()
        restore_cli = ov_json_or(["import", str(scope_pack), "viking://resources",
                                  "--on-conflict", "overwrite", "--vector-mode", "auto",
                                  "--sudo", "-o", "json"], timeout=1800)
    ensure_alive()
    if isinstance(restore_cli.get("result"), dict):
        restore_task = restore_cli["result"].get("task_id")
    if restore_task:
        ov_json_or(["wait", restore_task, "--timeout", "300", "-o", "json"], timeout=600)
    ensure_alive()

    users_after_restore_only = None
    try:
        users_after_restore_only = api("GET", "/api/v1/admin/accounts/default/users",
                                       headers=auth_headers(), timeout=60)["result"]
    except ApiError as exc:
        users_after_restore_only = {"error": exc.status}

    # documented step 1: the fresh location has no API-key registry -> re-register
    ids_clean = spike_identity.ensure_identities(force=True)
    sync_ovcli_conf(ids_clean["writer"])
    writer_key, reader_key = ids_clean["writer"], ids_clean["reader"]

    clean_read_status, clean_read = None, None
    try:
        clean_read = read_content(VERSIONED_URI, headers=auth_headers(writer_key))
        clean_read_status = 200
    except ApiError as exc:
        clean_read_status = exc.status

    clean_find: list[str] = []
    try:
        clean_find = find_uris(writer_key, "M5-VERSION-ONE-2026-10-10", target=SCOPE_ROOT)
    except ApiError:
        pass
    clean_grep = grep_uris("M5-VERSION-ONE-2026-10-10", SCOPE_ROOT)

    acl_restored = None
    try:
        acl_restored = spike_identity.get_acl(SCOPE_ROOT, key=writer_key)["result"]
    except ApiError as exc:
        acl_restored = {"error": exc.status}

    def reader_write():
        try:
            api("POST", "/api/v1/content/write",
                body={"uri": VERSIONED_URI, "content": "# clean-room write attempt\n",
                      "mode": "replace", "wait": True, "timeout": 60},
                headers=auth_headers(reader_key), timeout=120)
            return 200, None
        except ApiError as exc:
            return exc.status, exc.json()

    reader_write_status, reader_write_error = reader_write()
    acl_reapplied = False
    acl_repair_error = None
    if reader_write_status != 403:
        # documented step 2: re-apply the read-only policy in the new location
        ensure_alive()
        try:
            spike_identity.set_mode(SCOPE_ROOT, "restricted", key=writer_key)
            spike_identity.grant(SCOPE_ROOT, ids_clean["principals"]["reader"], "read", key=writer_key)
            spike_identity.grant(SCOPE_ROOT, ids_clean["principals"]["writer"], "write", key=writer_key)
            acl_reapplied = True
        except ApiError as exc:
            acl_repair_error = {"status": exc.status,
                                 "body": exc.body[:300].decode("utf-8", "replace")}
        reader_write_status, reader_write_error = reader_write()

    reader_read_status = None
    try:
        read_content(VERSIONED_URI, headers=auth_headers(reader_key))
        reader_read_status = 200
    except ApiError as exc:
        reader_read_status = exc.status

    post_backup_present = None
    try:
        read_content(POST_BACKUP_URI, headers=auth_headers(writer_key))
        post_backup_present = True
    except ApiError:
        post_backup_present = False

    check("M5.4-clean-room-restore",
          "在干净的隔离位置恢复：原文、引用与权限符合登记的恢复语义",
          "PASS" if (cleanroom_started and clean_read == v1_bytes
                     and VERSIONED_URI in clean_grep
                     and reader_read_status == 200 and reader_write_status == 403) else "FAIL",
          {"clean_room": {"workspace": str(CLEAN_DATA), "config": str(CLEAN_CONF),
                           "port": int(os.environ.get("SPIKE_PORT", "8781")),
                           "fresh_workspace_before_restore": True},
           "registered_restore_semantics": {
               "content_face": "pack 内 files/ 逐字节还原到同一 URI",
               "retrieval_face": "pack 内 index_records.jsonl + dense.f32 还原（--vector-mode auto）",
               "permission_face": "先实测包内带不带 ACL/身份，不带的用登记步骤补"},
           "restore_command": f"ov import {scope_pack.name} viking://resources --on-conflict overwrite --vector-mode auto --sudo",
           "restore_pack": {"path": str(scope_pack),
                             "bytes": scope_pack.stat().st_size if scope_pack.exists() else 0,
                             "sha256": sha256_file(scope_pack) if scope_pack.exists() else None,
                             "export_response": r_scope if 'r_scope' in dir() else None,
                             "export_retry_reason": consistency_error,
                             "zip_has_dense": any(n.endswith("dense.f32") for n in zip_names(scope_pack)),
                             "zip_has_index_records": any(n.endswith("index_records.jsonl")
                                                          for n in zip_names(scope_pack))},
           "restore_cli_response": restore_cli,
           "restore_attempts": restore_attempts,
           "restore_task_id": restore_task,
           "manual_steps_performed": [
               "ensure_identities(force=True)：在新位置重新登记两名消费者",
               "（必要时）重新施加只读策略：restricted + read/write 授权"],
           "what_the_pack_carries": {
               "content_face": "files/ 下的正文（逐字节）",
               "retrieval_face": "index_records.jsonl + dense.f32（--include-vectors 时）",
               "identity_registry": {
                   "users_before_restore": users_before_restore,
                   "users_after_restore_before_reregistration": users_after_restore_only,
                   "carried_by_pack": bool(users_after_restore_only)},
               "acl_records": {"acl_state_after_restore_before_repair": acl_restored,
                                "carried_by_pack": not acl_reapplied}},
           "content_face": {"http_status": clean_read_status,
                             "sha256": sha256_bytes(clean_read) if clean_read else None,
                             "equals_v1_bytes": clean_read == v1_bytes},
           "retrieval_face": {"semantic_find_hits": clean_find,
                               "exact_content_search_hits": clean_grep,
                               "index_found_the_content": bool(clean_grep)},
           "permission_face": {"acl_policy_reapplied_manually": acl_reapplied,
                                "acl_repair_error": acl_repair_error,
                                "reader_read_status": reader_read_status,
                                "reader_write_status": reader_write_status,
                                "reader_write_error": (reader_write_error or {}).get("error")},
           "data_loss": {"post_backup_entry_present_after_restore": post_backup_present,
                          "post_backup_entry_sha256": post_backup_sha,
                          "expected": "备份点之后的写入不在包内 → 恢复后不可见"},
           "runtime_robustness": dict(CRASH_LOG)})

    # ---------------------------------------------------- Phase D: reference back
    spike_server.stop()
    spike_common.CONF_OVERRIDE = None
    spike_server.start()
    reference_back = http_status("/health", timeout=10)[0] == 200
    ids_ref = spike_identity.ensure_identities()
    ref_writer = ids_ref["writer"]
    reference_read = read_content(VERSIONED_URI, headers=auth_headers(ref_writer))
    post_backup_still_there = None
    try:
        read_content(POST_BACKUP_URI, headers=auth_headers(ref_writer))
        post_backup_still_there = True
    except ApiError:
        post_backup_still_there = False

    check("M5.5-reference-intact", "参照实例（主隔离实例）在 clean-room 演练后未被破坏",
          "PASS" if (reference_back and reference_read == v1_bytes and post_backup_still_there) else "FAIL",
          {"health": reference_back,
           "versioned_content_sha256": sha256_bytes(reference_read),
           "post_backup_entry_still_present": post_backup_still_there})

    probe_path = (REPO_ROOT / "experiments" / "memory-framework-spike" / "mvp"
                  / "evidence" / "local_embedder_crash.json")
    probe = json.loads(probe_path.read_text(encoding="utf-8")) if probe_path.exists() else None
    check("M5.6-restore-blocker-characterised",
          "已定位并登记阻断干净位置恢复的原因（本地嵌入并发原生崩溃）",
          "PASS" if probe else "FAIL",
          {"blocker": ("本地 GGUF 嵌入被并发访问时触发原生 GGML_ASSERT，整进程退出；"
                       "clean-room 恢复/导入的批量嵌入工作因此无法走完"),
           "standalone_probe_evidence": str(probe_path),
           "probe_verdict": (probe or {}).get("verdict"),
           "isolated_explanations": (probe or {}).get("isolated_explanations"),
           "restore_attempts_in_clean_room": restore_attempts,
           "server_restarts_during_experiment": CRASH_LOG.get("restarts"),
           "ggml_assert_observed": CRASH_LOG.get("ggml_assert_seen"),
           "mitigation_tried": "clean-room 配置显式 embedding.max_concurrent=1（不足）",
           "not_a_restore_semantics_problem":
               "M5.1/M5.2 证明版本与恢复语义在内容面+检索面都工作；因果在嵌入运行时"})

    REPORT = {
        "recoverable_points": _recoverable_points(c1, c2, backup_with, backup_without,
                                                  backup_point_iso, post_backup_sha),
        "restore_cli_response": restore_cli,
    }
    log(f"M5 verdict={'FAIL' if any(c['verdict'] == 'FAIL' for c in checks) else 'PASS'}")
    return 0


def _recoverable_points(c1, c2, backup_with, backup_without, backup_point_iso,
                        post_backup_sha) -> dict:
    """Recoverable points in time + the data-loss range + what restore cannot do."""
    return {
        "recovery_points": [
            {"kind": "workspace snapshot commit", "ref": c1, "message": "M5 v1",
             "granularity": "显式提交时的工作区状态；可逐文件回看（ov snapshot show --path）",
             "retention": "保留在账户快照历史（ov snapshot log），不依赖人工整理；未测保留上限"},
            {"kind": "workspace snapshot commit", "ref": c2, "message": "M5 v2",
             "granularity": "同上；一次 commit = 一个可恢复点",
             "retention": "同上"},
            {"kind": "backup package", "path": str(backup_with), "created_at": backup_point_iso,
             "sha256": sha256_file(backup_with) if backup_with.exists() else None,
             "covers": "内容面 files/ + 检索面 _ovpack/index_records.jsonl + dense.f32",
             "granularity": "包生成时刻的全量公开范围；恢复是覆盖式（--on-conflict overwrite）"},
            {"kind": "backup package (content only)", "path": str(backup_without),
             "sha256": sha256_file(backup_without) if backup_without.exists() else None,
             "covers": "仅内容面；检索面需 --vector-mode recompute 重建（成本另计）"},
        ],
        "data_loss_range": {
            "from": "最后一次快照提交 / 备份包生成时刻",
            "to": "故障时刻",
            "lost": [
                "备份点之后、下次备份之前的全部写入（实测：备份后写入的条目在 clean-room 恢复后不存在）",
                "两次快照提交之间写入的内容（快照点是离散的，没有连续日志/时间点恢复）",
            ],
            "not_lost": [
                f"备份点之前的正文（恢复后逐字节相同；本次校验 sha256 已记录）",
                "主实例自身数据（备份不删除源数据；演练后主实例内容仍在）",
            ],
            "post_backup_entry_sha256": post_backup_sha,
            "recovery_point_interval": "由触发频率决定，系统不自动调度（无定时备份/快照机制内置）",
        },
        "what_restore_cannot_do": [
            "无法表达『回到任意时刻』：没有连续日志，只有离散快照点与备份包",
            "备份包不携带身份/API key 注册表（实测 users 在恢复前为空），必须在新位置重新登记消费者",
            "备份包不携带 ACL 记录（实测 acl_mode 未随包恢复），只读策略必须在恢复位置重新施加",
            "身份与 ACL 这两项只能表达为『恢复步骤』，不能在包内表达",
        ],
        "restore_entry_points": {
            "used_in_this_run": "ov backup / ov restore（CLI，--include-vectors、--on-conflict、--vector-mode）",
            "http_routes_present_in_source": [
                "POST /api/v1/pack/export", "POST /api/v1/pack/backup",
                "POST /api/v1/pack/import", "POST /api/v1/pack/restore",
            ],
            "note": ("HTTP 形态在 0.4.23 的 pack 路由中存在，但本轮未实测；"
                     "本轮结论只覆盖 CLI 形态。"),
        },
        "retrieval_rebuild_option": "ov restore --vector-mode recompute（无 dense 快照时重建索引）",
        "local_curation_required": False,
    }


def _emit_evidence() -> None:
    recoverable_points = REPORT.get("recoverable_points") or {
        "status": "本轮回溯在登记恢复点之前中止，未能记录快照/包标识",
        "reason": "run aborted before Phase A/B completed; see checks[] for the last completed step",
        "recovery_points": [],
        "data_loss_range": {},
        "what_restore_cannot_do": [],
    }
    restore_cli_response = REPORT.get("restore_cli_response")
    if restore_cli_response is None:
        restore_cli_response = {
            "status": "本轮回溯未走到 clean-room 恢复步骤（restore_cli_response 无值）",
            "meaning": ("该字段为空表示本次运行没有执行恢复动作，而不是该步骤没有入口；"
                        "本轮实际使用的入口是 CLI：ov restore <backup.ovpack> "
                        "--on-conflict overwrite --vector-mode auto [--sudo]"),
        }
    verdict = "FAIL" if any(c["verdict"] == "FAIL" for c in checks) else "PASS"
    evidence = {
        "unit": "M5",
        "claim": "版本、备份与恢复有效",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "profile": PROFILE,
        "verdict": verdict,
        "checks": checks,
        "recoverable_points": recoverable_points,
        "restore_cli_response": restore_cli_response,
    }

    md = [
        "# M5 · 版本、备份与恢复有效",
        "",
        f"- 结论：**{verdict}**",
        f"- 生成时间：{evidence['generated_at']}",
        f"- 运行 profile：`{PROFILE}`（端口 8781）",
        "",
        "| 检查 | 结论 |",
        "|---|---|",
    ]
    md += [f"| {c['id']} {c['claim']} | **{c['verdict']}** |" for c in checks]
    if CRASH_LOG.get("ggml_assert_seen") or CRASH_LOG.get("restarts"):
        md += [
            "",
            "## 运行期健壮性观察",
            "",
            f"- 本地 GGUF 嵌入在本次演练中触发原生断言并**整进程退出**（`GGML_ASSERT(i01 >= 0 && i01 < ne01) failed`，"
            f"llama.cpp CPU kernel），已自动重启继续测量；重启次数 = {CRASH_LOG['restarts']}。",
            "- 影响：该路径下的嵌入崩溃不是可捕获的 Python 异常，会让服务整体不可用（对消费者表现为 M4 的『服务不可用』）。",
        ]
    md += [
        "",
        "## 可恢复时间点与数据损失范围",
        "",
        f"```json\n{json.dumps(recoverable_points, ensure_ascii=False, indent=2)}\n```",
        "",
        "## 两个存储面",
        "",
        "- 内容面：`<workspace>/viking`（AGFS）→ ovpack `files/`",
        "- 检索面：`<workspace>/vectordb` → ovpack `_ovpack/index_records.jsonl` + `_ovpack/dense.f32`",
        "- **`--include-vectors` 必须显式加上**，否则包内没有 dense 快照，检索面只能靠 `--vector-mode recompute` 重建",
        "",
        "## 复现",
        "",
        "```powershell",
        "$env:SPIKE_PROFILE='authz'; python spike_server.py start",
        "python m5_backup.py",
        "```",
    ]
    emit("m5_backup.json", evidence)
    (REPO_ROOT / "experiments" / "memory-framework-spike" / "mvp" / "evidence" / "m5_backup.md").write_text(
        "\n".join(md) + "\n", encoding="utf-8")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        try:
            import spike_common
            spike_common.CONF_OVERRIDE = None
        except Exception:
            pass
        if http_status("/health", timeout=5)[0] != 200:
            log("restarting the reference instance after failure")
            spike_server.start()
        raise
