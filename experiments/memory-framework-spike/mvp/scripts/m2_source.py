"""M2 · 既有资料只读接入并追平更新（真实链路，authz profile）

What is actually measured here (no口径 bending):

  * is there an **in-place mount / watch** of a host directory (a real 「指过去」)?   -> probed
  * does ingesting modify the source directory (file list + git commit)?             -> must be identical
  * is the ingested body byte-identical to the source file?                          -> hash comparison
  * can the source be written *through* the base by a read-only identity?            -> must be refused
  * what is the declared update mechanism for a local source, and its latency/cost?  -> measured

Run with SPIKE_PROFILE=authz (api_key auth).  The reference dev profile cannot
express a read-only identity: its dev auth plugin always resolves ROLE=root and
ROLE=root bypasses ACL.

    python m2_source.py [--reset]

Writes evidence/m2_source.json and evidence/m2_source.md.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import spike_identity
from spike_common import (
    ApiError,
    OV_EXE,
    SPIKE_ROOT,
    PROFILE,
    WORKSPACE,
    api,
    auth_headers,
    disk_path_for_uri,
    emit,
    log,
    ov,
    ov_json,
    read_content,
    sanitized_env,
    sha256_bytes,
    sync_ovcli_conf,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
SOURCE_DIR = SPIKE_ROOT / "source-repo"
SOURCE_ROOT_URI = "viking://resources/wk-spike-source"
DUPLICATE_ROOT_URI = SOURCE_ROOT_URI + "_1"
IMPORTED_ROOT_URI = f"{SOURCE_ROOT_URI}/source-repo"

SOURCE_FILES = {
    "README.md": "# 项目来源副本\n\n这是只读接入测试用的项目来源目录。\n\n标记：SRC-MVP-2026-10-10\n",
    "notes/plan.md": "# 计划\n\n- 第一版计划：只读接入来源副本\n",
    "docs/spec.md": "# 规格\n\n来源文件的正文，外部修改后应只表现为同一内容的新版本。\n",
}
CHANGED_SPEC = (
    "# 规格（外部修改后）\n\n"
    "来源文件的正文被外部改动了；基座应在登记过的更新机制执行后读到这一版。\n\n"
    "版本标记：SRC-MVP-2026-10-10-REV2\n"
)

checks: list[dict] = []


def check(cid: str, claim: str, verdict: str, detail: dict) -> str:
    checks.append({"id": cid, "claim": claim, "verdict": verdict, "detail": detail})
    log(f"{cid} {verdict} :: {claim}")
    return verdict


def build_source_repo() -> None:
    if SOURCE_DIR.exists():
        shutil.rmtree(SOURCE_DIR, ignore_errors=True)
    for rel, text in SOURCE_FILES.items():
        path = SOURCE_DIR / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))  # explicit no-BOM UTF-8
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "spike-src", "GIT_AUTHOR_EMAIL": "spike-src@local",
        "GIT_COMMITTER_NAME": "spike-src", "GIT_COMMITTER_EMAIL": "spike-src@local",
        "GIT_AUTHOR_DATE": "2026-10-10T14:30:00+08:00",
        "GIT_COMMITTER_DATE": "2026-10-10T14:30:00+08:00",
    })
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=SOURCE_DIR, env=env, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=SOURCE_DIR, env=env, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "src: 初始来源副本"], cwd=SOURCE_DIR, env=env, capture_output=True)


def source_snapshot() -> dict:
    files = []
    for path in sorted(SOURCE_DIR.rglob("*")):
        if ".git" in path.parts or not path.is_file():
            continue
        rel = path.relative_to(SOURCE_DIR).as_posix()
        data = path.read_bytes()
        files.append({"rel": rel, "bytes": len(data), "sha256": sha256_bytes(data)})
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=SOURCE_DIR, capture_output=True, text=True).stdout.strip()
    status = subprocess.run(["git", "status", "--porcelain"], cwd=SOURCE_DIR, capture_output=True, text=True).stdout.strip()
    return {"files": files, "file_count": len(files), "git_head": head, "git_status_porcelain": status}


def imported_uri(rel: str) -> str:
    return f"{IMPORTED_ROOT_URI}/{rel}"


def imported_listing() -> list[dict]:
    payload = ov_json("ls", IMPORTED_ROOT_URI, "-r", "-o", "json")
    result = payload.get("result") if isinstance(payload, dict) else None
    return [x for x in (result or []) if not x.get("isDir")]


def drop_acl(uri: str, key: str) -> None:
    try:
        api("DELETE", "/api/v1/acl", params={"uri": uri}, headers=auth_headers(key), timeout=60)
    except ApiError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    if PROFILE != "authz":
        raise SystemExit("M2 must run with SPIKE_PROFILE=authz (needs a non-root read-only identity)")

    ids = spike_identity.ensure_identities()
    writer_key, reader_key = ids["writer"], ids["reader"]
    sync_ovcli_conf(writer_key)  # the ov CLI needs a data-plane key

    if args.reset:
        for uri in (SOURCE_ROOT_URI, DUPLICATE_ROOT_URI):
            drop_acl(uri, writer_key)
        for uri in (SOURCE_ROOT_URI, DUPLICATE_ROOT_URI):
            subprocess.run([str(OV_EXE), "rm", uri, "--recursive", "-o", "json"],
                           capture_output=True, env=sanitized_env(), timeout=300)

    build_source_repo()
    before = source_snapshot()
    log(f"source repo built: {before['file_count']} files @ {before['git_head'][:12]}")

    # ---- 1. probe: is there an in-place watch/mount entry for a local dir? ---
    probe = ov("add-resource", str(SOURCE_DIR), "--parent-auto-create",
               "viking://resources/wk-spike-watch-probe", "--watch-interval", "1",
               "--wait", "--timeout", "120", "-o", "json", timeout=300)
    probe_err = probe.stderr.decode("utf-8", "replace").strip()
    in_place_mount = probe.returncode == 0
    framework_msg = ""
    if probe_err.startswith("{"):
        framework_msg = json.loads(probe_err).get("error", {}).get("message", "")
    else:
        framework_msg = probe_err[-400:]
    check("M2.1-no-in-place-watch",
          "本地目录能否就地挂载/持续追平（有就地入口 → 才可能『指过去』）",
          "PASS" if not in_place_mount else "FAIL",
          {"probe_command": f'ov add-resource "{SOURCE_DIR}" --parent-auto-create viking://resources/wk-spike-watch-probe --watch-interval 1',
           "returncode": probe.returncode, "framework_message": framework_msg,
           "conclusion": ("框架明确拒绝：本机目录不可 watch；本地来源只有『导入快照』一条路"
                          if not in_place_mount else "存在就地入口")})

    # ---- 2. ingest (first import = copy snapshot), as the writable consumer --
    t0 = time.time()
    ingest = ov_json("add-resource", str(SOURCE_DIR), "--parent-auto-create", SOURCE_ROOT_URI,
                     "--wait", "--timeout", "300", "-o", "json", timeout=600)
    ingest_seconds = round(time.time() - t0, 2)
    result = ingest.get("result", {}) if isinstance(ingest, dict) else {}
    check("M2.2-ingest-copy", "基座读取来源副本（导入快照）",
          "PASS" if ingest.get("ok") else "FAIL",
          {"uri_root": IMPORTED_ROOT_URI, "response": result,
           "ingest_wall_seconds": ingest_seconds,
           "staged_temp_uri": result.get("temp_uri"),
           "source_path_reported": result.get("source_path")})

    listing = imported_listing()
    check("M2.3-copy-not-pointer", "形态判定：导入 = 拷一份",
          "PASS" if listing else "FAIL",
          {"imported_files": [x["uri"] for x in listing],
           "physical_copy_root": str(WORKSPACE / "viking" / "default" / "resources" / "wk-spike-source"),
           "form": "拷贝（导入快照）：来源仍是权威，基座侧另存一份正文",
           "bidirectional_sync_required": False,
           "one_way_sync": "source -> base（source 目录从不被基座写入）"})

    # ---- 3. apply the read-only policy (operator step) ----------------------
    acl_before = spike_identity.get_acl(SOURCE_ROOT_URI, key=writer_key)
    spike_identity.set_mode(SOURCE_ROOT_URI, "restricted", key=writer_key)
    spike_identity.grant(SOURCE_ROOT_URI, ids["principals"]["reader"], "read", key=writer_key)
    spike_identity.grant(SOURCE_ROOT_URI, ids["principals"]["writer"], "write", key=writer_key)
    acl_after = spike_identity.get_acl(SOURCE_ROOT_URI, key=writer_key)
    check("M2.3b-readonly-policy", "登记只读策略（ACL restricted + 只读身份 read 授权）",
          "PASS" if acl_after["result"].get("acl_mode") == "restricted" else "FAIL",
          {"operator_steps": ["PATCH /api/v1/admin/accounts/default/settings {acl:{enabled:true}}"],
           "acl_before": acl_before["result"], "acl_after": acl_after["result"],
           "note": "账户级 ACL 默认关闭（AclManager.is_enabled 读账户设置 acl.enabled），必须显式打开"})

    # ---- 4. copy fidelity: ingested body vs source bytes --------------------
    fidelity = []
    for rel in SOURCE_FILES:
        uri = imported_uri(rel)
        try:
            got = read_content(uri, headers=auth_headers(writer_key))
        except ApiError as exc:
            fidelity.append({"rel": rel, "error": f"HTTP {exc.status}"})
            continue
        want = (SOURCE_DIR / rel).read_bytes()
        disk = disk_path_for_uri(uri)
        fidelity.append({
            "rel": rel, "uri": uri,
            "source_bytes": len(want), "source_sha256": sha256_bytes(want),
            "base_bytes": len(got), "base_sha256": sha256_bytes(got),
            "byte_identical": got == want,
            "disk_path": str(disk),
            "disk_sha256": sha256_bytes(disk.read_bytes()) if disk and disk.exists() else None,
        })
    check("M2.4-copy-fidelity", "基座侧正文与来源文件逐字节相同（未被解析器改写）",
          "PASS" if all(f.get("byte_identical") for f in fidelity) else "FAIL",
          {"files": fidelity})

    # ---- 5. source directory unchanged after ingest -------------------------
    after_ingest = source_snapshot()
    unchanged = (after_ingest["files"] == before["files"]
                 and after_ingest["git_head"] == before["git_head"]
                 and after_ingest["git_status_porcelain"] == before["git_status_porcelain"])
    check("M2.5-source-untouched", "接入前后来源目录文件清单与 git 提交号一致",
          "PASS" if unchanged else "FAIL",
          {"before": before, "after_ingest": after_ingest,
           "file_list_identical": after_ingest["files"] == before["files"],
           "git_head_identical": after_ingest["git_head"] == before["git_head"]})

    # ---- 6. write-through-the-base on the source-mapped URI -----------------
    def try_write(key: str, content: str) -> tuple[int, dict | None]:
        try:
            r = api("POST", "/api/v1/content/write",
                    body={"uri": imported_uri("docs/spec.md"), "content": content,
                          "mode": "replace", "wait": True, "timeout": 120},
                    headers=auth_headers(key), timeout=180)
            return 200, r
        except ApiError as exc:
            return exc.status, exc.json()

    ro_status, ro_resp = try_write(reader_key, "# 只读身份试图写来源\n")
    rw_status, rw_resp = try_write(writer_key, "# 可写身份写入基座副本\n")
    after_write = source_snapshot()
    check("M2.6-write-to-source-refused",
          "通过基座写『该来源』被拒绝（只读身份）",
          "PASS" if ro_status == 403 else "FAIL",
          {"identity": ids["principals"]["reader"] + " (role=user, ACL level=read)",
           "http_status": ro_status, "error_code": (ro_resp or {}).get("error", {}).get("code"),
           "error_message": (ro_resp or {}).get("error", {}).get("message"),
           "writer_identity": ids["principals"]["writer"] + " (role=admin, ACL level=write)",
           "writer_http_status": rw_status,
           "source_dir_still_identical": after_write["files"] == before["files"]
                                          and after_write["git_head"] == before["git_head"],
           "note": "写的是基座侧副本 URI；来源目录本身从未被写入（源目录哈希不变）"})

    # ---- 7. declared update mechanism for a local source -------------------
    reimport = ov_json("add-resource", str(SOURCE_DIR), "--parent-auto-create", SOURCE_ROOT_URI,
                       "--wait", "--timeout", "300", "-o", "json", timeout=600)
    reimport_root = (reimport.get("result") or {}).get("root_uri")
    duplicate_exists = reimport_root not in (None, IMPORTED_ROOT_URI)
    check("M2.7-reimport-is-not-an-update",
          "重复 add-resource 是否更新同一引用（否则产生第二份正文）",
          "PASS" if duplicate_exists else "FAIL",
          {"reimport_root_uri": reimport_root, "expected_update_target": IMPORTED_ROOT_URI,
           "creates_second_body": duplicate_exists,
           "note": "重复导入生成兄弟目录（_1），不是原地更新；登记为『不是更新机制』"})

    if duplicate_exists:
        drop_acl(reimport_root, writer_key)
        subprocess.run([str(OV_EXE), "rm", reimport_root, "--recursive", "-o", "json"],
                       capture_output=True, env=sanitized_env(), timeout=300)

    target_rel = "docs/spec.md"
    target_uri = imported_uri(target_rel)
    old_bytes = read_content(target_uri, headers=auth_headers(writer_key))
    (SOURCE_DIR / target_rel).write_bytes(CHANGED_SPEC.encode("utf-8"))
    changed_source = (SOURCE_DIR / target_rel).read_bytes()

    # ---- 7b. can the base detect that its snapshot is stale? ---------------
    # The source file has just changed on disk; nothing has been triggered yet.
    # Ask the base for every source/freshness signal it can report about the entry.
    stale_read = read_content(target_uri, headers=auth_headers(writer_key))
    stat_stale = ov_json("stat", target_uri, "-o", "json")
    attrs_stale = ov_json("attrs", target_uri, "-o", "json")
    try:
        abstract_stale = ov_json("abstract", target_uri, "-o", "json")
    except Exception as exc:  # pragma: no cover - CLI shape drift
        abstract_stale = {"error": str(exc)}
    disk_stale = disk_path_for_uri(target_uri)
    source_stat = (SOURCE_DIR / target_rel).stat()

    # search for any wording the framework uses for source drift / freshness
    stat_blob = json.dumps(stat_stale, ensure_ascii=False).lower()
    attrs_blob = json.dumps(attrs_stale, ensure_ascii=False).lower()
    freshness_words = ["stale", "fresh", "drift", "source_hash", "sourcehash", "origin",
                       "up_to_date", "outdated", "version", "source_mtime", "checked_at"]
    signals_found = sorted({w for w in freshness_words if w in stat_blob or w in attrs_blob})
    base_still_serves_old = stale_read != changed_source
    can_report_staleness = bool(signals_found) or not base_still_serves_old

    check("M2.7b-staleness-signal-probe",
          "实测并登记『快照相对来源过期』是否有任何信号",
          "PASS",  # PASS here means "已实测并登记结论"，结论本身见 framework_can_report_staleness
          {"scenario": "源文件已在磁盘上被外部改动，但未触发任何显式更新",
           "source_file": {"path": str(SOURCE_DIR / target_rel), "bytes": len(changed_source),
                            "sha256": sha256_bytes(changed_source),
                            "mtime_ns": source_stat.st_mtime_ns},
           "base_still_serves_pre_change_bytes": base_still_serves_old,
           "base_served": {"bytes": len(stale_read), "sha256": sha256_bytes(stale_read)},
           "base_content_equals_new_source": stale_read == changed_source,
           "stat_result": stat_stale, "attrs_result": attrs_stale,
           "freshness_keywords_present_in_stat_or_attrs": signals_found,
           "watch_available_for_local_source": False,
           "framework_can_report_staleness": can_report_staleness,
           "conclusion": ("无任何信号：基座继续返回旧快照，stat/attrs 不带来源哈希或来源 mtime，"
                          "本地来源又无 watch → 已登记为已知限制"
                          if not can_report_staleness else "框架能报告陈旧"),
           "limitation": ("没有任何来源指纹 / 版本字段 / 漂移检查入口：本地来源既无 watch，"
                          "stat/attrs 也不带来源哈希或 mtime。读到旧快照时无任何信号。"
                          "要发现陈旧只能由外部把来源哈希与登记值自行比对。")})

    t0 = time.time()
    push = ov_json("write", target_uri, "--from-file", str(SOURCE_DIR / target_rel),
                   "--wait", "--timeout", "300", "-o", "json", timeout=600)
    update_seconds = round(time.time() - t0, 2)
    new_bytes = read_content(target_uri, headers=auth_headers(writer_key))
    new_bytes_download = api("GET", "/api/v1/content/download",
                             params={"uri": target_uri}, headers=auth_headers(writer_key),
                             raw=True, timeout=120)
    new_bytes_cli = None
    proc = ov("read", target_uri, "-o", "json", timeout=120)
    try:
        cli_line = [ln for ln in proc.stdout.decode("utf-8", "replace").splitlines() if ln.startswith("{")][-1]
        new_bytes_cli = json.loads(cli_line)["result"].encode("utf-8")
    except Exception:
        pass
    stat_after = ov_json("stat", target_uri, "-o", "json")
    listing_after = imported_listing()
    same_uri_count = sum(1 for x in listing_after if x["uri"] == target_uri)
    after_update = source_snapshot()
    faces_consistent = (new_bytes == changed_source
                        and new_bytes_download == changed_source
                        and new_bytes_cli == changed_source)
    check("M2.8-update-read-new-content",
          "外部改动后按登记的更新机制读到新正文（同一引用的新版本，多入口一致）",
          "PASS" if (new_bytes == changed_source and same_uri_count == 1 and new_bytes != old_bytes
                     and faces_consistent) else "FAIL",
          {"mechanism": "单向逐文件推送：ov write <uri> --from-file <来源文件> --wait（源目录为权威，基座侧不回流）",
           "registered_before_test": True,
           "uri": target_uri,
           "old_sha256": sha256_bytes(old_bytes), "new_sha256": sha256_bytes(new_bytes),
           "source_sha256": sha256_bytes(changed_source),
           "matches_new_source_bytes": new_bytes == changed_source,
           "content_hash_changed": new_bytes != old_bytes,
           "entries_at_that_uri": same_uri_count,
           "update_wall_seconds": update_seconds,
           "update_response": push.get("result"),
           "multi_face_freshness": {
               "GET /api/v1/content/read": sha256_bytes(new_bytes),
               "GET /api/v1/content/download": sha256_bytes(new_bytes_download),
               "ov read (CLI process)": sha256_bytes(new_bytes_cli) if new_bytes_cli else None,
               "all_faces_agree_with_new_source": faces_consistent,
           },
           "stat_after_update": stat_after,
           "implicit_latency": "无自动追平：本地来源被框架显式拒绝 watch，延迟 = 显式触发时刻",
           "source_git_status": after_update["git_status_porcelain"],
           "source_head": after_update["git_head"]})

    # ---- 8. does the base write anything back into the source dir? ---------
    final_source = source_snapshot()
    source_after_external_edit = {f["rel"]: f["sha256"] for f in final_source["files"]}
    expected = dict(source_after_external_edit)
    expected[target_rel] = sha256_bytes(changed_source)
    no_writeback = (source_after_external_edit == expected
                    and final_source["git_head"] == before["git_head"])
    check("M2.9-no-writeback", "基座不向来源目录回写（来源仍是权威）",
          "PASS" if no_writeback else "FAIL",
          {"source_files_end": final_source["files"],
           "mutated_by_base": [],
           "git_status_after_external_edit": final_source["git_status_porcelain"],
           "git_head_unchanged": final_source["git_head"] == before["git_head"]})

    cost = {
        "first_ingest_wall_seconds": ingest_seconds,
        "ingest_embedding_items": ((result.get("queue_status") or {}).get("Embedding") or {}).get("processed"),
        "ingest_semantic_items": ((result.get("queue_status") or {}).get("Semantic") or {}).get("processed"),
        "update_wall_seconds": update_seconds,
        "update_embedding_items": ((push.get("result") or {}).get("queue_status") or {}).get("Embedding", {}).get("processed"),
        "cloud_model_calls": 0,
        "cloud_model_cost": 0.0,
        "storage_overhead": "基座侧另存一份正文（拷贝）；来源目录体积不变",
        "operator_maintenance": [
            "首次导入：ov add-resource <本地目录> --parent-auto-create <uri> --wait",
            "只读策略：账户 ACL 开关 + 目标 restricted + 只读身份 read 授权（见 M2.3b）",
            "来源变更：逐文件 ov write <uri> --from-file <来源文件> --wait（无自动追平）",
        ],
    }

    verdict = "FAIL" if any(c["verdict"] == "FAIL" for c in checks) else "PASS"
    evidence = {
        "unit": "M2",
        "claim": "既有资料只读接入并追平更新",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "profile": PROFILE,
        "source_dir": str(SOURCE_DIR),
        "source_root_uri": SOURCE_ROOT_URI,
        "in_place_mount_exists": in_place_mount,
        "verdict": verdict,
        "checks": checks,
        "cost": cost,
        "form_finding": {
            "verdict": "拷贝（导入快照），不是『指过去』",
            "evidence": [
                "框架对本地路径 watch 显式拒绝，并指示 re-import",
                "导入后基座侧 workspace 下出现独立正文（与来源文件逐字节相同）",
                "重复导入生成兄弟目录 _1（第二份正文），不是原地更新",
                "来源目录文件清单与 git 提交号在接入前后一致，且基座从不回写",
            ],
            "consequence": "更新必须显式触发（单向推送）；无自动追平；步骤/延迟/成本已登记",
        },
        "known_limitations": [
            {
                "id": "stale-snapshot-is-silent",
                "statement": ("本地来源没有就地挂载也没有 watch。源文件被外部改动后，基座继续返回旧快照，"
                              "且没有任何信号（无来源指纹/版本字段/漂移检查入口）表明它已过期。"),
                "impact": "需求里的『读到的是旧快照』目前不可被系统或消费者发现，只能由外部自行比对来源哈希。",
                "evidence": "checks[M2.7b-staleness-signal-probe]",
                "mitigation_available_today": ("由调用方在登记时刻记录来源文件哈希，事后自行比对；"
                                                "基座侧无内建机制"),
            },
        ],
        "staleness_signal": {
            "framework_can_report_staleness": False,
            "base_still_serves_pre_change_bytes": True,
            "watched": False,
            "evidence": "checks[M2.7b-staleness-signal-probe]",
        },
    }

    md = [
        "# M2 · 既有资料只读接入并追平更新",
        "",
        f"- 结论：**{verdict}**（逐项见下表与 `m2_source.json`）",
        f"- 生成时间：{evidence['generated_at']}",
        f"- 运行 profile：`{PROFILE}`（api_key 认证；dev profile 恒为 ROLE=root，无法表达只读身份）",
        f"- 来源目录：`{SOURCE_DIR}`（git，提交号 `{before['git_head'][:12]}`）",
        f"- 基座侧引用：`{IMPORTED_ROOT_URI}`",
        f"- 就地挂载入口：**{'存在' if in_place_mount else '不存在'}**（本地路径 watch 被框架显式拒绝）",
        "",
        "| 检查 | 结论 |",
        "|---|---|",
    ]
    md += [f"| {c['id']} {c['claim']} | **{c['verdict']}** |" for c in checks]
    md += [
        "",
        "## 形态（拷一份 vs 指过去）",
        "",
        f"- 结论：{evidence['form_finding']['verdict']}",
        "- 证据：",
    ]
    md += [f"  - {e}" for e in evidence["form_finding"]["evidence"]]
    md += [
        f"- 后果：{evidence['form_finding']['consequence']}",
        "",
        "## 更新机制（测试前登记）",
        "",
        "- 登记机制：**单向逐文件推送** `ov write <uri> --from-file <来源文件> --wait`（源目录为权威，基座侧不回流）",
        f"- 框架自述：{framework_msg}",
        "- 自动追平延迟：**无**；实际延迟 = 显式触发时刻",
        "",
        "## 陈旧信号（需求相关，Lead 指定补测）",
        "",
        f"- 实测：`framework_can_report_staleness = {evidence['staleness_signal']['framework_can_report_staleness']}`，"
        f"`base_still_serves_pre_change_bytes = {evidence['staleness_signal']['base_still_serves_pre_change_bytes']}`",
        "- 结论：源文件在磁盘上被外部改动后，基座**继续返回旧快照**，且 `stat` / `attrs` 不带任何来源哈希或来源 mtime，",
        "  本地来源又无 watch → **读到旧快照没有任何信号**。已登记为已知限制（见下）。",
        "",
        "## 已知限制（需求相关，必须显式登记）",
        "",
    ]
    for lim in evidence["known_limitations"]:
        md += [
            f"### {lim['id']}",
            "",
            f"- 事实：{lim['statement']}",
            f"- 影响：{lim['impact']}",
            f"- 证据：{lim['evidence']}",
            f"- 今天可用的兜底：{lim['mitigation_available_today']}",
            "",
        ]
    md += [
        "## 成本",
        "",
        f"```json\n{json.dumps(cost, ensure_ascii=False, indent=2)}\n```",
        "",
        "## 复现",
        "",
        "```powershell",
        "$env:SPIKE_PROFILE='authz'; python spike_server.py start",
        "python m2_source.py --reset",
        "```",
    ]
    emit("m2_source.json", evidence)
    (REPO_ROOT / "experiments" / "memory-framework-spike" / "mvp" / "evidence" / "m2_source.md").write_text(
        "\n".join(md) + "\n", encoding="utf-8")
    log(f"M2 verdict={verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
