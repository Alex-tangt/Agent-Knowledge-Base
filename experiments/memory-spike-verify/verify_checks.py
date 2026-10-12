# -*- coding: utf-8 -*-
"""检查实现：静态自检（--stage static）与隔离实例实跑（--stage live）。

静态阶段：不联网、不启服务，只证明"判定标准明确 + 脚本能跑 + 输出可复算哈希 +
写域不外溢 + 预期来自框架源码字节"。
实跑阶段：在本线隔离实例（8783 / %USERPROFILE%\\.openviking-mvp-verify）上重跑五条检查。
"""

from __future__ import annotations

import ast
import json
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import verify_criteria as C
import verify_env as E
import verify_probe as P


# ===========================================================================
# 静态自检
# ===========================================================================

def _class_fields(source: str, class_name: str) -> Dict[str, Any]:
    """用 ast 取出某个类声明的字段名与类源码文本（框架源码级静态证据）。"""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            fields: List[str] = []
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    fields.append(stmt.target.id)
                elif isinstance(stmt, ast.Assign):
                    for t in stmt.targets:
                        if isinstance(t, ast.Name):
                            fields.append(t.id)
            return {"fields": sorted(fields), "src": ast.get_source_segment(source, node) or ""}
    raise KeyError(class_name)


def _framework_file(rel: str) -> Dict[str, Any]:
    p = E.VENV_OV.parent / "Lib" / "site-packages" / rel
    data = p.read_bytes()
    return {"path": str(p), "sha256": E.sha256_bytes(data), "text": data.decode("utf-8")}


def run_static() -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    problems: List[str] = []

    def add(name: str, ok: bool, detail: Any) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            problems.append(name)

    # --- 1. 判定标准结构完整 ------------------------------------------------
    struct_problems = C.structural_audit()
    add("criteria_structure", not struct_problems, struct_problems)
    add(
        "criteria_fingerprint",
        len(C.criteria_sha256()) == 64,
        {"criteria_version": C.CRITERIA_VERSION, "criteria_sha256": C.criteria_sha256()},
    )

    # --- 2. 哈希可复算 ------------------------------------------------------
    sample = "原文往返核验 sample \u3000\t\n"
    h1, h2 = E.sha256_text(sample), E.sha256_text(sample)
    h3 = E.sha256_bytes(sample.encode("utf-8"))
    add("hash_determinism", h1 == h2 == h3 and len(h1) == 64, {"sha256": h1})

    # --- 3. 隔离面 ----------------------------------------------------------
    add("port_isolation", E.PORT == 8783 and E.PORT not in E.FORBIDDEN_PORTS,
        {"port": E.PORT, "forbidden_ports": sorted(E.FORBIDDEN_PORTS)})
    in_repo = E._is_under(E.VERIFY_ROOT, E.MAIN_TREE)
    add("verify_root_outside_repo", not in_repo, {"verify_root": str(E.VERIFY_ROOT)})
    add("forbidden_paths_declared", len(E.FORBIDDEN_PATHS) >= 4,
        [str(p) for p in E.FORBIDDEN_PATHS])

    # 写域守卫：必须拒绝仓库内路径 / 禁止路径，必须接受本线两个根
    guard: Dict[str, Any] = {}
    for label, target, expect_ok in [
        ("main_tree_file", E.MAIN_TREE / "AGENTS.md", False),
        ("real_kb", E.FORBIDDEN_PATHS[0], False),
        ("old_daemon_conf", E.FORBIDDEN_PATHS[2], False),
        ("real_index", E.FORBIDDEN_PATHS[3], False),
        ("verify_root", E.VERIFY_ROOT / "guard_probe.txt", True),
        ("module_dir", E.VERIFY_MODULE_DIR / "guard_probe.txt", True),
    ]:
        try:
            E.assert_writable(target)
            got = True
        except PermissionError:
            got = False
        guard[label] = {"target": str(target), "allowed": got, "expected": expect_ok}
    add("write_guard", all(v["allowed"] == v["expected"] for v in guard.values()), guard)

    # BOM 检测器自证（在隔离根里造两个临时文件）
    E.ensure_dirs()
    bom_ok = False
    try:
        from_bom = E.VERIFY_ROOT / "_bom_probe.conf"
        from_clean = E.VERIFY_ROOT / "_clean_probe.conf"
        E.assert_writable(from_bom).write_bytes(b"\xef\xbb\xbf{}")
        E.assert_writable(from_clean).write_bytes(b"{}")
        bom_ok = E.has_bom(from_bom) and not E.has_bom(from_clean)
        from_bom.unlink(missing_ok=True)
        from_clean.unlink(missing_ok=True)
    except Exception as exc:
        problems.append(f"bom_probe: {exc}")
    add("bom_detector", bom_ok, {"has_bom_on_bom_file": True, "has_bom_on_clean_file": False})

    add("no_cloud_model_in_conf",
        json.loads(json.dumps(E.build_conf(E.WORKSPACE)))["embedding"]["dense"]["provider"] == "local"
        and "vlm" not in E.build_conf(E.WORKSPACE)
        and "query_planner" not in E.build_conf(E.WORKSPACE),
        {"embedding": E.build_conf(E.WORKSPACE)["embedding"],
         "vlm_section_present": "vlm" in E.build_conf(E.WORKSPACE)})

    add("novproxy_sanitizer",
        all("[" not in s for s in E.child_env().get("NO_PROXY", "").split(",")),
        {"NO_PROXY": E.child_env().get("NO_PROXY", "")[:200]})

    # --- 4. 框架源码静态证据（预期锚定到字节） ------------------------------
    content_src = _framework_file(r"openviking\server\routers\content.py")
    resources_src = _framework_file(r"openviking\server\routers\resources.py")
    guard_src = _framework_file(r"openviking\server\local_input_guard.py")
    sdk_src = _framework_file(r"openviking_sdk\client.py")
    errors_src = _framework_file(r"openviking_sdk\errors.py")

    wcr = _class_fields(content_src["text"], "WriteContentRequest")
    arr = _class_fields(resources_src["text"], "AddResourceRequest")
    static_expectations: Dict[str, Any] = {
        "write_request_fields": wcr["fields"],
        "write_request_extra_forbid": 'extra="forbid"' in wcr["src"],
        "write_request_has_idempotency_key": any(
            f in wcr["fields"] for f in ("request_id", "idempotency_key", "client_request_id")
        ),
        "add_resource_fields": arr["fields"],
        "add_resource_extra_forbid": 'extra="forbid"' in arr["src"],
        "add_resource_has_idempotency_key": any(
            f in arr["fields"] for f in ("request_id", "idempotency_key", "client_request_id")
        ),
        "http_rejects_local_paths": "direct host filesystem paths are not allowed" in guard_src["text"],
        "sdk_zips_local_dir_then_uploads": (
            "_zip_directory" in sdk_src["text"] and "request_data[\"temp_file_id\"]" in sdk_src["text"]
        ),
        "upload_watch_interval_rejected": (
            "not supported for uploaded content" in resources_src["text"]
        ),
        "has_unavailable_error_class": "class UnavailableError" in errors_src["text"],
        "source_sha256": {
            "content.py": content_src["sha256"],
            "resources.py": resources_src["sha256"],
            "local_input_guard.py": guard_src["sha256"],
            "openviking_sdk/client.py": sdk_src["sha256"],
            "openviking_sdk/errors.py": errors_src["sha256"],
        },
    }
    add("framework_static_expectations",
        not static_expectations["write_request_has_idempotency_key"]
        and not static_expectations["add_resource_has_idempotency_key"]
        and static_expectations["http_rejects_local_paths"]
        and static_expectations["upload_watch_interval_rejected"]
        and static_expectations["has_unavailable_error_class"],
        static_expectations)

    # --- 5. 版本口径 --------------------------------------------------------
    versions: Dict[str, Any] = {}
    try:
        from importlib.metadata import version

        for name in ("openviking", "openviking-sdk"):
            try:
                versions[name] = version(name)
            except Exception as exc:
                versions[name] = f"<{type(exc).__name__}: {exc}>"
    except Exception as exc:
        versions["error"] = f"{type(exc).__name__}: {exc}"
    add("versions_recorded", "openviking" in versions, versions)

    # --- 6. 工具链自指纹（判定标准 + 全部检查脚本的字节） --------------------
    harness: Dict[str, str] = {}
    for name in ("verify_criteria.py", "verify_env.py", "verify_probe.py",
                 "verify_checks.py", "run_verify.py"):
        p = E.VERIFY_MODULE_DIR / name
        if p.exists():
            harness[name] = E.sha256_bytes(p.read_bytes())
    add("harness_pinned", len(harness) >= 4, harness)

    payload: Dict[str, Any] = {
        "stage": "static",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "criteria_version": C.CRITERIA_VERSION,
        "criteria_sha256": C.criteria_sha256(),
        "criteria_file_sha256": harness.get("verify_criteria.py"),
        "harness_sha256": harness,
        "pre_registered_at": C.PRE_REGISTERED_AT,
        "isolation": C.ISOLATION,
        "params": C.criteria_payload()["params"],
        "checks": checks,
        "static_expectations": static_expectations,
        "problems": problems,
        "verdict": "PASS" if not problems else "FAIL",
    }
    payload["payload_sha256"] = E.sha256_text(
        E.sha256_text(json.dumps({k: v for k, v in payload.items() if k != "payload_sha256"},
                                 ensure_ascii=False, sort_keys=True))
    )
    return payload


