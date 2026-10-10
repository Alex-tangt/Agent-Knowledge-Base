# -*- coding: utf-8 -*-
"""独立复核：授权面（执行方 M2.6 / M3 的核心声明）。

执行方声明（本次要独立核对，**不复用其脚本**）：
  a) 账户级 ACL **默认关闭**（`acl.enabled` 默认 false）；
  b) 不打开时，只读身份（role=user + ACL read）**照样能写**；
  c) 打开后，同一身份同一操作变成 403 `PERMISSION_DENIED`。

我方的做法：自己起一个 **api_key 模式** 的隔离实例（8783，数据仍在仓外），
自建账户与两名身份（admin / 只读 user），自建资源与 ACL，逐一实测。
root key 每次运行随机生成，只写进仓外隔离 conf，**绝不进证据**。
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

import verify_criteria as C
import verify_env as E
import verify_probe as P
from verify_checks import _err, wait_ready

AUTHZ_CONF = E.VERIFY_ROOT / "ov-authz.conf"


def _client(api_key: Optional[str], user: Optional[str], account: str):
    from openviking_sdk import SyncHTTPClient

    c = SyncHTTPClient(
        url=E.BASE_URL, api_key=api_key, user=user, account=account, timeout=C.CALL_TIMEOUT_S
    )
    c.initialize()
    return c


def _redact(obj: Any) -> Any:
    """证据里绝不出现凭据值。"""
    if isinstance(obj, dict):
        return {
            k: ("<redacted>" if "key" in k.lower() or "token" in k.lower() else _redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def _call(fn, *args, **kwargs) -> Dict[str, Any]:
    """调用并把结果**脱敏后**返回（用于证据）。

    注意：脱敏会破坏凭据值，所以**不能**用它取 user_key —— 需要凭据的调用请用 `_call_raw`
    再把脱敏副本写进证据（曾因此拿到字面量 "<redacted>" 当 API key 用，见 README 工具缺陷#6）。
    """
    try:
        return {"ok": True, "result": _redact(fn(*args, **kwargs))}
    except Exception as exc:
        return {"ok": False, "error": _err(exc)}


def _call_raw(fn, *args, **kwargs) -> Dict[str, Any]:
    """调用并保留原值（仅用于在内存里取凭据；写证据前必须自行脱敏）。"""
    try:
        return {"ok": True, "result": fn(*args, **kwargs)}
    except Exception as exc:
        return {"ok": False, "error": _err(exc)}


def run_acl() -> Dict[str, Any]:
    run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)
    root_key = secrets.token_urlsafe(32)  # 只驻留内存 + 仓外隔离 conf
    # 每次运行用**全新账户**：ACL 开关是账户级持久设置，复用账户会把上一次 run 的
    # `acl.enabled=true` 带进来，导致"默认值"根本测不到（我第一版就踩了这个）。
    account = "mvpverify" + secrets.token_hex(3)
    admin_user = "vadmin"
    ro_user = "vro"
    scope = f"viking://resources/{account}"
    doc_uri = f"{scope}/doc.md"
    body = f"# ACL 独立复核 {run_id}\n\n原文正文，用于只读身份的写尝试。\n"

    obs: Dict[str, Any] = {
        "run_id": run_id,
        "profile": "api_key（root_api_key 随运行随机生成，仅写仓外隔离 conf，证据不含值）",
        "account": account,
        "identities": {"admin": f"user:{admin_user} (role=admin)", "readonly": f"user:{ro_user} (role=user)"},
        "scope": scope,
        "doc_uri": doc_uri,
        "input_bytes": len(body.encode("utf-8")),
        "input_sha256": E.sha256_text(body),
    }
    obs["static_default"] = _static_acl_default()
    claims: List[Dict[str, Any]] = []

    def claim(cid: str, statement: str, verdict: str, detail: Any) -> None:
        claims.append({"claim": cid, "statement": statement, "verdict": verdict, "detail": detail})

    E.ensure_dirs()
    conf = E.write_authz_conf(AUTHZ_CONF, root_key)
    obs["authz_conf"] = {"path": str(conf), "sha256": E.sha256_bytes(conf.read_bytes()),
                         "bom": E.has_bom(conf)}
    started = E.start_server(conf_path=conf)
    obs["start"] = started
    if not started.get("started"):
        obs["fatal"] = "隔离实例未启动"
        obs["claims"] = claims
        return _finish(obs)

    ready = wait_ready()
    obs["ready"] = ready
    if not ready.get("ready"):
        obs["fatal"] = "隔离实例未就绪"
        obs["stop"] = E.stop_server(started.get("pid"), started.get("listener_pid"), conf)
        obs["claims"] = claims
        return _finish(obs)

    # 进程内也钉到本线 conf（避免任何真实用户配置被读到），并净化代理环境变量
    obs["proxy_env_sanitized"] = E.pin_process_env(conf)

    root_c = None
    try:
        root_c = _client(root_key, admin_user, account)
        # 1) 建账户 + 首个 admin（凭据取原值，证据只存脱敏副本）
        cr = _call_raw(root_c.admin_create_account, account, admin_user)
        obs["create_account"] = _redact(cr)
        admin_key = ((cr.get("result") or {}).get("user_key") if cr.get("ok") else None)
        if not admin_key:
            # 账户可能已存在（上一次运行残留）：重新签发 key
            obs["create_account_note"] = "未拿到 user_key，尝试重新签发 admin key"
            rk = _call_raw(root_c.admin_regenerate_key, account, admin_user)
            obs["admin_regenerate_key"] = _redact(rk)
            admin_key = ((rk.get("result") or {}).get("user_key") if rk.get("ok") else None)
        if not admin_key:
            obs["fatal"] = "拿不到 admin 凭据 → 无法构造非 ROOT 身份"
            obs["claims"] = claims
            return _finish(obs, root_c=root_c, started=started, conf=conf)

        admin_c = _client(admin_key, admin_user, account)
        # 2) 注册只读用户 + 拿 key
        rr = _call_raw(admin_c.admin_register_user, account, ro_user, "user")
        obs["register_readonly_user"] = _redact(rr)
        ro_key = ((rr.get("result") or {}).get("user_key") if rr.get("ok") else None)
        if not ro_key:
            rk = _call_raw(admin_c.admin_regenerate_key, account, ro_user)
            obs["readonly_regenerate_key"] = _redact(rk)
            ro_key = ((rk.get("result") or {}).get("user_key") if rk.get("ok") else None)
        if not ro_key:
            obs["fatal"] = "拿不到只读身份凭据"
            obs["claims"] = claims
            return _finish(obs, root_c=root_c, started=started, conf=conf)

        # 3) admin 写正文 + 设 restricted + 给只读/管理身份授权
        obs["admin_write"] = _call(admin_c.write, doc_uri, body, "create", True)
        obs["acl_set_restricted"] = _call(admin_c.acl_set, scope, None, acl_mode="restricted")
        obs["acl_grant_read"] = _call(admin_c.acl_grant, scope, f"user:{ro_user}", "read")
        obs["acl_grant_write_admin"] = _call(admin_c.acl_grant, scope, f"user:{admin_user}", "write")
        obs["acl_get"] = _call(admin_c.acl_get, scope)

        # 未授权的兄弟子树（同样 restricted，只给 admin 写权限）→ 只读身份读它必须被拒
        outside_scope = f"viking://resources/{account}-outside"
        outside_doc = f"{outside_scope}/secret.md"
        obs["admin_write_outside"] = _call(admin_c.write, outside_doc, "越界内容\n", "create", True)
        obs["acl_set_restricted_outside"] = _call(
            admin_c.acl_set, outside_scope, None, acl_mode="restricted"
        )
        obs["acl_grant_write_outside"] = _call(
            admin_c.acl_grant, outside_scope, f"user:{admin_user}", "write"
        )

        # 4) 只读身份在 ACL 关闭时的可读/可写
        ro_c = _client(ro_key, ro_user, account)
        settings_before = _call(admin_c.admin_get_account_settings, account)
        obs["settings_before"] = settings_before
        acl_default = _extract_acl_enabled(settings_before)
        claim(
            "ACL-a-default-off",
            "账户级 ACL 默认关闭（acl.enabled 默认 false）",
            "PASS" if acl_default is False else ("FAIL" if acl_default is True else "INCONCLUSIVE"),
            {"acl_enabled_observed": acl_default,
             "static_source": obs.get("static_default")},
        )

        ro_read = _call(ro_c.read_raw, doc_uri)
        obs["readonly_read"] = ro_read
        ro_write_off = _call(ro_c.write, doc_uri, body + "\n只读身份的写入尝试（ACL 关闭时）\n",
                             "replace", True)
        obs["readonly_write_acl_off"] = ro_write_off
        write_off_ok = bool(ro_write_off.get("ok"))
        claim(
            "ACL-b-off-means-ro-can-write",
            "ACL 未打开时，只读身份（role=user + ACL read）仍能写入",
            "PASS" if write_off_ok else ("FAIL" if ro_write_off.get("error") else "INCONCLUSIVE"),
            {"write_accepted_with_acl_off": write_off_ok,
             "error": ro_write_off.get("error"),
             "readonly_read_ok": ro_read.get("ok")},
        )

        # 5) 打开 ACL，同一身份同一操作
        patch = _http_patch_admin(admin_key, account, {"acl": {"enabled": True}})
        obs["patch_acl_enabled"] = patch
        obs["settings_after"] = _call(admin_c.admin_get_account_settings, account)
        ro_write_on = _call(ro_c.write, doc_uri, body + "\n只读身份的写入尝试（ACL 打开后）\n",
                            "replace", True)
        obs["readonly_write_acl_on"] = ro_write_on
        ro_read_on = _call(ro_c.read_raw, doc_uri)
        obs["readonly_read_acl_on"] = ro_read_on
        admin_write_after = _call(admin_c.write, f"{scope}/admin-ok.md", "admin 仍应能写\n",
                                  "create", True)
        obs["admin_write_after_acl_on"] = admin_write_after

        code = _error_code(ro_write_on.get("error"))
        denied = (not ro_write_on.get("ok")) and code == "PERMISSION_DENIED"
        claim(
            "ACL-c-on-means-403",
            "打开 ACL 后，同一身份同一操作被拒（403 PERMISSION_DENIED）",
            "PASS" if denied else ("FAIL" if ro_write_on.get("ok") else "INCONCLUSIVE"),
            {"write_rejected_with_acl_on": not ro_write_on.get("ok"),
             "error_code": code, "error": ro_write_on.get("error"),
             "readonly_read_still_ok": ro_read_on.get("ok"),
             "admin_write_still_ok": admin_write_after.get("ok")},
        )

        # 6) 越权读取也不得泄露（只读身份读未授权 restricted 子树）
        obs["readonly_read_outside"] = _call(ro_c.read_raw, outside_doc)
        obs["readonly_ls_outside"] = _call(ro_c.ls, outside_scope, simple=True, show_all_hidden=True)
        claim(
            "ACL-d-outside-denied",
            "范围外（未授权 restricted 子树）的直接读取被拒且不泄露正文",
            "PASS" if not obs["readonly_read_outside"].get("ok") else "FAIL",
            {"read": obs["readonly_read_outside"], "ls": obs["readonly_ls_outside"]},
        )

        # 附：只读身份写「受限范围」被拒这一条，正好补上 C4b 缺的非 ROOT 身份证据
        claim(
            "ACL-e-ro-write-scope",
            "（补 C4b）非 ROOT 身份通过基座写受限范围被显式拒绝",
            "PASS" if denied else "INCONCLUSIVE",
            {"target": doc_uri, "error_code": code},
        )
    except Exception as exc:  # 任何未预期异常也必须停服务并落证据，不留孤儿
        obs["fatal"] = f"{type(exc).__name__}: {exc}"
        obs["claims"] = claims
        return _finish(obs, started=started, conf=conf)
    finally:
        import contextlib

        if root_c is not None:
            with contextlib.suppress(Exception):
                root_c.close()

    obs["claims"] = claims
    return _finish(obs, started=started, conf=conf)


def _static_acl_default() -> Dict[str, Any]:
    """框架源码级证据：账户 ACL 开关的默认值（可复算）。"""
    try:
        p = E.VENV_OV.parent / "Lib" / "site-packages" / "openviking" / "config" / "account_config.py"
        text = p.read_text(encoding="utf-8")
        idx = text.find("class AccountAclSettings")
        segment = text[idx: idx + 400] if idx >= 0 else ""
        return {
            "file": str(p),
            "file_sha256": E.sha256_bytes(p.read_bytes()),
            "enabled_default_false": ("enabled: bool = RuntimeField(default=False)" in segment),
            "segment": segment.strip()[:300],
        }
    except Exception as exc:
        return {"error": _err(exc)}


def _finish(obs: Dict[str, Any], started: Optional[Dict[str, Any]] = None,
            conf: Optional[Path] = None, root_c: Any = None) -> Dict[str, Any]:
    if root_c is not None:
        import contextlib

        with contextlib.suppress(Exception):
            root_c.close()
    if started and started.get("started"):
        obs["stop"] = E.stop_server(started.get("pid"), started.get("listener_pid"), conf)
    payload = {
        "stage": "acl",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "criteria_sha256": C.criteria_sha256(),
        "observation": obs,
    }
    payload["payload_sha256"] = E.sha256_text(
        json.dumps({k: v for k, v in payload.items() if k != "payload_sha256"},
                   ensure_ascii=False, sort_keys=True, default=str)
    )
    return payload


def _extract_acl_enabled(settings: Dict[str, Any]) -> Optional[bool]:
    """从 account settings 响应里挖出 acl.enabled（形状随版本而异，故递归找）。"""
    def walk(node: Any) -> Optional[bool]:
        if isinstance(node, dict):
            if node.get("acl") and isinstance(node["acl"], dict) and "enabled" in node["acl"]:
                return bool(node["acl"]["enabled"])
            for v in node.values():
                got = walk(v)
                if got is not None:
                    return got
        return None

    return walk(settings)


def _http_patch_admin(admin_key: str, account: str, body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        r = httpx.patch(
            f"{E.BASE_URL}/api/v1/admin/accounts/{account}/settings",
            json=body,
            headers={"X-API-Key": admin_key},
            timeout=C.CALL_TIMEOUT_S,
            trust_env=False,
        )
        return {"status": r.status_code, "body": _redact(r.text[:800])}
    except Exception as exc:
        return {"error": _err(exc)}


def _error_code(err: Optional[Dict[str, Any]]) -> Optional[str]:
    if not err:
        return None
    msg = err.get("message") or ""
    if "PERMISSION_DENIED" in msg:
        return "PERMISSION_DENIED"
    if err.get("exception") == "PermissionDeniedError":
        return "PERMISSION_DENIED"
    return err.get("exception")


def render_markdown(payload: Dict[str, Any]) -> str:
    o = payload["observation"]
    lines = ["# 独立复核：授权面（执行方 M2.6 / M3 声明）", ""]
    lines.append(f"- 生成时间：{payload['generated_at']}")
    lines.append(f"- 运行：`{o['run_id']}` · profile `{o['profile']}` · 账户 `{o['account']}`")
    lines.append(f"- 判定标准指纹：`{payload['criteria_sha256']}`")
    lines.append(f"- 载荷指纹：`{payload['payload_sha256']}`")
    lines.append("")
    lines.append("| 声明 | 判定 |")
    lines.append("|---|---|")
    for c in o.get("claims", []):
        lines.append(f"| {c['claim']} | **{c['verdict']}** |")
    lines.append("")
    for c in o.get("claims", []):
        lines.append(f"## {c['claim']} → {c['verdict']}")
        lines.append(f"- 声明：{c['statement']}")
        lines.append(f"- 观测：`{json.dumps(c['detail'], ensure_ascii=False)[:1200]}`")
        lines.append("")
    if o.get("fatal"):
        lines.append(f"**致命**：{o['fatal']}")
    lines.append("")
    lines.append("（原始 JSON 见 `acl_independent.json`；凭据值已全部脱敏。）")
    return "\n".join(lines) + "\n"


__all__ = ["run_acl", "render_markdown"]
