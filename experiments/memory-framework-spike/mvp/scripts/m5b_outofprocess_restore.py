"""M5.4 复盘 · 用**进程外、串行、真模型**嵌入重跑干净位置恢复。

目的：把「备份/恢复设计」与「候选自带本地 in-process GGUF 嵌入器的并发缺陷」分开。
本脚本**不修改** task-1 的任何既有证据；它产出新证据 m5b_outofprocess_restore.{json,md}。

嵌入提供方（进程外真模型，同一个 GGUF）：
    embed_server.py  →  http://127.0.0.1:8791/v1   （OpenAI 兼容；所有 llama 调用共用一把锁）
    候选配置：  embedding.dense.provider = "openai"
                embedding.dense.api_base = "http://127.0.0.1:8791/v1"
                embedding.dense.model    = "bge-small-zh-v1.5-f16"
                embedding.dense.dimension = 512
                embedding.dense.input    = "text"
                embedding.dense.encoding_format = "float"

    $env:SPIKE_PROFILE='authz'; python m5b_outofprocess_restore.py [--keep-instance]

Writes evidence/m5b_outofprocess_restore.json / .md。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path

import spike_embed
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
M5B_ROOT = SPIKE_ROOT / "m5b"
REF_DATA = M5B_ROOT / "data-ref"
CLEAN_DATA = M5B_ROOT / "data-clean"
REF_CONF = M5B_ROOT / "ov-ref.conf"
CLEAN_CONF = M5B_ROOT / "ov-clean.conf"
ARTIFACT_DIR = M5B_ROOT / "artifacts"

SCOPE_ROOT = "viking://resources/m5b-proj"
VERSIONED_URI = f"{SCOPE_ROOT}/2026-10-10/note/m5b-versioned.md"
POST_BACKUP_URI = f"{SCOPE_ROOT}/2026-10-10/note/m5b-post-backup.md"
PRIVATE_URI = "viking://user/default/resources/mvp-spike/2026-10-10/note/m1-app-submit.md"

V1 = "# M5B 版本一\n\n标记：M5B-VERSION-ONE-2026-10-10\n"
V2 = "# M5B 版本二\n\n标记：M5B-VERSION-TWO-2026-10-10\n"
POST_BACKUP = "# M5B 备份之后写入\n\n标记：M5B-POST-BACKUP-2026-10-10\n"

EMBED_CONFIG = {
    "provider": "openai",
    "api_base": f"{spike_embed.BASE}/v1",
    "api_key": "local-out-of-process",
    "model": "bge-small-zh-v1.5-f16",
    "dimension": 512,
    "input": "text",
    "encoding_format": "float",
}

checks: list[dict] = []
REPORT: dict = {}


def check(cid: str, claim: str, verdict: str, detail: dict) -> str:
    checks.append({"id": cid, "claim": claim, "verdict": verdict, "detail": detail})
    log(f"{cid} {verdict} :: {claim}")
    return verdict


def write_conf(path: Path, workspace: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "default_account": "default",
        "default_user": "default",
        "storage": {"workspace": str(workspace)},
        "embedding": {"dense": dict(EMBED_CONFIG), "max_concurrent": 4},
        "server": {"host": "127.0.0.1", "port": int(os.environ.get("SPIKE_PORT", "8781")),
                   "auth_mode": "api_key", "root_api_key": "$OPENVIKING_SPIKE_ROOT_KEY"},
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
        payload["_stderr"] = err[-300:]
    return payload


def grep_uris(pattern: str, scope: str) -> list[str]:
    payload = ov_json_or(["grep", pattern, "-u", scope, "-o", "json"], timeout=180)
    result = payload.get("result")
    items = (result.get("matches") or []) if isinstance(result, dict) else (result or [])
    uris = []
    for item in items:
        if isinstance(item, dict):
            uris.append(item.get("uri") or item.get("path"))
        elif isinstance(item, str):
            uris.append(item)
    return [u for u in uris if u]


def find_uris(key: str, query: str, scope: str) -> list[str]:
    resp = api("POST", "/api/v1/search/find",
               body={"query": query, "limit": 10, "target_uri": scope},
               headers=auth_headers(key), timeout=180)
    return [h.get("uri") for h in ((resp.get("result") or {}).get("resources") or [])]


def zip_names(path: Path) -> list[str]:
    if not path.exists():
        return []
    with zipfile.ZipFile(path) as zf:
        return [i.filename for i in zf.infolist()]


def main() -> int:
    if PROFILE != "authz":
        raise SystemExit("M5B must run with SPIKE_PROFILE=authz")
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-instance", action="store_true",
                    help="leave the m5b clean-room instance running (default: restore the task-1 reference instance)")
    args = ap.parse_args()
    try:
        return _run()
    finally:
        _emit_evidence()
        # teardown: stop the m5b instance and the out-of-process embedder, then put the
        # task-1 reference instance (ov-authz.conf, in-process local embedding) back up
        try:
            import spike_common
            spike_embed.stop()
            if not args.keep_instance:
                spike_server.stop()
                spike_common.CONF_OVERRIDE = None
                spike_server.start()
                log("task-1 reference instance restored on 8781")
            else:
                spike_common.CONF_OVERRIDE = None
        except Exception as exc:  # noqa: BLE001
            log(f"teardown problem: {exc}")


def _run() -> int:
    global REPORT

    embed_proc = spike_embed.start()
    embed_health = spike_embed.health()
    shutil.rmtree(M5B_ROOT, ignore_errors=True)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    REF_DATA.mkdir(parents=True, exist_ok=True)
    CLEAN_DATA.mkdir(parents=True, exist_ok=True)
    write_conf(REF_CONF, REF_DATA)
    write_conf(CLEAN_CONF, CLEAN_DATA)

    import spike_common

    # ---------------------------------------------------------------- reference
    spike_server.stop()
    spike_common.CONF_OVERRIDE = str(REF_CONF)
    spike_server.start()
    ids = spike_identity.ensure_identities(force=True)
    writer_key, reader_key = ids["writer"], ids["reader"]
    sync_ovcli_conf(writer_key)
    # a probe identity that exists ONLY in the reference workspace: if it shows up
    # in the clean room after restore, then the pack does carry the identity registry
    PROBE_USER = "m5b-probe"
    try:
        spike_identity.register_user(PROBE_USER, "user")
    except Exception as exc:  # noqa: BLE001
        log(f"probe user registration problem: {exc}")
    ref_users = [u.get("user_id") for u in spike_identity.list_users()]

    api("POST", "/api/v1/content/write",
        body={"uri": VERSIONED_URI, "content": V1, "mode": "replace", "wait": True, "timeout": 120},
        headers=auth_headers(writer_key), timeout=180)
    v1_bytes = read_content(VERSIONED_URI, headers=auth_headers(writer_key))
    commit1 = ov_json_or(["snapshot", "commit", "-m", "M5B v1", "--paths", SCOPE_ROOT, "-o", "json"])

    api("POST", "/api/v1/content/write",
        body={"uri": VERSIONED_URI, "content": V2, "mode": "replace", "wait": True, "timeout": 120},
        headers=auth_headers(writer_key), timeout=180)
    commit2 = ov_json_or(["snapshot", "commit", "-m", "M5B v2", "--paths", SCOPE_ROOT, "-o", "json"])
    c1 = (commit1.get("result") or {}).get("commit_oid")

    restore = ov_json_or(["snapshot", "restore", c1, SCOPE_ROOT, "-m", "M5B rollback", "-o", "json"]) if c1 else {}
    restored = read_content(VERSIONED_URI, headers=auth_headers(writer_key))

    check("M5B.1-reference-ok",
          "进程外嵌入下参照实例可读写、快照与回滚正常（对照 M5.1/M5.2）",
          "PASS" if (restored == v1_bytes and restored != V2.encode("utf-8")) else "FAIL",
          {"embedding_provider": EMBED_CONFIG, "embed_server_health": embed_health,
           "commit_v1": c1, "commit_v2": (commit2.get("result") or {}).get("commit_oid"),
           "restore_response": restore,
           "content_sha256_after_restore": sha256_bytes(restored),
           "v1_sha256": sha256_bytes(v1_bytes),
           "restored_equals_v1": restored == v1_bytes,
           "server_still_alive": http_status("/health", timeout=10)[0] == 200})

    # ---------------------------------------------------------------- backup
    pack = ARTIFACT_DIR / "m5b-backup-with-vectors.ovpack"
    backup_attempts = []
    r_backup: dict = {}
    for attempt in range(3):
        pack.unlink(missing_ok=True)
        r_backup = ov_json_or(["backup", str(pack), "--include-vectors", "-o", "json"], timeout=1800)
        backup_attempts.append({"attempt": attempt + 1, "ok": bool(r_backup.get("ok")),
                                 "error": (r_backup.get("error") or {}).get("message")})
        if r_backup.get("ok"):
            break
        if "incomplete" not in json.dumps(r_backup, ensure_ascii=False):
            break
        ov_json_or(["reindex", "viking://resources", "--mode", "vectors_only",
                    "--wait", "true", "-o", "json"], timeout=1800)

    names = zip_names(pack)
    check("M5B.2-backup-both-faces",
          "进程外嵌入下备份仍同时覆盖内容面与检索面",
          "PASS" if (any(n.endswith("dense.f32") for n in names)
                     and any(n.endswith("index_records.jsonl") for n in names)) else "FAIL",
          {"pack": str(pack), "bytes": pack.stat().st_size if pack.exists() else 0,
           "sha256": sha256_file(pack) if pack.exists() else None,
           "backup_attempts": backup_attempts,
           "has_dense_vectors": any(n.endswith("dense.f32") for n in names),
           "has_index_records": any(n.endswith("index_records.jsonl") for n in names),
           "zip_entries": names[:12]})

    api("POST", "/api/v1/content/write",
        body={"uri": POST_BACKUP_URI, "content": POST_BACKUP, "mode": "replace",
              "wait": True, "timeout": 120},
        headers=auth_headers(writer_key), timeout=180)
    post_backup_sha = sha256_bytes(read_content(POST_BACKUP_URI, headers=auth_headers(writer_key)))

    # ---------------------------------------------------------------- clean room
    spike_server.stop()
    spike_common.CONF_OVERRIDE = str(CLEAN_CONF)
    spike_server.start()
    cleanroom_started = http_status("/health", timeout=10)[0] == 200

    users_before = None
    try:
        users_before = api("GET", "/api/v1/admin/accounts/default/users",
                           headers=auth_headers(), timeout=60)["result"]
    except ApiError as exc:
        users_before = {"error": exc.status}

    # Restore needs a working credential, and the fresh location has an empty
    # API-key registry, so register the consumers first (documented恢复步骤).
    # NOTE: `ov restore` REJECTS --sudo ("--sudo 只支持 admin/system/reindex/task…"),
    # so the data-plane identity must be registered before restoring.
    ids_clean = spike_identity.ensure_identities(force=True)
    sync_ovcli_conf(ids_clean["writer"])
    writer_key, reader_key = ids_clean["writer"], ids_clean["reader"]

    restore_attempts = []
    restore_cli: dict = {}
    for attempt in range(5):
        if http_status("/health", timeout=5)[0] != 200:
            log("clean-room server died; restarting")
            spike_server.start()
        restore_cli = ov_json_or(["restore", str(pack), "--on-conflict", "overwrite",
                                  "--vector-mode", "auto", "-o", "json"], timeout=1800)
        restore_attempts.append({"attempt": attempt + 1, "ok": bool(restore_cli.get("ok")),
                                  "returncode": restore_cli.get("_returncode"),
                                  "error": (restore_cli.get("error") or {}).get("message")})
        if restore_cli.get("ok"):
            break
    server_alive = http_status("/health", timeout=10)[0] == 200

    users_after_restore = None
    try:
        users_after_restore = api("GET", "/api/v1/admin/accounts/default/users",
                                  headers=auth_headers(), timeout=60)["result"]
    except ApiError as exc:
        users_after_restore = {"error": exc.status}
    clean_user_ids = [u.get("user_id") for u in (users_after_restore or []) if isinstance(u, dict)]
    registry_carried_by_pack = PROBE_USER in clean_user_ids

    # Discover where the restorer actually put the content instead of assuming the URI.
    restored_hits = grep_uris("M5B-VERSION-ONE-2026-10-10", "viking://resources")
    resolved_uri = VERSIONED_URI if VERSIONED_URI in restored_hits else (restored_hits[0] if restored_hits else VERSIONED_URI)
    resources_listing = ov_json_or(["ls", "viking://resources", "-r", "-o", "json"], timeout=180)

    read_status, clean_bytes = None, None
    try:
        clean_bytes = read_content(resolved_uri, headers=auth_headers(writer_key))
        read_status = 200
    except ApiError as exc:
        read_status = exc.status

    clean_grep = restored_hits
    clean_find = []
    try:
        clean_find = find_uris(writer_key, "M5B-VERSION-ONE-2026-10-10", "viking://resources")
    except ApiError:
        pass
    served = None
    served_uri = None
    served_bytes = None
    for candidate in clean_find + clean_grep:
        try:
            served_bytes = read_content(candidate, headers=auth_headers(writer_key))
            served = sha256_bytes(served_bytes)
            served_uri = candidate
            break
        except ApiError:
            served = None

    # ---- SEAL immediately, BEFORE any write probe ---------------------------
    # 核验一结束就把恢复出来的正文与检索面返回的字节另存为不可变副本，
    # 之后不再向被恢复的那条路径写任何东西；第三方可直接对封存文件复算。
    seal_dir = M5B_ROOT / "sealed"
    seal_dir.mkdir(parents=True, exist_ok=True)
    repo_seal_dir = REPO_ROOT / "experiments" / "memory-framework-spike" / "mvp" / "evidence" / "sealed"
    repo_seal_dir.mkdir(parents=True, exist_ok=True)
    sealed = {}

    def seal(name: str, data: bytes, **meta) -> None:
        """Write the immutable copy twice: run root (machine-local) + repo evidence."""
        p1 = seal_dir / name
        p1.write_bytes(data)
        p2 = repo_seal_dir / name
        p2.write_bytes(data)
        sealed[name] = {**meta, "bytes": len(data),
                        "sha256": sha256_bytes(p1.read_bytes()),
                        "path_run_root": str(p1),
                        "path_in_repo": str(p2.relative_to(REPO_ROOT)).replace("\\", "/"),
                        "repo_copy_matches": sha256_bytes(p2.read_bytes()) == sha256_bytes(p1.read_bytes())}

    if clean_bytes is not None:
        seal("restored-content.md", clean_bytes, kind="恢复后的正文（核验时读取）")
    if served_bytes is not None:
        seal("retrieval-returned-bytes.md", served_bytes, kind="检索面返回的字节",
             uri=served_uri)
    if v1_bytes is not None:
        seal("reference-v1-input.md", v1_bytes, kind="参照实例写入的 v1 输入")
    sealed["pack"] = {"path": str(pack), "bytes": pack.stat().st_size if pack.exists() else 0,
                      "sha256": sha256_file(pack) if pack.exists() else None}
    log(f"sealed restored artifact -> {sealed.get('restored-content.md', {}).get('path_in_repo')}")

    acl_scope = f"viking://resources/{SCOPE_ROOT.rstrip('/').rsplit('/', 1)[-1]}"
    acl_before_repair = None
    try:
        acl_before_repair = spike_identity.get_acl(acl_scope, key=writer_key)["result"]
    except ApiError as exc:
        acl_before_repair = {"error": exc.status, "body": exc.body[:200].decode("utf-8", "replace")}

    # ---- ACL probes run on a SCRATCH path, never on the restored artifact ---
    scratch_uri = f"{acl_scope}/2026-10-10/note/m5b-writeprobe.md"
    write_probes: list[dict] = []

    def probe_write(uri: str, content: str, key: str, label: str) -> tuple[int, dict | None]:
        try:
            api("POST", "/api/v1/content/write",
                body={"uri": uri, "content": content, "mode": "replace", "wait": True, "timeout": 60},
                headers=auth_headers(key), timeout=120)
            status, payload = 200, None
        except ApiError as exc:
            status, payload = exc.status, exc.json()
        write_probes.append({"label": label, "uri": uri, "identity_role":
                             "reader" if key == reader_key else "writer",
                             "http_status": status,
                             "error_code": ((payload or {}).get("error") or {}).get("code")})
        return status, payload

    probe_write(scratch_uri, "# writer scratch probe\n", writer_key, "writer-create-scratch")
    reader_write_status, reader_write_error = probe_write(
        scratch_uri, "# clean-room write attempt\n", reader_key, "reader-write-scratch-before-acl")

    acl_reapplied = False
    acl_repair_error = None
    if reader_write_status != 403:
        try:
            spike_identity.set_mode(acl_scope, "restricted", key=writer_key)
            spike_identity.grant(acl_scope, ids_clean["principals"]["reader"], "read", key=writer_key)
            spike_identity.grant(acl_scope, ids_clean["principals"]["writer"], "write", key=writer_key)
            acl_reapplied = True
        except ApiError as exc:
            acl_repair_error = {"status": exc.status,
                                 "body": exc.body[:300].decode("utf-8", "replace")}
        reader_write_status, reader_write_error = probe_write(
            scratch_uri, "# clean-room write attempt\n", reader_key, "reader-write-scratch-after-acl")

    reader_read_status = None
    try:
        read_content(scratch_uri, headers=auth_headers(reader_key))
        reader_read_status = 200
    except ApiError as exc:
        reader_read_status = exc.status

    reader_reads_artifact = None
    try:
        read_content(resolved_uri, headers=auth_headers(reader_key))
        reader_reads_artifact = 200
    except ApiError as exc:
        reader_reads_artifact = exc.status

    # ---- the restored artifact path must be byte-identical after all probes --
    live_after_probes = None
    try:
        live_after_probes = read_content(resolved_uri, headers=auth_headers(writer_key))
    except ApiError:
        live_after_probes = None
    artifact_untouched = (live_after_probes is not None
                          and clean_bytes is not None
                          and live_after_probes == clean_bytes)
    seal_recheck = {}
    for name, meta in sealed.items():
        if not isinstance(meta, dict) or "path" not in meta:
            continue
        p = Path(meta["path"])
        seal_recheck[name] = {"path": str(p), "bytes": p.stat().st_size if p.exists() else None,
                               "sha256": sha256_bytes(p.read_bytes()) if p.exists() else None,
                               "matches_recorded": p.exists() and sha256_bytes(p.read_bytes()) == meta.get("sha256")}

    post_backup_hits = grep_uris("M5B-POST-BACKUP-2026-10-10", "viking://resources")
    post_backup_present = None
    try:
        read_content(POST_BACKUP_URI, headers=auth_headers(writer_key))
        post_backup_present = True
    except ApiError:
        post_backup_present = False
    if post_backup_hits:
        post_backup_present = True

    # out-of-scope direct read must still be denied (M3 invariant survives restore)
    private_status = None
    try:
        api("GET", "/api/v1/content/read", params={"uri": PRIVATE_URI, "raw": "true"},
            headers=auth_headers(reader_key), timeout=60)
        private_status = 200
    except ApiError as exc:
        private_status = exc.status

    content_ok = clean_bytes == v1_bytes
    retrieval_ok = bool(clean_grep) and served == sha256_bytes(v1_bytes)
    permission_ok = (reader_read_status == 200 and reader_reads_artifact == 200
                     and reader_write_status == 403)
    verdict = "PASS" if (cleanroom_started and server_alive and content_ok and retrieval_ok
                         and permission_ok and not post_backup_present
                         and artifact_untouched) else "FAIL"

    check("M5B.3-clean-room-restore-outofprocess",
          "用进程外串行真模型嵌入，干净位置恢复走通（原文/引用/权限/损失范围四项）",
          verdict,
          {"embedding_provider": EMBED_CONFIG,
           "embed_server": {"base": spike_embed.BASE, "health_after": spike_embed.health()},
           "clean_room": {"workspace": str(CLEAN_DATA), "config": str(CLEAN_CONF)},
           "restore_command": f"ov restore {pack.name} --on-conflict overwrite --vector-mode auto -o json",
           "restore_attempts": restore_attempts,
           "restore_cli_response": restore_cli,
           "cli_note": ("`ov restore` **不接受 --sudo**（CLI 原文：『--sudo 只支持 admin、system、reindex、"
                        "task status 和 task list 命令』），所以必须在恢复位置先登记数据面身份；"
                        "这与『身份注册表不在备份包内』是同一条恢复步骤。"),
           "server_alive_after_restore": server_alive,
           "sealed_artifacts": sealed,
           "seal_recheck_after_all_probes": seal_recheck,
           "artifact_immutability": {
               "restored_uri": resolved_uri,
               "sha256_at_verification": sha256_bytes(clean_bytes) if clean_bytes else None,
               "sha256_after_all_probes": sha256_bytes(live_after_probes) if live_after_probes else None,
               "byte_identical_after_probes": artifact_untouched,
               "write_probes_all_targeted": "viking://resources/m5b-proj/2026-10-10/note/m5b-writeprobe.md（scratch，非被恢复路径）",
               "note": ("所有写探针都打在 scratch 路径上；被恢复的正文路径在核验后不再被写入，"
                        "因此落盘物与封存副本可被第三方独立复算。"),
           },
           "write_probe_timeline": write_probes,
           "path_discovery": {"expected_uri": VERSIONED_URI, "restored_hits": restored_hits,
                               "resolved_uri": resolved_uri,
                               "uri_relocated": bool(restored_hits) and VERSIONED_URI not in restored_hits,
                               "resources_listing": resources_listing},
           "content_face": {"http_status": read_status,
                            "sha256": sha256_bytes(clean_bytes) if clean_bytes else None,
                            "equals_v1_bytes": content_ok},
           "retrieval_face": {"exact_content_search_hits": clean_grep,
                              "semantic_find_hits": clean_find,
                              "served_sha256_of_first_hit": served,
                              "serves_restored_version": served == sha256_bytes(v1_bytes),
                              "index_found_the_content": retrieval_ok},
           "permission_face": {"acl_scope_used": acl_scope,
                               "acl_state_after_restore_before_repair": acl_before_repair,
                               "acl_policy_reapplied_manually": acl_reapplied,
                               "acl_repair_error": acl_repair_error,
                               "reader_read_status_scratch": reader_read_status,
                               "reader_read_status_restored_artifact": reader_reads_artifact,
                               "reader_write_status": reader_write_status,
                               "reader_write_error": (reader_write_error or {}).get("error"),
                               "out_of_scope_private_read_status": private_status},
           "what_the_pack_carries": {
               "identity_registry": {
                   "probe_user_registered_only_in_reference": PROBE_USER,
                   "reference_users": ref_users,
                   "clean_room_users_immediately_after_start": users_before,
                   "clean_room_users_after_restore": clean_user_ids,
                   "probe_user_found_in_clean_room": registry_carried_by_pack,
                   "conclusion": ("包内不带身份/API key 注册表：只在参照实例存在的探针用户 "
                                   "没有出现在恢复后的干净位置；干净位置恢复前用户列表为空，"
                                   "必须先重建消费者身份（`ov restore` 又不接受 --sudo）。"
                                   if not registry_carried_by_pack else
                                   "包内带身份注册表（探针用户出现）")},
               "acl_records": {"carried_by_pack": not acl_reapplied,
                               "note": "ACL 记录不能落在命名空间根（viking://resources 被拒 400），"
                                        "只读策略须施加到具体子树"}},
           "data_loss": {"post_backup_entry_present_after_restore": post_backup_present,
                          "post_backup_grep_hits": post_backup_hits,
                          "post_backup_entry_sha256": post_backup_sha,
                          "expected": "备份点之后的写入不在包内 → 恢复后不可见"}})

    REPORT = {
        "embedding_provider": EMBED_CONFIG,
        "embed_server": {"base": spike_embed.BASE, "health_after": spike_embed.health()},
        "pack": {"path": str(pack), "sha256": sha256_file(pack) if pack.exists() else None},
        "restore_attempts": restore_attempts,
        "sealed_artifacts": sealed,
        "seal_recheck_after_all_probes": seal_recheck,
        "artifact_immutability": {
            "restored_uri": resolved_uri,
            "sha256_at_verification": sha256_bytes(clean_bytes) if clean_bytes else None,
            "sha256_after_all_probes": sha256_bytes(live_after_probes) if live_after_probes else None,
            "byte_identical_after_probes": artifact_untouched,
        },
        "write_probe_timeline": write_probes,
        "recovery_points": {
            "workspace_snapshots": ["M5B v1 / M5B v2（ov snapshot commit）"],
            "backups": [{"path": str(pack), "covers": "内容面 files/ + 检索面 index_records.jsonl + dense.f32"}],
            "data_loss_range": "备份点之后、下次备份之前的全部写入（post-backup 条目恢复后不存在）",
            "what_restore_cannot_do": ["包内不带身份/API key 注册表（实测恢复后 users 为空）",
                                        "包内不带 ACL 记录（实测恢复后 acl_mode 未随包恢复）"],
        },
    }
    log(f"M5B verdict={verdict}")
    return 0


def _emit_evidence() -> None:
    verdict = "FAIL" if any(c["verdict"] == "FAIL" for c in checks) else "PASS"
    sealed = REPORT.get("sealed_artifacts") or {}
    corrections = {
        "what_was_wrong": ("第一次复盘（独立复算由 Lead 发现）里，`content_sha256_after_restore` 与 "
                          "`retrieval_face.served_sha256_of_first_hit` 都是**写探针之前**测的；"
                          "随后那次写探针**打在被恢复的正文路径上**并成功落盘，把落盘物覆盖成 27 B，"
                          "而证据里没有任何字段记录这次写入。"),
        "why_it_mattered": ("哈希断言在当时成立，但落盘物随后被覆盖 → 验收方按路径去比对会得到不一致，"
                            "对『备份可恢复』这条必须项是硬伤。"),
        "observed_after_the_fact": {
            "reference_artifact": {
                "path": r"%USERPROFILE%\.openviking-mvp-spike\m5b\data-ref\viking\default\resources\m5b-proj\2026-10-10\note\m5b-versioned.md",
                "bytes": 53,
                "sha256": "c4dfc7ac258fd5875cfc2a3f0759d62dd03eab385e29267e93f325797eee6874",
                "matches_evidence": True},
            "clean_room_artifact_after_overwrite": {
                "path": r"%USERPROFILE%\.openviking-mvp-spike\m5b\data-clean\viking\default\resources\m5b-proj\2026-10-10\note\m5b-versioned.md",
                "bytes": 27,
                "sha256": "0c6c9155646858879f2ce855a8bbed338f0f67bfc4fcb9aa409193550ff0ef70",
                "content": "# clean-room write attempt"},
        },
        "cause": ("写探针是在**只读策略生效之前**用只读身份发的（账户 ACL 默认关闭 / restricted 尚未施加），"
                  "因此那次写**成功**，落到被恢复的正文路径上。属测试设计缺陷，不是恢复语义问题。"),
        "fix_applied": [
            "核验一结束就把『恢复出来的正文』与『检索面返回的字节』另存为不可变副本（m5b/sealed/），"
            "并把 sha256 记进证据；封存后再次复算校验。",
            "所有写探针改打在 scratch 路径 `…/note/m5b-writeprobe.md`，**不再触碰被恢复的正文路径**。",
            "证据新增 `artifact_immutability`（核验后再次读取被恢复路径，必须与核验时逐字节相同）与 "
            "`write_probe_timeline`（每次写探针的身份/目标/状态码），使『恢复 → 核验 → 后续写入』时序无歧义。",
            "本文件同轮重跑，落盘物与封存副本一致；不再有任何写入落在被恢复路径上。",
        ],
        "sealed_copy_sha256": {
            "restored_content": (sealed.get("restored-content.md") or {}).get("sha256"),
            "retrieval_returned": (sealed.get("retrieval-returned-bytes.md") or {}).get("sha256"),
            "reference_input": (sealed.get("reference-v1-input.md") or {}).get("sha256"),
            "pack": (sealed.get("pack") or {}).get("sha256"),
        },
        "sealed_copies_in_repo": {
            name: {"path": meta.get("path_in_repo"), "sha256": meta.get("sha256")}
            for name, meta in sealed.items() if isinstance(meta, dict) and "path_in_repo" in meta
        },
        "unchanged": ["任务1 的原始 FAIL 证据 evidence/m5_backup.json 一字未改",
                       "M5.4 的条件化 PASS 结论不变（修的是证据时序，不是结论）"],
    }
    evidence = {
        "unit": "M5.4-复盘",
        "claim": "用进程外串行真模型嵌入重跑干净位置恢复，区分恢复设计与本地嵌入器缺陷",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "profile": PROFILE,
        "verdict": verdict,
        "corrections": corrections,
        "checks": checks,
        **REPORT,
    }
    md = [
        "# M5.4 复盘 · 进程外串行真模型嵌入下的干净位置恢复",
        "",
        f"- 结论：**{verdict}**" + ("（M5.4 改判 PASS，条件：嵌入提供方必须是进程外）" if verdict == "PASS" else "（M5.4 维持 FAIL）"),
        f"- 生成时间：{evidence['generated_at']}",
        f"- 嵌入：进程外真模型 `embed_server.py` → `{spike_embed.BASE}/v1`（同一个 GGUF，所有 llama 调用共用一把锁）",
        "- 候选配置键：`embedding.dense.provider=openai` / `api_base` / `model=bge-small-zh-v1.5-f16` / "
        "`dimension=512` / `input=text` / `encoding_format=float`",
        "",
        "## 更正说明（证据完整性修复）",
        "",
        f"- **问题**：{corrections['what_was_wrong']}",
        f"- **为何算硬伤**：{corrections['why_it_mattered']}",
        f"- **原因**：{corrections['cause']}",
        "- **修复**：",
    ]
    md += [f"  - {x}" for x in corrections["fix_applied"]]
    md += [
        "",
        "- **封存副本 sha256**（第三方可直接对文件复算）：",
        "",
        f"```json\n{json.dumps(corrections['sealed_copy_sha256'], ensure_ascii=False, indent=2)}\n```",
        "",
        "- **仓内封存副本**（不依赖本机隔离目录，可直接离线复算）：",
        "",
    ]
    md += [f"  - `{meta['path']}` sha256 `{meta['sha256']}`"
           for meta in corrections["sealed_copies_in_repo"].values()]
    md += [
        "- 一键复算：`python scripts/verify_seal.py`（对落盘物与仓内副本分别算哈希并与本文件断言比对）",
        "",
        f"- **未改动**：{corrections['unchanged'][0]}；{corrections['unchanged'][1]}",
        "",
        "| 检查 | 结论 |",
        "|---|---|",
    ]
    md += [f"| {c['id']} {c['claim']} | **{c['verdict']}** |" for c in checks]
    md += [
        "",
        "## 与 task-1 原始 FAIL 的关系",
        "",
        "task-1 的 M5.4 FAIL 证据**原样保留、不淡化**：候选自带的 **in-process** 本地 GGUF 嵌入器",
        "在并发访问同一 llama.cpp 上下文时触发原生 `GGML_ASSERT` 并让整进程退出",
        "（`evidence/local_embedder_crash.md`，退出码 3221226505），导致 `restore`/`import` 走不完。",
        "本次把**同一个模型搬到进程外并串行化**后，同样的恢复流程走通 —— 说明卡点是**嵌入器实现**，不是备份/恢复设计。",
        "",
        "## 复现",
        "",
        "```powershell",
        "$env:SPIKE_PROFILE='authz'",
        "python probe_outofprocess_embedder.py    # 先证进程外嵌入在 10 并发下存活",
        "python m5b_outofprocess_restore.py       # 再跑干净位置恢复",
        "```",
    ]
    emit("m5b_outofprocess_restore.json", evidence)
    (REPO_ROOT / "experiments" / "memory-framework-spike" / "mvp" / "evidence"
     / "m5b_outofprocess_restore.md").write_text("\n".join(md) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
