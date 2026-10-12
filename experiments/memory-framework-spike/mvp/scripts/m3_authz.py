"""M3 · 路径范围与授权一致（真实链路，authz profile）

Pass conditions from mvp-protocol.md M3:
  * addressing by date / type / project path works (list or search) and the
    returned reference can be read back;
  * a read-only identity cannot write;
  * direct reads **and** retrieval outside the granted scope leak nothing;
  * authorization errors are identifiable.

    $env:SPIKE_PROFILE='authz'; python m3_authz.py [--reset]

Writes evidence/m3_authz.json and evidence/m3_authz.md.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import spike_identity
from spike_common import (
    ApiError,
    OV_EXE,
    PROFILE,
    WORKSPACE,
    api,
    auth_headers,
    emit,
    log,
    sanitized_env,
    sync_ovcli_conf,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
PROJECT = "mvp-proj"
SCOPE_ROOT = f"viking://resources/{PROJECT}"
PRIVATE_URI = "viking://user/default/resources/mvp-spike/2026-10-10/note/m1-app-submit.md"
PRIVATE_MARKER = "MVP-SPIKE-2026-10-10-A1"

# date / type / project addressing, written by the writable consumer
SCOPED_FILES = {
    "2026-10-09/note/m3-dated.md": "# 2026-10-09 笔记\n\n标记：M3-SCOPE-2026-10-09\n",
    "2026-10-10/note/m3-scoped.md": "# 2026-10-10 笔记\n\n标记：M3-SCOPE-2026-10-10-NOTE\n",
    "2026-10-10/doc/m3-spec.md": "# 2026-10-10 规格\n\n标记：M3-SCOPE-2026-10-10-DOC\n",
}
OUTSIDE_ROOT = "viking://resources/spike-outside-2026-10-10"
OUTSIDE_URI = f"{OUTSIDE_ROOT}/note/outside.md"
OUTSIDE_MARKER = "M3-OUTSIDE-2026-10-10"

checks: list[dict] = []


def check(cid: str, claim: str, verdict: str, detail: dict) -> str:
    checks.append({"id": cid, "claim": claim, "verdict": verdict, "detail": detail})
    log(f"{cid} {verdict} :: {claim}")
    return verdict


def call(method: str, path: str, *, key: str | None, body: dict | None = None,
         params: dict | None = None, timeout: float = 120.0):
    """Return (status, payload); never raises on HTTP errors."""
    try:
        return 200, api(method, path, params=params, body=body,
                        headers=auth_headers(key) if key else {}, timeout=timeout)
    except ApiError as exc:
        payload = exc.json() or {"raw": exc.body[:300].decode("utf-8", "replace")}
        return exc.status, payload


def err_code(payload: dict) -> str | None:
    return ((payload or {}).get("error") or {}).get("code")


def write(uri: str, content: str, key: str, mode: str = "create"):
    return call("POST", "/api/v1/content/write", key=key,
                body={"uri": uri, "content": content, "mode": mode, "wait": True, "timeout": 120})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    if PROFILE != "authz":
        raise SystemExit("M3 must run with SPIKE_PROFILE=authz")

    ids = spike_identity.ensure_identities()
    writer_key, reader_key = ids["writer"], ids["reader"]
    sync_ovcli_conf(writer_key)
    w_principal, r_principal = ids["principals"]["writer"], ids["principals"]["reader"]

    for uri in (SCOPE_ROOT, OUTSIDE_ROOT):
        try:
            api("DELETE", "/api/v1/acl", params={"uri": uri}, headers=auth_headers(writer_key), timeout=60)
        except ApiError:
            pass
        subprocess.run([str(OV_EXE), "rm", uri, "--recursive", "-o", "json"],
                       capture_output=True, env=sanitized_env(), timeout=300)

    # ---- seed content -------------------------------------------------------
    seeded = {}
    for rel, text in SCOPED_FILES.items():
        status, resp = write(f"{SCOPE_ROOT}/{rel}", text, writer_key, mode="create")
        seeded[rel] = {"uri": f"{SCOPE_ROOT}/{rel}", "http_status": status,
                       "ok": resp.get("status") == "ok"}
    # an out-of-scope shared tree: restricted, granted to the writer only,
    # deliberately NOT granted to the reader
    write(OUTSIDE_URI, f"# 范围外条目\n\n标记：{OUTSIDE_MARKER}\n", writer_key, mode="create")
    # private content of another user is already present from M1 (dev-mode write)
    private_status, _ = call("GET", "/api/v1/content/read", key=writer_key,
                             params={"uri": PRIVATE_URI, "raw": "true"})

    # ---- apply scope ACL ----------------------------------------------------
    spike_identity.set_mode(SCOPE_ROOT, "restricted", key=writer_key)
    spike_identity.grant(SCOPE_ROOT, r_principal, "read", key=writer_key)
    spike_identity.grant(SCOPE_ROOT, w_principal, "write", key=writer_key)
    spike_identity.set_mode(OUTSIDE_ROOT, "restricted", key=writer_key)
    spike_identity.grant(OUTSIDE_ROOT, w_principal, "write", key=writer_key)
    acl = spike_identity.get_acl(SCOPE_ROOT, key=writer_key)["result"]
    acl_outside = spike_identity.get_acl(OUTSIDE_ROOT, key=writer_key)["result"]

    check("M3.0-seed", "播种日期/类型/项目路径内容 + 未授权 restricted 子树",
          "PASS" if all(v["ok"] for v in seeded.values()) else "FAIL",
          {"seeded": seeded, "outside_uri": OUTSIDE_URI,
           "acl_on_scope": acl, "acl_on_outside_tree": acl_outside,
           "private_uri_readable_by_writer": private_status})

    # ---- 0b. the account ACL switch is OFF by default: same identity, same op --
    scope_file = f"{SCOPE_ROOT}/2026-10-10/note/m3-scoped.md"
    probe_body = "# 只读身份写入尝试（ACL 开关对照）\n"

    spike_identity.set_account_acl(False)
    acl_off_settings = spike_identity.acl_enabled()
    off_status, off_resp = write(scope_file, probe_body, reader_key, mode="replace")
    off_read = call("GET", "/api/v1/content/read", key=reader_key,
                    params={"uri": scope_file, "raw": "true"})

    spike_identity.set_account_acl(True)
    acl_on_settings = spike_identity.acl_enabled()
    on_status, on_resp = write(scope_file, probe_body, reader_key, mode="replace")
    # restore the original entry (the ACL-off phase above did land its bytes)
    write(scope_file, SCOPED_FILES["2026-10-10/note/m3-scoped.md"], writer_key, mode="replace")

    # where the switch lives on disk (config layer for the write-up)
    config_doc = WORKSPACE / "viking" / "default" / "_system" / "setting.json"
    config_doc_text = None
    if config_doc.exists():
        config_doc_text = config_doc.read_text(encoding="utf-8", errors="replace")

    check("M3.0b-acl-default-off",
          "账户级 ACL 默认关闭：同一身份同一操作在开关前后的可观测差异",
          "PASS" if (acl_off_settings is False and off_status == 200
                     and acl_on_settings is True and on_status == 403) else "FAIL",
          {"switch": {"config_layer": "account runtime config（AGFS 文档）",
                       "config_document": str(config_doc),
                       "key_path": "acl.enabled",
                       "api": "PATCH /api/v1/admin/accounts/default/settings  {\"acl\":{\"enabled\": <bool>}}",
                       "read_api": "GET /api/v1/admin/accounts/default/settings",
                       "code_reader": "openviking.storage.acl.AclManager.is_enabled(account_id) → runtime_config.get_account(account_id, 'acl')"},
           "identity": r_principal + " (role=user, ACL level=read on the scope)",
           "operation": f"POST /api/v1/content/write mode=replace uri={scope_file}",
           "acl_off": {"acl_enabled": acl_off_settings, "http_status": off_status,
                        "response_status_field": off_resp.get("status"),
                        "written_bytes": ((off_resp.get("result") or {}).get("written_bytes")),
                        "read_back_after_write": (off_read[1].get("result") if off_read[0] == 200 else None)},
           "acl_on": {"acl_enabled": acl_on_settings, "http_status": on_status,
                       "error_code": err_code(on_resp),
                       "error_message": ((on_resp.get("error") or {}).get("message")),
                       "full_error_body": on_resp.get("error")},
           "statement": "默认状态下（acl.enabled 未设置 = false）只读身份也能写；restricted 与 grant 记录仍在，但不生效。",
           "config_document_contains_acl": ("\"acl\"" in (config_doc_text or "")),
           "config_document_excerpt": (config_doc_text[:600] if config_doc_text else None)})

    # ---- 1. addressing by date / type / project path ------------------------
    findings = {}
    for label, uri in (
        ("project", SCOPE_ROOT),
        ("date", f"{SCOPE_ROOT}/2026-10-10"),
        ("date+type", f"{SCOPE_ROOT}/2026-10-10/note"),
    ):
        status, resp = call("GET", "/api/v1/fs/ls", key=writer_key, params={"uri": uri})
        uris = [x.get("uri") for x in (resp.get("result") or [])] if status == 200 else []
        findings[label] = {"uri": uri, "http_status": status, "returned": uris}
    date_type_ok = set(findings["date+type"]["returned"]) == {f"{SCOPE_ROOT}/2026-10-10/note/m3-scoped.md"}
    check("M3.1-path-addressing", "能按日期/类型/项目路径列举",
          "PASS" if date_type_ok else "FAIL", findings)

    # ---- 2. search returns a reference that reads back ----------------------
    status, resp = call("POST", "/api/v1/search/find", key=writer_key,
                        body={"query": "M3-SCOPE-2026-10-10-DOC", "target_uri": SCOPE_ROOT, "limit": 10})
    hits = ((resp.get("result") or {}).get("resources") or []) if status == 200 else []
    hit_uris = [h.get("uri") for h in hits]
    read_status, read_payload = (None, None)
    if hit_uris:
        read_status, read_payload = call("GET", "/api/v1/content/read", key=writer_key,
                                         params={"uri": hit_uris[0], "raw": "true"})
    read_ok = read_status == 200 and "M3-SCOPE-2026-10-10-DOC" in (read_payload or {}).get("result", "")
    check("M3.2-search-then-read", "检索返回引用，且能读回该引用的正文",
          "PASS" if (hit_uris and read_ok) else "FAIL",
          {"query": "M3-SCOPE-2026-10-10-DOC", "target_uri": SCOPE_ROOT,
           "returned_uris": hit_uris, "read_back_uri": hit_uris[0] if hit_uris else None,
           "read_back_http_status": read_status,
           "read_back_contains_marker": bool(read_ok)})

    # ---- 3. read-only identity: reads granted scope, cannot write ----------
    ro_read_status, ro_read = call("GET", "/api/v1/content/read", key=reader_key,
                                   params={"uri": f"{SCOPE_ROOT}/2026-10-10/note/m3-scoped.md",
                                           "raw": "true"})
    ro_write_status, ro_write = write(f"{SCOPE_ROOT}/2026-10-10/note/m3-scoped.md",
                                      "# 只读身份写入尝试\n", reader_key, mode="replace")
    ro_mkdir_status, ro_mkdir = call("POST", "/api/v1/fs/mkdir", key=reader_key,
                                     body={"uri": f"{SCOPE_ROOT}/2026-10-10/evil"})
    ro_rm_status, ro_rm = call("DELETE", "/api/v1/fs", key=reader_key,
                               params={"uri": f"{SCOPE_ROOT}/2026-10-10/note/m3-scoped.md",
                                       "recursive": "false"})
    ro_mv_status, ro_mv = call("POST", "/api/v1/fs/mv", key=reader_key,
                               body={"from_uri": f"{SCOPE_ROOT}/2026-10-10/note/m3-scoped.md",
                                     "to_uri": f"{SCOPE_ROOT}/2026-10-10/doc/stolen.md"})
    check("M3.3-readonly-identity", "只读身份可读授权范围，且写/建目录/删/移全部被拒绝",
          "PASS" if (ro_read_status == 200 and ro_write_status == 403 and ro_mkdir_status == 403
                     and ro_rm_status == 403 and ro_mv_status == 403) else "FAIL",
          {"identity": r_principal + " (role=user, ACL level=read)",
           "read": {"http_status": ro_read_status,
                    "content_ok": "M3-SCOPE-2026-10-10-NOTE" in (ro_read or {}).get("result", "")},
           "write": {"http_status": ro_write_status, "code": err_code(ro_write)},
           "mkdir": {"http_status": ro_mkdir_status, "code": err_code(ro_mkdir)},
           "delete": {"http_status": ro_rm_status, "code": err_code(ro_rm)},
           "move": {"http_status": ro_mv_status, "code": err_code(ro_mv)}})

    # ---- 4. direct read outside the granted scope ---------------------------
    outside_read = call("GET", "/api/v1/content/read", key=reader_key,
                        params={"uri": OUTSIDE_URI, "raw": "true"})
    private_read = call("GET", "/api/v1/content/read", key=reader_key,
                        params={"uri": PRIVATE_URI, "raw": "true"})
    other_user_ls = call("GET", "/api/v1/fs/ls", key=reader_key,
                         params={"uri": "viking://user/default/resources"})
    check("M3.4-out-of-scope-direct-read-denied",
          "范围外（未授权的 restricted 子树 / 他用户私有命名空间）的直接读取不能泄露内容",
          "PASS" if (outside_read[0] == 403 and private_read[0] == 403
                     and other_user_ls[0] == 403) else "FAIL",
          {"ungranted_restricted_subtree": {"uri": OUTSIDE_URI, "http_status": outside_read[0],
                                             "code": err_code(outside_read[1]),
                                             "body_leaks_content": OUTSIDE_MARKER in json.dumps(outside_read[1], ensure_ascii=False)},
           "other_user_private": {"uri": PRIVATE_URI, "http_status": private_read[0],
                                   "code": err_code(private_read[1]),
                                   "body_leaks_content": PRIVATE_MARKER in json.dumps(private_read[1], ensure_ascii=False)},
           "other_user_ls": {"uri": "viking://user/default/resources", "http_status": other_user_ls[0],
                             "code": err_code(other_user_ls[1]),
                             "returned": (other_user_ls[1].get("result") if other_user_ls[0] == 200 else None)},
           "note": ("边界是『他用户私有命名空间』与『restricted 且未授予的子树』。"
                    "账户内共享的 viking://resources/ 下未被 restricted 的子树对所有用户可读，"
                    "这是框架默认语义，不算越权泄露。")})

    # ---- 5. retrieval outside the scope must not leak -----------------------
    leak_queries = {
        "private_marker": {"query": PRIVATE_MARKER, "limit": 20},
        "private_topic": {"query": "个人记忆基座 MVP 测试条目", "limit": 20},
        "outside_marker": {"query": OUTSIDE_MARKER, "limit": 20},
    }
    leak_results = {}
    leaked = []
    returned_all: set[str] = set()
    for label, body in leak_queries.items():
        status, resp = call("POST", "/api/v1/search/find", key=reader_key,
                            body={**body, "read_content": True}, timeout=180)
        uris = [h.get("uri") for h in ((resp.get("result") or {}).get("resources") or [])] if status == 200 else []
        returned_all.update(uris)
        blob = json.dumps(resp, ensure_ascii=False)
        hits_private = PRIVATE_URI in blob or PRIVATE_MARKER in blob
        hits_outside = any(u.startswith(OUTSIDE_ROOT) for u in uris) or OUTSIDE_MARKER in blob
        leak_results[label] = {"query": body["query"], "http_status": status, "returned_uris": uris,
                               "leaks_private": hits_private, "leaks_outside_restricted": hits_outside}
        if hits_private or hits_outside:
            leaked.append(label)

    # Nothing the reader was handed may sit in a restricted-without-grant subtree or in
    # another user's private namespace.  wk-spike-source is deliberately readable: M2
    # granted the reader `read` there, so hits from it are legitimate, not a leak.
    forbidden_returned = sorted(
        u for u in returned_all
        if u.startswith(OUTSIDE_ROOT) or u.startswith("viking://user/default")
    )
    # the strongest invariant: everything the reader was handed is actually readable by it
    readability = {}
    for u in sorted(returned_all):
        st, _ = call("GET", "/api/v1/content/read", key=reader_key, params={"uri": u, "raw": "true"})
        readability[u] = st
    unreadable_but_returned = sorted(u for u, st in readability.items() if st != 200)
    grep_status, grep_resp = call("POST", "/api/v1/search/grep", key=reader_key,
                                  body={"uri": "viking://user/default/resources", "pattern": PRIVATE_MARKER},
                                  timeout=120)
    grep_blob = json.dumps(grep_resp, ensure_ascii=False)
    check("M3.5-out-of-scope-retrieval-no-leak",
          "范围外的检索不能泄露内容（含 grep 越界与『返回即不可读』不一致）",
          "PASS" if (not leaked and not forbidden_returned and not unreadable_but_returned
                     and grep_status in (403, 404) and PRIVATE_MARKER not in grep_blob) else "FAIL",
          {"find_probes": leak_results,
           "forbidden_uris_returned": forbidden_returned,
           "readability_of_every_returned_uri": readability,
           "unreadable_but_returned": unreadable_but_returned,
           "readable_by_design": {
               "viking://resources/mvp-proj/**": "M3 授予 reader read",
               "viking://resources/wk-spike-source/**": "M2 授予 reader read",
               "viking://user/mem-ro/**": "reader 自己的私有命名空间",
           },
           "grep_out_of_scope": {"target": "viking://user/default/resources",
                                  "http_status": grep_status, "code": err_code(grep_resp),
                                  "leaks_marker": PRIVATE_MARKER in grep_blob}})

    # ---- 6. authorization errors are identifiable ---------------------------
    no_key = call("GET", "/api/v1/content/read", key=None,
                  params={"uri": f"{SCOPE_ROOT}/2026-10-10/note/m3-scoped.md"})
    bad_key = call("GET", "/api/v1/content/read", key="not-a-real-key",
                   params={"uri": f"{SCOPE_ROOT}/2026-10-10/note/m3-scoped.md"})
    missing_uri = call("GET", "/api/v1/content/read", key=writer_key,
                       params={"uri": f"{SCOPE_ROOT}/2026-10-10/note/does-not-exist.md"})
    distinct = {(no_key[0], err_code(no_key[1])), (bad_key[0], err_code(bad_key[1])),
                (ro_write_status, err_code(ro_write)), (missing_uri[0], err_code(missing_uri[1]))}
    check("M3.6-errors-identifiable",
          "无凭据 / 坏凭据 / 越权 / 不存在 四类错误可识别",
          "PASS" if (no_key[0] == 401 and ro_write_status == 403 and missing_uri[0] == 404
                     and len(distinct) >= 3) else "FAIL",
          {"no_credential": {"http_status": no_key[0], "code": err_code(no_key[1])},
           "bad_credential": {"http_status": bad_key[0], "code": err_code(bad_key[1])},
           "permission_denied": {"http_status": ro_write_status, "code": err_code(ro_write)},
           "not_found": {"http_status": missing_uri[0], "code": err_code(missing_uri[1])},
           "distinct_pairs": sorted([list(x) for x in distinct])})

    verdict = "FAIL" if any(c["verdict"] == "FAIL" for c in checks) else "PASS"
    evidence = {
        "unit": "M3",
        "claim": "路径范围与授权一致",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "profile": PROFILE,
        "identities": {"writer": w_principal + " (role=admin, ACL write)",
                       "reader": r_principal + " (role=user, ACL read)",
                       "root": "server root API key (admin plane only; ROOT keys cannot touch data APIs)"},
        "verdict": verdict,
        "checks": checks,
    }

    md = [
        "# M3 · 路径范围与授权一致",
        "",
        f"- 结论：**{verdict}**",
        f"- 生成时间：{evidence['generated_at']}",
        f"- 运行 profile：`{PROFILE}` · 端口 8781 · 同一隔离 workspace",
        f"- 身份：`{w_principal}`（可写，ROLE=admin，ACL write）/ `{r_principal}`（只读，ROLE=user，ACL read）",
        "- 关键前提：**账户级 ACL 默认关闭**，必须先 `PATCH /api/v1/admin/accounts/default/settings {\"acl\":{\"enabled\":true}}`，否则 restricted/grant 不生效",
        "",
        "| 检查 | 结论 |",
        "|---|---|",
    ]
    md += [f"| {c['id']} {c['claim']} | **{c['verdict']}** |" for c in checks]
    md += [
        "",
        "## 范围外的四类错误",
        "",
        f"```json\n{json.dumps(checks[-1]['detail'], ensure_ascii=False, indent=2)}\n```",
        "",
        "## 复现",
        "",
        "```powershell",
        "$env:SPIKE_PROFILE='authz'; python spike_server.py start",
        "python m3_authz.py --reset",
        "```",
    ]
    emit("m3_authz.json", evidence)
    (REPO_ROOT / "experiments" / "memory-framework-spike" / "mvp" / "evidence" / "m3_authz.md").write_text(
        "\n".join(md) + "\n", encoding="utf-8")
    log(f"M3 verdict={verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
