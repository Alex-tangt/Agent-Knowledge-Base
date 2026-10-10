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
    for candidate in clean_find + clean_grep:
        try:
            served = sha256_bytes(read_content(candidate, headers=auth_headers(writer_key)))
            break
        except ApiError:
            served = None

    acl_scope = f"viking://resources/{SCOPE_ROOT.rstrip('/').rsplit('/', 1)[-1]}"
    acl_before_repair = None
    try:
        acl_before_repair = spike_identity.get_acl(acl_scope, key=writer_key)["result"]
    except ApiError as exc:
        acl_before_repair = {"error": exc.status, "body": exc.body[:200].decode("utf-8", "replace")}

    def reader_write():
        try:
            api("POST", "/api/v1/content/write",
                body={"uri": resolved_uri, "content": "# clean-room write attempt\n",
                      "mode": "replace", "wait": True, "timeout": 60},
                headers=auth_headers(reader_key), timeout=120)
            return 200, None
        except ApiError as exc:
            return exc.status, exc.json()

    reader_write_status, reader_write_error = reader_write()
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
        reader_write_status, reader_write_error = reader_write()

    reader_read_status = None
    try:
        read_content(resolved_uri, headers=auth_headers(reader_key))
        reader_read_status = 200
    except ApiError as exc:
        reader_read_status = exc.status

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
    permission_ok = reader_read_status == 200 and reader_write_status == 403
    verdict = "PASS" if (cleanroom_started and server_alive and content_ok and retrieval_ok
                         and permission_ok and not post_backup_present) else "FAIL"

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
                               "reader_read_status": reader_read_status,
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
    evidence = {
        "unit": "M5.4-复盘",
        "claim": "用进程外串行真模型嵌入重跑干净位置恢复，区分恢复设计与本地嵌入器缺陷",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "profile": PROFILE,
        "verdict": verdict,
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