# ===========================================================================
# 预检：只测"测量仪器"本身（正控 + 负控），不启服务
# ===========================================================================

def _rmtree_readonly_safe(path: Path) -> None:
    """git 对象文件在 Windows 上是只读的，直接 rmtree 会 WinError 5。"""
    import shutil
    import stat

    if not path.exists():
        return

    def _onexc(func, p, exc):  # noqa: ANN001
        try:
            import os as _os

            _os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass

    shutil.rmtree(path, onexc=_onexc)


def run_preflight() -> Dict[str, Any]:
    """证明观测原语真能分辨"找到/找不到"与"逐字节相等/不等"。

    一个复核工具若不先证明自己能测出阳性与阴性，它报的 PASS 没有意义。
    """
    import subprocess

    run_id = "preflight-" + secrets.token_hex(3)
    marker = f"{C.MARKER_PREFIX}-{run_id}"
    items: List[Dict[str, Any]] = []
    problems: List[str] = []

    def add(name: str, ok: bool, detail: Any) -> None:
        items.append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            problems.append(name)

    E.ensure_dirs()
    src = E.VERIFY_ROOT / "source-tree"
    _rmtree_readonly_safe(src)
    files = P.write_source_tree(src, marker)
    before = P.source_snapshot(src)
    add("source_tree_has_git_head", bool(before["git_head"]) and len(before["files"]) == 3,
        {"git_head": before["git_head"], "files": sorted(before["files"])})

    # 阴性对照：workspace 里没有该正文时，扫描必须找不到
    a_bytes = files["a.md"].encode("utf-8")
    neg = P.scan_for_bytes(E.WORKSPACE, marker.encode("utf-8"), a_bytes)
    ws_before = {h["path"] for h in neg["marker_hits"]}
    add("scan_negative_control", not ws_before,
        {"marker_hits": sorted(ws_before)})

    # 阳性对照：把正文放到 workspace 后，扫描必须命中且逐字节相等
    probe_copy = E.WORKSPACE / "_preflight_copy.md"
    E.assert_writable(probe_copy).write_bytes(a_bytes)
    pos = P.scan_for_bytes(E.WORKSPACE, marker.encode("utf-8"), a_bytes)
    hit = next((h for h in pos["full_content_hits"] if h["path"] == str(probe_copy)), None)
    add("scan_positive_control", hit is not None,
        {"full_content_hits": [h["path"] for h in pos["full_content_hits"]],
         "hit": hit})

    # 改写检测：改一个字节后，完整原文不得再命中（证明"改写"能被发现）
    tampered = E.WORKSPACE / "_preflight_tampered.md"
    E.assert_writable(tampered).write_bytes(a_bytes[:-2] + "XX".encode("utf-8"))
    tam = P.scan_for_bytes(E.WORKSPACE, marker.encode("utf-8"), a_bytes)
    tampered_full = [h for h in tam["full_content_hits"] if h["path"] == str(tampered)]
    tampered_marker = [h for h in tam["marker_hits"] if h["path"] == str(tampered)]
    add("scan_detects_rewrite", not tampered_full and bool(tampered_marker),
        {"tampered_full_hits": [h["path"] for h in tampered_full],
         "tampered_marker_hits": [h["path"] for h in tampered_marker]})

    # git 对象核对：blob 级逐字节相等必须能测出
    probe_git = E.WORKSPACE / "_preflight_git"
    _rmtree_readonly_safe(probe_git)
    probe_git.mkdir(parents=True)
    (probe_git / "a.md").write_bytes(a_bytes)
    for cmd in (["init", "-q"], ["config", "user.email", "v@local"], ["config", "user.name", "v"],
                ["add", "-A"], ["commit", "-q", "-m", "probe"]):
        subprocess.run(["git", "-C", str(probe_git), *cmd], capture_output=True, text=True)
    gitres = P.scan_git_blobs(E.WORKSPACE, marker.encode("utf-8"), a_bytes)
    exact = [b for b in gitres["blobs"] if b["exact_equal_to_input"]]
    add("git_blob_positive_control", bool(exact),
        {"repos": gitres["repos"], "exact_blobs": exact, "examined": gitres["examined"]})

    # 改动可被 diff 测出
    (src / "a.md").write_text(P.make_modified_a(marker), encoding="utf-8")
    after = P.source_snapshot(src)
    diff = P.snapshot_diff(before, after)
    add("snapshot_diff_detects_change",
        diff["changed"] == ["a.md"] and not diff["identical"]
        and diff["before"]["files"]["a.md"] != diff["after"]["files"]["a.md"],
        {"changed": diff["changed"], "git_head_same": diff["git_head_same"]})

    # 清理探针（只清本线 verify_root 内自己造的东西），并复核 workspace 已干净
    cleanup_ok = True
    for p in (probe_copy, tampered):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            cleanup_ok = False
    try:
        _rmtree_readonly_safe(probe_git)
    except OSError as exc:
        cleanup_ok = False
        problems.append(f"cleanup probe_git: {exc}")
    E.write_conf_files()  # 顺带固定 conf 形态
    final_scan = P.scan_for_bytes(E.WORKSPACE, marker.encode("utf-8"), a_bytes)
    add("cleanup_leaves_no_marker", cleanup_ok and not final_scan["marker_hits"],
        {"remaining_marker_hits": [h["path"] for h in final_scan["marker_hits"]]})

    payload: Dict[str, Any] = {
        "stage": "preflight",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "criteria_sha256": C.criteria_sha256(),
        "checks": items,
        "problems": problems,
        "verdict": "PASS" if not problems else "FAIL",
    }
    payload["payload_sha256"] = E.sha256_text(
        json.dumps({k: v for k, v in payload.items() if k != "payload_sha256"},
                   ensure_ascii=False, sort_keys=True, default=str)
    )
    return payload


