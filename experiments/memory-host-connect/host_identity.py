#!/usr/bin/env python
r"""host_identity.py -- M6 supplement: provision two identities + a restricted subtree on 8782.

Runs against THIS line's instance only. Needs the authz profile (auth_mode=api_key), so the root
key must be in the process environment (``OPENVIKING_MVP_HOST_ROOT_KEY``); that place is the only
place it exists -- this script never writes it anywhere and only ever prints key *prefixes*.

What it does, in order:
  1. registers ``host-rw`` (role=admin) and ``host-ro`` (role=user) in account ``default``;
  2. turns ON account ACL enforcement (default is OFF -- without this, restricted/grant do nothing);
  3. marks ``viking://resources/m6-scope`` **restricted** and grants ``host-ro`` read /
     ``host-rw`` write;
  4. seeds one file inside that subtree **as the writer**;
  5. stores both user keys into DeepTutor's own per-owner secret store under the authz home, so
     mcp.json can hold ``${secret:openviking-mvp/api_key_ro|api_key_rw}`` instead of a key.

Usage (one shell, so the env root key is shared with the server start):
    $env:OPENVIKING_MVP_HOST_ROOT_KEY = "..."
    python host_identity.py --home <iso>\hosts\deeptutor-home-authz
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TRY = "utf-8"
ACCOUNT = "default"
WRITER = "host-rw"
READER = "host-ro"
SCOPE_ROOT = "viking://resources/m6-scope"
SEED_URI = f"{SCOPE_ROOT}/seed.md"
SERVER_NAME = "openviking-mvp"
ROOT_KEY_ENV = "OPENVIKING_MVP_HOST_ROOT_KEY"


def api(method: str, path: str, *, body: dict | None = None, params: dict | None = None,
        key: str | None, timeout: float = 120.0) -> dict:
    base = os.environ.get("HOST_BASE_URL", "http://127.0.0.1:8782")
    url = base + path
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    data = json.dumps(body, ensure_ascii=False).encode(TRY) if body is not None else None
    hdrs = {"Accept": "application/json"}
    if data is not None:
        hdrs["Content-Type"] = "application/json; charset=utf-8"
    if key:
        hdrs["X-API-Key"] = key
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"status": resp.status, "body": json.loads(resp.read().decode(TRY) or "null")}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "error": exc.read().decode(TRY, "replace")[:400]}
    except Exception as exc:
        return {"status": 0, "error": f"{type(exc).__name__}: {exc}"}


def prefix(value: str) -> str:
    return (value[:6] + "...") if value else "(none)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True, help="isolated authz DEEPTUTOR_HOME (secret store target)")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    home = Path(args.home).expanduser().resolve()
    os.environ["DEEPTUTOR_HOME"] = str(home)

    root_key = os.environ.get(ROOT_KEY_ENV) or ""
    if not root_key:
        print(f"[FAIL] {ROOT_KEY_ENV} not set -- start the authz server from a shell that has it", file=sys.stdout)
        return 2

    out: dict = {"base_url": os.environ.get("HOST_BASE_URL", "http://127.0.0.1:8782"),
                 "account": ACCOUNT, "scope_root": SCOPE_ROOT, "seed_uri": SEED_URI, "steps": []}

    def step(name: str, ok: bool, detail) -> None:
        out["steps"].append({"step": name, "ok": ok, "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    # --- authn mode fact (read from the server, not assumed) -------------------
    health = api("GET", "/health", key=None, timeout=10)
    mode = ""
    if health.get("status") == 200 and isinstance(health.get("body"), dict):
        mode = str(health["body"].get("auth_mode", ""))
    out["auth_mode"] = mode
    step("server_auth_mode", mode == "api_key", f"auth_mode={mode!r} (need 'api_key' for身份/ACL)")

    # --- 1. two identities -----------------------------------------------------
    keys: dict[str, str] = {}
    for user_id, role in ((WRITER, "admin"), (READER, "user")):
        r = api("POST", f"/api/v1/admin/accounts/{ACCOUNT}/users",
                body={"user_id": user_id, "role": role, "seed": f"m6host-{user_id}"}, key=root_key)
        key = ""
        if r.get("status") in (200, 201) and isinstance(r.get("body"), dict):
            key = (r["body"].get("result") or {}).get("user_key") or ""
        if not key:
            listing = api("GET", f"/api/v1/admin/accounts/{ACCOUNT}/users", key=root_key)
            users = (listing.get("body") or {}).get("result") or []
            key = next((u.get("api_key", "") for u in users if u.get("user_id") == user_id), "")
        keys[user_id] = key
        step(f"identity_{user_id}", bool(key), {"role": role, "key_prefix": prefix(key),
                                                "register_status": r.get("status"),
                                                "register_error": r.get("error", "")[:200]})
    if not all(keys.values()):
        _dump(out, args.json_out)
        return 3

    # --- 2. account ACL switch (default OFF) -----------------------------------
    st = api("PATCH", f"/api/v1/admin/accounts/{ACCOUNT}/settings",
             body={"acl": {"enabled": True}}, key=root_key)
    check = api("GET", f"/api/v1/admin/accounts/{ACCOUNT}/settings", key=root_key)
    enabled = bool((((check.get("body") or {}).get("result") or {})
                    .get("settings", {}) or {}).get("acl", {}).get("enabled"))
    step("account_acl_enabled", enabled, {"patch_status": st.get("status"), "acl_enabled": enabled})

    # --- 3. seed one file inside the subtree, as the writer -------------------
    # NOTE: order matters -- the ACL endpoints 404 while the resource does not exist yet
    # (measured 2026-10-10: set_mode/grant before the first write => 404 NOT_FOUND resource).
    seed = api("POST", "/api/v1/content/write",
               body={"uri": SEED_URI,
                     "content": "M6-SUPPLEMENT seed written by host-rw\n",
                     "mode": "replace", "wait": True},
               key=keys[WRITER])
    step("seed_written_by_writer", seed.get("status") == 200,
         {"status": seed.get("status"), "uri": SEED_URI, "error": seed.get("error", "")[:200]})

    # --- 4. restricted subtree + grants ---------------------------------------
    w = api("PUT", "/api/v1/acl", body={"uri": SCOPE_ROOT, "acl_mode": "restricted"}, key=keys[WRITER])
    g_ro = api("POST", "/api/v1/acl/grant",
               body={"uri": SCOPE_ROOT, "principal": f"user:{READER}", "level": "read"},
               key=keys[WRITER])
    g_rw = api("POST", "/api/v1/acl/grant",
               body={"uri": SCOPE_ROOT, "principal": f"user:{WRITER}", "level": "write"},
               key=keys[WRITER])
    acl = api("GET", "/api/v1/acl", params={"uri": SCOPE_ROOT}, key=keys[WRITER])
    step("restricted_subtree", w.get("status") in (200, 201),
         {"set_mode_status": w.get("status"), "grant_ro": g_ro.get("status"),
          "grant_rw": g_rw.get("status"),
          "acl": (acl.get("body") or {}).get("result") or acl.get("error", "")})

    # --- 5. store both keys in DeepTutor's own secret store -------------------
    try:
        from deeptutor.services.mcp.secrets import store_secrets
        from deeptutor.services.mcp.manager import SHARED_OWNER
        store_secrets(SHARED_OWNER, SERVER_NAME,
                      {"api_key_ro": keys[READER], "api_key_rw": keys[WRITER]})
        step("secrets_stored_in_deeptutor", True,
             {"owner": SHARED_OWNER, "server": SERVER_NAME,
              "fields": ["api_key_ro", "api_key_rw"],
              "note": "values live in DeepTutor's secret store, not in mcp.json/evidence"})
    except Exception as exc:
        step("secrets_stored_in_deeptutor", False, f"{type(exc).__name__}: {exc}")
        _dump(out, args.json_out)
        return 4

    out["principals"] = {"writer": WRITER, "reader": READER,
                         "key_prefixes": {WRITER: prefix(keys[WRITER]), READER: prefix(keys[READER])}}
    _dump(out, args.json_out)
    return 0


def _dump(out: dict, json_out: str | None) -> None:
    text = json.dumps(out, ensure_ascii=False, indent=2)
    print(text)
    if json_out:
        Path(json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(json_out).write_text(text + "\n", encoding="utf-8")
        print(f"[evidence] {json_out}")


if __name__ == "__main__":
    raise SystemExit(main())