# ===========================================================================
# 附加：CLI 读回模式（执行方 M1.5 用的是 `ov read -o json`）
# ===========================================================================

def run_cli_read_modes() -> Dict[str, Any]:
    """核对独立进程 `ov read` 两种输出模式的字节口径。

    执行方 M1.5 声称"独立进程 ov read 逐字节相同"，其命令是 `ov read <uri> -o json`；
    我方实测默认（人读）输出会**多一个尾随换行**。这里把两种模式都测出来，
    以便把"逐字节相同"这句话限定到正确的口径上。
    """
    run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)
    marker = f"{C.MARKER_PREFIX}-CLI-{run_id}"
    text = P.make_roundtrip_text(marker)
    uri = f"viking://user/default/verify-cli/{run_id}/cli.md"
    obs: Dict[str, Any] = {
        "run_id": run_id,
        "uri": uri,
        "input_bytes": len(text.encode("utf-8")),
        "input_sha256": E.sha256_text(text),
    }
    E.ensure_dirs()
    E.write_conf_files()
    E.pin_process_env()  # 必须在任何 SDK 客户端之前：别读到真实 ~/.openviking/ovcli.conf
    started = E.start_server()
    obs["start"] = started
    if not started.get("started"):
        obs["fatal"] = "实例未启动"
        return _cli_finish(obs)
    obs["ready"] = wait_ready()
    if not obs["ready"].get("ready"):
        obs["fatal"] = "实例未就绪"
        return _cli_finish(obs, started)

    consumer = P.Consumer("cli-verify")
    try:
        consumer.open()
        try:
            obs["write"] = consumer.write(uri, text, mode="create", wait=True)
            obs["sdk_read_raw_equal"] = consumer.read_raw(uri) == text
        except Exception as exc:
            obs["write_error"] = _err(exc)
    except Exception as exc:
        obs["fatal"] = f"消费者不可用: {type(exc).__name__}: {exc}"
        return _cli_finish(obs, started)
    finally:
        import contextlib

        with contextlib.suppress(Exception):
            consumer.close()

    for label, args in (
        ("default", ["read", uri]),
        ("json", ["read", uri, "-o", "json"]),
    ):
        try:
            res = run_ov(args)
        except Exception as exc:
            obs[f"ov_read_{label}"] = {"error": _err(exc)}
            continue
        out = res.get("stdout") or ""
        raw = out.encode("utf-8")
        entry: Dict[str, Any] = {
            "argv": res.get("args"),
            "rc": res.get("rc"),
            "stdout_bytes": len(raw),
            "stdout_sha256": E.sha256_bytes(raw),
            "byte_identical_to_input": raw == text.encode("utf-8"),
            "stdout_head": out[:160],
            "stderr_head": (res.get("stderr") or "")[:160],
        }
        if label == "json":
            try:
                obj = json.loads(out)
                entry["json_keys"] = sorted(obj.keys()) if isinstance(obj, dict) else None
                content = None
                if isinstance(obj, dict):
                    for key in ("content", "result", "text", "data"):
                        val = obj.get(key)
                        if isinstance(val, str):
                            content = val
                            break
                        if isinstance(val, dict) and isinstance(val.get("content"), str):
                            content = val["content"]
                            break
                if content is not None:
                    entry["extracted_bytes"] = len(content.encode("utf-8"))
                    entry["extracted_sha256"] = E.sha256_text(content)
                    entry["extracted_byte_identical"] = content == text
            except Exception as exc:
                entry["json_parse_error"] = f"{type(exc).__name__}: {exc}"
        obs[f"ov_read_{label}"] = entry

    return _cli_finish(obs, started)


def _cli_finish(obs: Dict[str, Any], started: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if started and started.get("started"):
        obs["stop"] = E.stop_server(started.get("pid"), started.get("listener_pid"))
    payload = {
        "stage": "cli",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "criteria_sha256": C.criteria_sha256(),
        "observation": obs,
    }
    payload["payload_sha256"] = E.sha256_text(
        json.dumps({k: v for k, v in payload.items() if k != "payload_sha256"},
                   ensure_ascii=False, sort_keys=True, default=str)
    )
    return payload


# ===========================================================================
# 实跑（隔离实例）
# ===========================================================================

class Recorder:
    """逐 probe 记录观测与判定；判定必须来自预注册规则。"""

    def __init__(self) -> None:
        self.probes: Dict[str, Dict[str, Any]] = {}

    def probe(self, pid: str, verdict: str, observed: Any, note: str = "") -> None:
        self.probes[pid] = {
            "probe": pid,
            "verdict": verdict,
            "observed": observed,
            "note": note,
        }

    def verdicts(self, prefix: str) -> Dict[str, str]:
        return {k: v["verdict"] for k, v in self.probes.items() if k.startswith(prefix)}


def _write_evidence(name: str, obj: Any) -> str:
    p = E.write_json(E.EVIDENCE_DIR / "live" / name, obj)
    return str(p)


def _err(exc: BaseException) -> Dict[str, Any]:
    return {"exception": type(exc).__name__, "module": type(exc).__module__, "message": str(exc)[:600]}


def run_ov(args: List[str]) -> Dict[str, Any]:
    """以隔离 CLI 配置跑 `ov` 二进制（独立进程的真实消费者路径）。"""
    import subprocess

    r = subprocess.run(
        [str(E.OV_EXE), *args],
        capture_output=True,
        env=E.child_env(),
        timeout=C.CALL_TIMEOUT_S,
    )
    return {
        "args": args,
        "rc": r.returncode,
        "stdout": r.stdout.decode("utf-8", errors="replace"),
        "stderr": r.stderr.decode("utf-8", errors="replace")[:600],
    }


def wait_ready(timeout: int = C.SERVER_READY_TIMEOUT_S) -> Dict[str, Any]:
    import httpx

    deadline = time.time() + timeout
    last: Dict[str, Any] = {}
    while time.time() < deadline:
        try:
            h = httpx.get(f"{E.BASE_URL}/health", timeout=5, trust_env=False)
            r = httpx.get(f"{E.BASE_URL}/ready", timeout=5, trust_env=False)
            last = {"health": h.status_code, "ready": r.status_code, "ready_body": r.text[:300]}
            if h.status_code == 200 and r.status_code == 200:
                return {"ready": True, **last}
        except Exception as exc:
            last = {"error": f"{type(exc).__name__}: {exc}"}
        time.sleep(2)
    return {"ready": False, "last": last}


def run_live(auto_start: bool = True) -> Dict[str, Any]:
    run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)
    marker = f"{C.MARKER_PREFIX}-{run_id}"
    rec = Recorder()
    obs: Dict[str, Any] = {"run_id": run_id, "marker": marker}
    started = {"started": False}

    E.ensure_dirs()
    conf = E.write_conf_files()
    obs["conf"] = {k: v for k, v in conf.items()}
    # 驱动器自身的进程环境也钉到隔离配置：SDK/CLI 解析绝不能读到 ~/.openviking 的真实配置
    obs["proxy_env_sanitized"] = E.pin_process_env()

    if auto_start:
        started = E.start_server()
        obs["start"] = started
        obs["ready"] = wait_ready()
        if not obs["ready"].get("ready"):
            obs["fatal"] = "隔离实例未就绪"
            # 不留下半启动的实例：按命令行所有权校验（launcher + 监听进程）停掉再返回
            obs["stop_after_failed_ready"] = E.stop_server(
                started.get("pid"), started.get("listener_pid")
            )
            return _finalize(rec, obs)

    consumer = P.Consumer("verify-a")
    consumer.open()
    try:
        # ---------------- C1 原文往返 ----------------
        text = P.make_roundtrip_text(marker)
        entry_uri = f"viking://user/default/verify/{run_id}/roundtrip.md"
        c1: Dict[str, Any] = {"uri": entry_uri, "input_sha256": E.sha256_text(text),
                              "input_bytes": len(text.encode("utf-8"))}
        try:
            c1["write_result"] = consumer.write(entry_uri, text, mode="create", wait=True)
        except Exception as exc:
            c1["write_error"] = _err(exc)
        try:
            raw = consumer.read_raw(entry_uri)
            c1["raw_sha256"] = E.sha256_text(raw)
            c1["raw_equal"] = raw == text
            if not c1["raw_equal"]:
                c1["raw_first_divergence"] = E.first_divergence(raw.encode(), text.encode())
                c1["raw_len"] = len(raw.encode("utf-8"))
        except Exception as exc:
            c1["raw_error"] = _err(exc)
        try:
            dflt = consumer.read_default(entry_uri)
            c1["default_sha256"] = E.sha256_text(dflt)
            c1["default_equal"] = dflt == text
            if dflt != text:
                c1["default_first_divergence"] = E.first_divergence(dflt.encode(), text.encode())
                c1["default_text"] = dflt[:2000]
        except Exception as exc:
            c1["default_error"] = _err(exc)

        disk = P.scan_for_bytes(E.WORKSPACE, marker.encode("utf-8"), text.encode("utf-8"))
        gitres = P.scan_git_blobs(E.WORKSPACE, marker.encode("utf-8"), text.encode("utf-8"))
        c1["disk"] = disk
        c1["git"] = gitres
        obs["C1"] = c1
        obs["C1_evidence"] = _write_evidence("C1_roundtrip.json", c1)

        rec.probe(
            "C1a",
            "PASS" if c1.get("raw_equal") else ("FAIL" if "raw_sha256" in c1 else "INCONCLUSIVE"),
            {"input_sha256": c1["input_sha256"], "raw_sha256": c1.get("raw_sha256")},
            c1.get("raw_error", {}).get("message", ""),
        )
        exact_disk = bool(disk["full_content_hits"]) or any(
            b.get("exact_equal_to_input") for b in gitres["blobs"]
        )
        marker_persisted = bool(disk["marker_hits"]) or bool(gitres["blobs"])
        if exact_disk:
            c1b_verdict, c1b_note = "PASS", "磁盘/ git 对象中找到与输入逐字节相等的持久化正文"
        elif marker_persisted:
            c1b_verdict, c1b_note = "FAIL", "找到持久化形态但正文与输入不等（被改写）"
        else:
            c1b_verdict, c1b_note = "INCONCLUSIVE", "workspace 内未找到可核对的正文形态（存储形态不可字节核对）"
        rec.probe(
            "C1b",
            c1b_verdict,
            {
                "disk_full_hits": len(disk["full_content_hits"]),
                "disk_marker_hits": len(disk["marker_hits"]),
                "git_blobs": gitres["blobs"],
                "git_examined": gitres["examined"],
                "git_truncated": gitres["truncated"],
                "files_scanned": disk["files_scanned"],
            },
            c1b_note,
        )
        rec.probe(
            "C1c",
            "PASS" if c1.get("default_equal") else ("FAIL" if "default_sha256" in c1 else "INCONCLUSIVE"),
            {"default_sha256": c1.get("default_sha256"), "default_equal": c1.get("default_equal")},
            "默认读回接口若与输入不等，M1 的『原文读回一致』不成立",
        )

        # ------- 附加观测（非预注册 probe，故不改变 criteria_sha256）-------
        # 执行方在 M1 里声称"磁盘字节 / raw / download / 独立进程 ov read 四处逐字节相同"。
        # 预注册的 C1a/C1b 只覆盖了 raw 与磁盘两处；这里把另外两处也照实测出来，
        # 作为**描述性证据**附在结果里（判定仍只由预注册 probe 决定）。
        supp: Dict[str, Any] = {}
        try:
            import httpx

            dr = httpx.get(f"{E.BASE_URL}/api/v1/content/download",
                           params={"uri": entry_uri}, timeout=C.CALL_TIMEOUT_S, trust_env=False)
            supp["download"] = {
                "status": dr.status_code,
                "bytes": len(dr.content),
                "sha256": E.sha256_bytes(dr.content),
                "equal_to_input_bytes": dr.content == text.encode("utf-8"),
            }
        except Exception as exc:
            supp["download_error"] = _err(exc)
        try:
            supp["ov_version"] = run_ov(["--version"])
            supp["ov_read"] = run_ov(["read", entry_uri])
            out = (supp["ov_read"].get("stdout") or "")
            supp["ov_read"]["stdout_sha256"] = E.sha256_text(out)
            supp["ov_read"]["equal_to_input"] = out == text
            supp["ov_read"]["equal_after_strip"] = out.strip() == text.strip()
        except Exception as exc:
            supp["ov_read_error"] = _err(exc)
        obs["supplementary"] = supp

        # ---------------- C4 来源只读 ----------------
        src = E.VERIFY_ROOT / "source-tree"
        P.write_source_tree(src, marker)
        before = P.source_snapshot(src)
        obs["source_before"] = before

        status, body = P.http_post(
            "/api/v1/resources",
            {"path": str(src), "to": f"viking://resources/verify-{run_id}/rejected-local"},
        )
        obs["C4c"] = {"status": status, "body": body}
        c4c_ok = 400 <= status < 500
        rec.probe(
            "C4c", "PASS" if c4c_ok else "FAIL",
            {"status": status, "body": body[:600]},
            "本地文件系统路径作为来源的 HTTP 请求应被显式拒绝",
        )

        to_uri = f"viking://resources/verify-{run_id}/src"
        c2: Dict[str, Any] = {"to": to_uri, "source": str(src)}
        try:
            c2["add_result"] = consumer.add_resource(str(src), to=to_uri, wait=True)
        except Exception as exc:
            c2["add_error"] = _err(exc)
        try:
            c2["ls_imported"] = consumer.ls_simple(to_uri)
        except Exception as exc:
            c2["ls_error"] = _err(exc)
        try:
            c2["read_a_imported"] = consumer.read_raw(f"{to_uri}/a.md")
        except Exception as exc:
            c2["read_a_error"] = _err(exc)

        after_import = P.source_snapshot(src)
        diff = P.snapshot_diff(before, after_import)
        obs["source_after_import_diff"] = diff
        rec.probe(
            "C4a", "PASS" if diff["identical"] else "FAIL",
            {"added": diff["added"], "removed": diff["removed"], "changed": diff["changed"],
             "git_head_same": diff["git_head_same"], "git_status_same": diff["git_status_same"],
             "before_listing_sha256": before["listing_sha256"],
             "after_listing_sha256": after_import["listing_sha256"]},
            "接入前后源目录文件清单与 git 提交号必须一致",
        )

        # 第二份正文：workspace 内是否存在源文件对应的独立副本
        # 关键：用「源文件在磁盘上的真实字节」当针，而不是我生成的字符串（避免换行/编码口径造成假阴性）
        src_a_path = src / "a.md"
        src_a_bytes = src_a_path.read_bytes()
        disk_copy = P.scan_for_bytes(E.WORKSPACE, marker.encode("utf-8"), src_a_bytes)
        c2["source_a_bytes_sha256"] = E.sha256_bytes(src_a_bytes)
        c2["disk_copy_scan"] = disk_copy
        imported_a = c2.get("read_a_imported")
        if imported_a is not None:
            imported_bytes = imported_a.encode("utf-8")
            c2["imported_a_read_sha256"] = E.sha256_bytes(imported_bytes)
            c2["imported_a_equal_source_bytes"] = imported_bytes == src_a_bytes
            c2["imported_a_bytes"] = len(imported_bytes)
        independent_copy = bool(disk_copy["full_content_hits"])
        rec.probe(
            "C2b",
            "FAIL" if independent_copy else "INCONCLUSIVE",
            {"source_a": {"path": str(src_a_path), "sha256": E.sha256_bytes(src_a_bytes)},
             "workspace_full_content_hits": disk_copy["full_content_hits"],
             "marker_hits": [h["path"] for h in disk_copy["marker_hits"]]},
            "workspace 内出现源文件正文的独立副本即判『拷贝』",
        )

        # C2a：外部改源文件，按时限等待追平（比较锚 = 改动后源文件的真实字节）
        modified = P.make_modified_a(marker)
        mod_meta = P.replace_source_file(src_a_path, modified)
        obs["source_modified"] = mod_meta
        import subprocess

        subprocess.run(["git", "-C", str(src), "add", "-A"], capture_output=True, text=True)
        subprocess.run(["git", "-C", str(src), "commit", "-q", "-m", "external change v2"],
                       capture_output=True, text=True)
        expected_bytes = src_a_path.read_bytes()
        propagation: List[Dict[str, Any]] = []
        propagated = False
        deadline = time.time() + C.UPDATE_WINDOW_S
        while True:
            elapsed = int(time.time() - (deadline - C.UPDATE_WINDOW_S))
            try:
                got = consumer.read_raw(f"{to_uri}/a.md")
                got_bytes = got.encode("utf-8")
                equal = got_bytes == expected_bytes
                sample = {"t": elapsed, "equal_to_source_bytes": equal,
                          "read_sha256": E.sha256_bytes(got_bytes),
                          "read_bytes": len(got_bytes)}
            except Exception as exc:
                equal = False
                sample = {"t": elapsed, "error": _err(exc)}
            propagation.append(sample)
            if equal:
                propagated = True
                break
            if time.time() >= deadline:
                break
            time.sleep(C.UPDATE_POLL_S)
        c2["propagation"] = propagation
        c2["propagated_within_window"] = propagated
        c2["modified_sha256"] = E.sha256_text(modified)
        obs["C2"] = c2
        obs["C2_evidence"] = _write_evidence("C2_form.json", c2)
        rec.probe(
            "C2a", "PASS" if propagated else "FAIL",
            {"window_s": C.UPDATE_WINDOW_S, "propagated": propagated,
             "samples": propagation},
            "外部改源文件后应在上限内读到新正文（PASS=指过去；FAIL=拷贝/快照未追平）",
        )

        # C2c：同一源重复接入（用 parent 计数；先 mkdir，否则 add_resource 的 parent 不存在会直接失败）
        dup_parent = f"viking://resources/verify-{run_id}/dup-parent"
        dup: Dict[str, Any] = {"parent": dup_parent}
        try:
            dup["mkdir"] = consumer.mkdir(dup_parent)
        except Exception as exc:
            dup["mkdir_error"] = _err(exc)
        for i in (1, 2):
            try:
                dup[f"add_{i}"] = consumer.add_resource(str(src), parent=dup_parent, wait=True)
            except Exception as exc:
                dup[f"add_{i}_error"] = _err(exc)
            try:
                dup[f"ls_after_{i}"] = consumer.ls_simple(dup_parent)
            except Exception as exc:
                dup[f"ls_after_{i}_error"] = _err(exc)
        counts = [len(dup.get(f"ls_after_{i}") or []) for i in (1, 2)]
        dup["counts"] = counts
        adds_succeeded = sum(1 for i in (1, 2) if dup.get(f"add_{i}_error") is None)
        lss_ok = sum(1 for i in (1, 2) if dup.get(f"ls_after_{i}_error") is None)
        dup["adds_succeeded"] = adds_succeeded
        dup["ls_succeeded"] = lss_ok
        obs["C2c"] = dup
        if adds_succeeded == 0 or lss_ok == 0 or counts[0] == 0:
            # 两次接入都没成功（例如 parent 不存在）→ 这个 probe 什么都没测到，不得记 PASS
            c2c_verdict, c2c_note = "INCONCLUSIVE", "重复接入本身未成功（parent/mkdir/ls 失败）→ 无法裁决"
        else:
            c2c_verdict = "PASS" if counts[0] == counts[1] else "FAIL"
            c2c_note = "同一源重复接入不应新增第二条（条目数 %s → %s）" % (counts[0], counts[1])
        rec.probe(
            "C2c", c2c_verdict,
            {"count_after_first": counts[0], "count_after_second": counts[1],
             "adds_succeeded": adds_succeeded, "ls_succeeded": lss_ok,
             "ls_after_1": dup.get("ls_after_1"), "ls_after_2": dup.get("ls_after_2"),
             "mkdir_error": dup.get("mkdir_error"), "add_errors": [
                 dup.get("add_1_error"), dup.get("add_2_error")]},
            c2c_note,
        )

        # C4b：通过基座写该来源（基线必须取"写之前"的快照——此前我自己改过源文件）
        before_write = P.source_snapshot(src)
        c4b: Dict[str, Any] = {"target": f"{to_uri}/a.md", "baseline": before_write["listing_sha256"]}
        try:
            c4b["write_result"] = consumer.write(f"{to_uri}/a.md", "INJECTED-BY-VERIFY\n",
                                                 mode="replace", wait=True)
            c4b["accepted"] = True
        except Exception as exc:
            c4b["error"] = _err(exc)
            c4b["accepted"] = False
        after_write = P.source_snapshot(src)
        c4b["source_diff_vs_before_write"] = P.snapshot_diff(before_write, after_write)
        try:
            c4b["read_back"] = consumer.read_raw(f"{to_uri}/a.md")[:200]
        except Exception as exc:
            c4b["read_back_error"] = _err(exc)
        obs["C4b"] = c4b
        if not c4b["accepted"]:
            rec.probe("C4b", "PASS", {"rejected": c4b.get("error")},
                      "基座对来源路径的写被显式拒绝")
        elif not c4b["source_diff_vs_before_write"]["identical"]:
            rec.probe("C4b", "FAIL",
                      {"accepted": True,
                       "changed": c4b["source_diff_vs_before_write"]["changed"]},
                      "基座写请求改变了真实来源目录内容")
        else:
            rec.probe(
                "C4b", "INCONCLUSIVE",
                {"accepted": True, "read_back": c4b.get("read_back"),
                 "source_dir_unchanged": True,
                 "imported_copy_changed": (c4b.get("read_back") or "").strip()
                 == "INJECTED-BY-VERIFY"},
                "基座只持有导入副本：副本可写、且写不改动真实源目录 —— 物理上没有『写来源』这个动作，"
                "无法给出『写来源被拒』的证据（不得记 PASS）",
            )

        # ---------------- C3 重复提交 ----------------
        c3: Dict[str, Any] = {}
        idem_parent = f"viking://user/default/verify/{run_id}/idem"
        replace_uri = f"{idem_parent}/replace-twice.md"
        for i in (1, 2):
            try:
                c3[f"replace_write_{i}"] = consumer.write(replace_uri, text, mode="replace", wait=True)
            except Exception as exc:
                c3[f"replace_write_{i}_error"] = _err(exc)
        try:
            c3["ls_idem_parent"] = consumer.ls_simple(idem_parent)
        except Exception as exc:
            c3["ls_idem_parent_error"] = _err(exc)
        replace_count = sum(1 for x in (c3.get("ls_idem_parent") or []) if "replace-twice.md" in str(x))
        c3["replace_name_count"] = replace_count
        rec.probe(
            "C3a", "PASS" if replace_count == 1 else "FAIL",
            {"name_count": replace_count, "ls": c3.get("ls_idem_parent")},
            "同一 URI 同正文 write 两次（replace）后应只有 1 条",
        )

        create_uri = f"{idem_parent}/create-twice.md"
        c3["create_first"] = None
        c3["create_second_error"] = None
        try:
            c3["create_first"] = consumer.write(create_uri, text, mode="create", wait=True)
        except Exception as exc:
            c3["create_first_error"] = _err(exc)
        try:
            c3["create_second"] = consumer.write(create_uri, text, mode="create", wait=True)
        except Exception as exc:
            c3["create_second_error"] = _err(exc)
        try:
            c3["ls_idem_parent_2"] = consumer.ls_simple(idem_parent)
        except Exception as exc:
            c3["ls_idem_parent_2_error"] = _err(exc)
        create_count = sum(1 for x in (c3.get("ls_idem_parent_2") or []) if "create-twice.md" in str(x))
        c3["create_name_count"] = create_count
        second_rejected = c3["create_second_error"] is not None
        rec.probe(
            "C3b", "PASS" if create_count == 1 and (second_rejected or c3.get("create_second") is not None) else "FAIL",
            {"name_count": create_count, "second_rejected": second_rejected,
             "second_error": c3.get("create_second_error"),
             "ls": c3.get("ls_idem_parent_2")},
            "create 重复提交必须幂等或显式冲突，不得产生第二条",
        )

        c3_to = f"viking://resources/verify-{run_id}/idem-src"
        c3["to"] = c3_to
        for i in (1, 2):
            try:
                c3[f"to_add_{i}"] = consumer.add_resource(str(src), to=c3_to, wait=True)
            except Exception as exc:
                c3[f"to_add_{i}_error"] = _err(exc)
        try:
            c3["ls_to_parent"] = consumer.ls_simple(f"viking://resources/verify-{run_id}")
        except Exception as exc:
            c3["ls_to_parent_error"] = _err(exc)
        to_entries = sum(1 for x in (c3.get("ls_to_parent") or []) if "idem-src" in str(x))
        c3["to_entry_count"] = to_entries
        rec.probe(
            "C3c", "PASS" if to_entries <= 1 else "FAIL",
            {"entry_count": to_entries, "ls": c3.get("ls_to_parent"),
             "second_error": c3.get("to_add_2_error")},
            "同一源 + 同一显式 to 重复提交不得新增条目",
        )

        status_rid, body_rid = P.http_post(
            "/api/v1/content/write",
            {
                "uri": f"{idem_parent}/request-id.md",
                "content": text,
                "mode": "create",
                "request_id": f"verify-{run_id}",
            },
        )
        c3["request_id_probe"] = {"status": status_rid, "body": body_rid[:600]}
        rec.probe(
            "C3d",
            "INCONCLUSIVE" if 200 <= status_rid < 300 else "FAIL",
            c3["request_id_probe"],
            "公开写入接口无 request_id 幂等键（携带该字段被 extra=forbid 拒绝）→ 无法按 request_id 判重",
        )
        obs["C3"] = c3
        obs["C3_evidence"] = _write_evidence("C3_duplicate.json", c3)

        # ---------------- C5 停机报错 ----------------
        c5: Dict[str, Any] = {}
        try:
            c5["online_read_ok"] = consumer.read_raw(entry_uri) == text
        except Exception as exc:
            c5["online_read_error"] = _err(exc)
        rec.probe(
            "C5a", "PASS" if c5.get("online_read_ok") else "FAIL",
            {"online_read_ok": c5.get("online_read_ok")}, "在线对照必须成功",
        )

        # 错误可区分性（在线时先取 NotFound）
        try:
            consumer.read_raw(f"viking://user/default/verify/{run_id}/definitely-missing-{run_id}.md")
            c5["missing"] = {"returned": "no-exception"}
        except Exception as exc:
            c5["missing"] = _err(exc)
        obs["C5"] = c5

        stop = E.stop_server(started.get("pid"), started.get("listener_pid"))
        obs["stop_for_C5"] = stop
        down: Dict[str, Any] = {"stop": stop}
        try:
            value = consumer.read_raw(entry_uri)
            down["read_returned"] = repr(value)[:300]
            down["read_raised"] = False
        except Exception as exc:
            down["read_raised"] = True
            down["read_error"] = _err(exc)
        try:
            value = consumer.find(marker, limit=5)
            down["find_returned"] = json.dumps(value, ensure_ascii=False)[:300]
            down["find_raised"] = False
        except Exception as exc:
            down["find_raised"] = True
            down["find_error"] = _err(exc)
        c5["down"] = down
        obs["C5"] = c5
        obs["C5_evidence"] = _write_evidence("C5_down.json", c5)

        stop_failed = not bool(stop.get("stopped"))
        rec.probe(
            "C5b",
            "INCONCLUSIVE" if stop_failed else ("PASS" if down.get("read_raised") else "FAIL"),
            {"stop": stop, "raised": down.get("read_raised"), "error": down.get("read_error"),
             "returned": down.get("read_returned")},
            "停机后读取必须抛显式错误，不能返回空结果"
            + ("（停机未成功 → 无法裁决）" if stop_failed else ""),
        )
        rec.probe(
            "C5c",
            "INCONCLUSIVE" if stop_failed else ("PASS" if down.get("find_raised") else "FAIL"),
            {"stop": stop, "raised": down.get("find_raised"), "error": down.get("find_error"),
             "returned": down.get("find_returned")},
            "停机后检索必须抛显式错误，不能返回空命中"
            + ("（停机未成功 → 无法裁决）" if stop_failed else ""),
        )
        taxonomy = {
            "not_found": c5.get("missing"),
            "unavailable": down.get("read_error"),
            "permission_denied": obs.get("C4c"),
        }
        classes = {
            "not_found": (c5.get("missing") or {}).get("exception"),
            "unavailable": (down.get("read_error") or {}).get("exception"),
        }
        # 按冻结规则原文执行：必须**同时**拿到"无匹配"与"服务不可用"两档且类型不同；
        # "拒绝访问"一档在本实例（dev/ROOT）无法构造 → 该档标为无法核实，但不得因此放宽前两档。
        both_present = all(classes.values())
        distinct = len(set(classes.values())) == 2 if both_present else False
        c5d_observed = {"distinct": distinct, "classes": classes,
                        "permission_denied_unreachable_with_dev_identity": True,
                        "permission_denied_evidence": obs.get("C4c")}
        rec.probe(
            "C5d",
            "PASS" if (both_present and distinct) else "INCONCLUSIVE",
            c5d_observed,
            "需要『无匹配』与『服务不可用』两档同时存在且类型不同；"
            "『拒绝访问』一档需非 ROOT 身份，本实例无法构造（如实标为无法核实）"
            + ("" if both_present else "；本次 unavailable 档缺失 → 无法裁决"),
        )

        # 重启后读回（C1d）
        restart = E.start_server()
        obs["restart"] = restart
        started = restart  # 最终停机必须按"当前"实例的 PID，否则端口所有者校验会拒绝停
        obs["restart_ready"] = wait_ready()
        if not restart.get("started"):
            # 实例没真正重启（旧进程仍在 / 启动被拒）→ 不把"旧进程仍可读"误记成重启后仍可读
            obs["C1d"] = {"equal": False, "error": "restart_not_started",
                          "reason": restart.get("reason"), "restart": restart}
            rec.probe("C1d", "INCONCLUSIVE", obs["C1d"],
                      "隔离实例未真正重启（旧进程仍在或启动被拒）→ 无法核 C1d")
        elif not obs["restart_ready"].get("ready"):
            obs["C1d"] = {"equal": False, "error": "restart_not_ready"}
            rec.probe("C1d", "INCONCLUSIVE", obs["C1d"], "重启后未就绪 → 无法核 C1d")
        else:
            try:
                again = consumer.read_raw(entry_uri)
                obs["C1d"] = {"equal": again == text, "sha256": E.sha256_text(again)}
            except Exception as exc:
                obs["C1d"] = {"equal": False, "error": _err(exc)}
            rec.probe(
                "C1d", "PASS" if obs["C1d"]["equal"] else "FAIL",
                obs["C1d"], "重启后原文仍可读回同一内容",
            )
    finally:
        import contextlib

        with contextlib.suppress(Exception):
            consumer.close()

    # 清理：停掉本线实例（按命令行所有权校验，launcher 与监听进程一起停）
    obs["final_stop"] = E.stop_server(started.get("pid"), started.get("listener_pid"))
    return _finalize(rec, obs)


def _finalize(rec: Recorder, obs: Dict[str, Any]) -> Dict[str, Any]:
    results: Dict[str, Any] = {}
    for check in C.CHECKS:
        cid = check["id"]
        v = rec.verdicts(cid)
        expected = [p["id"] for p in check["probes"]]
        if not all(p in v for p in expected):
            results[cid] = {"verdict": "INCONCLUSIVE", "probes": rec.probes,
                            "note": f"未全部执行: {[p for p in expected if p not in v]}"}
            continue
        results[cid] = {"verdict": C.aggregate(cid, v), "title": check["title"],
                        "probes": {p: rec.probes[p] for p in expected}}
    payload = {
        "stage": "live",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "criteria_version": C.CRITERIA_VERSION,
        "criteria_sha256": C.criteria_sha256(),
        "criteria_file_sha256": E.sha256_bytes(
            (E.VERIFY_MODULE_DIR / "verify_criteria.py").read_bytes()
        ),
        "harness_sha256": {
            name: E.sha256_bytes((E.VERIFY_MODULE_DIR / name).read_bytes())
            for name in ("verify_criteria.py", "verify_env.py", "verify_probe.py",
                         "verify_checks.py", "run_verify.py")
            if (E.VERIFY_MODULE_DIR / name).exists()
        },
        "isolation": C.ISOLATION,
        "results": results,
        "observation": obs,
    }
    payload["payload_sha256"] = E.sha256_text(
        json.dumps({k: v for k, v in payload.items() if k != "payload_sha256"},
                   ensure_ascii=False, sort_keys=True)
    )
    return payload


def render_markdown(payload: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# 独立复核（自测）结果")
    lines.append("")
    lines.append(f"- 阶段：`{payload.get('stage')}`")
    lines.append(f"- 生成时间：{payload.get('generated_at')}")
    lines.append(f"- 判定标准指纹（跑前预注册）：`{payload.get('criteria_sha256')}`")
    lines.append(f"- 结果载荷指纹：`{payload.get('payload_sha256')}`")
    lines.append(f"- 隔离实例：{C.ISOLATION['service']} @ {E.HOST}:{E.PORT}")
    lines.append("")
    if payload.get("stage") == "live":
        lines.append("| 检查 | 判定 | 说明 |")
        lines.append("|---|---|---|")
        for cid, r in payload["results"].items():
            lines.append(f"| {cid} | **{r['verdict']}** | {r.get('title', r.get('note', ''))} |")
        lines.append("")
        for cid, r in payload["results"].items():
            lines.append(f"## {cid} — {r.get('title', '')} → {r['verdict']}")
            if "probes" in r:
                for pid, pr in r["probes"].items():
                    lines.append(f"- **{pid}**: {pr['verdict']} — {pr.get('note', '')}")
                    lines.append(f"  - 观测：`{json.dumps(pr['observed'], ensure_ascii=False)[:600]}`")
            elif r.get("note"):
                lines.append(f"- {r['note']}")
            lines.append("")
        supp = (payload.get("observation") or {}).get("supplementary")
        if supp:
            lines.append("## 附加观测（非预注册 probe；只作描述性证据，不参与判定）")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(supp, ensure_ascii=False, indent=2)[:4000])
            lines.append("```")
            lines.append("")
    else:
        lines.append(f"- 判定：**{payload.get('verdict')}**")
        lines.append("")
        lines.append("| 静态自检项 | 通过 |")
        lines.append("|---|---|")
        for c in payload.get("checks", []):
            lines.append(f"| {c['check']} | {'✅' if c['ok'] else '❌'} |")
        if payload.get("problems"):
            lines.append("")
            lines.append(f"**问题**：{payload['problems']}")
    return "\n".join(lines) + "\n"


__all__ = ["run_static", "run_preflight", "run_live", "run_cli_read_modes",
           "render_markdown", "wait_ready"]
