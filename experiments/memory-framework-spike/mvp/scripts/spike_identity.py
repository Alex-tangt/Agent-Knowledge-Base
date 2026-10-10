"""Two authorized consumers for the isolated spike instance (api_key profile).

Creates/uses exactly two identities and never prints or commits their keys:

  * ``mem-rw``  role=admin  -- the writable consumer (also imports the source)
  * ``mem-ro``  role=user   -- the read-only consumer

Key material is kept in ``%USERPROFILE%\\.openviking-mvp-spike\\identities.json``
(outside the repo); evidence records only the variable/principal names.

Why this exists: the server's **dev** auth mode always resolves ROLE=root, and
ROLE=root bypasses ACL entirely, so dev mode cannot express "read-only identity".
``auth_mode=api_key`` (ov-authz.conf, same host/port/workspace) can.  A second
server-side switch is also required: **account ACL enforcement is off by default**
(``AclManager.is_enabled`` reads the account setting ``acl.enabled``), so the ACL
records alone do nothing until the account switch is turned on.
"""

from __future__ import annotations

import json
from pathlib import Path

from spike_common import SPIKE_ROOT, ApiError, api, auth_headers, log

IDENTITIES_FILE = SPIKE_ROOT / "identities.json"
ACCOUNT = "default"
WRITER = "mem-rw"
READER = "mem-ro"


def _load() -> dict:
    if IDENTITIES_FILE.exists():
        return json.loads(IDENTITIES_FILE.read_text(encoding="utf-8"))
    return {}


def _save(data: dict) -> None:
    IDENTITIES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _list_user_keys() -> dict:
    users = api("GET", f"/api/v1/admin/accounts/{ACCOUNT}/users",
                headers=auth_headers(), timeout=60)["result"]
    return {u["user_id"]: u["api_key"] for u in users if u.get("api_key")}


def _ensure_user(user_id: str, role: str) -> str:
    try:
        resp = api("POST", f"/api/v1/admin/accounts/{ACCOUNT}/users",
                   body={"user_id": user_id, "role": role, "seed": f"spike-{user_id}"},
                   headers=auth_headers(), timeout=60)
        key = resp["result"].get("user_key")
        if key:
            return key
    except ApiError as exc:
        body = exc.body[:300].decode("utf-8", "replace").lower()
        if exc.status not in (400, 409) or "exist" not in body:
            raise
        log(f"identity {user_id} already registered; reusing its key")
    key = _list_user_keys().get(user_id)
    if not key:
        raise RuntimeError(f"could not obtain api key for {user_id}")
    return key


def enable_account_acl() -> dict:
    """Turn on ACL enforcement for the account (default is OFF)."""
    resp = api("PATCH", f"/api/v1/admin/accounts/{ACCOUNT}/settings",
               body={"acl": {"enabled": True}}, headers=auth_headers(), timeout=60)
    return resp["result"]


def acl_enabled() -> bool:
    resp = api("GET", f"/api/v1/admin/accounts/{ACCOUNT}/settings",
               headers=auth_headers(), timeout=60)
    return bool(resp["result"]["settings"]["acl"]["enabled"])


def ensure_identities() -> dict:
    """Idempotently provision both consumers and enable account ACL.

    Returns {"writer": key, "reader": key, "principals": {...}} -- callers must
    not log the keys.
    """
    data = _load()
    for user_id, role in ((WRITER, "admin"), (READER, "user")):
        if user_id not in data:
            data[user_id] = _ensure_user(user_id, role)
            _save(data)
    if not acl_enabled():
        enable_account_acl()
        log("account ACL enforcement enabled (default was off)")
    return {
        "writer": data[WRITER],
        "reader": data[READER],
        "principals": {"writer": f"user:{WRITER}", "reader": f"user:{READER}"},
        "account": ACCOUNT,
    }


def grant(uri: str, principal: str, level: str, *, key: str) -> dict:
    return api("POST", "/api/v1/acl/grant",
               body={"uri": uri, "principal": principal, "level": level},
               headers=auth_headers(key), timeout=60)


def set_mode(uri: str, mode: str, *, key: str) -> dict:
    return api("PUT", "/api/v1/acl", body={"uri": uri, "acl_mode": mode},
               headers=auth_headers(key), timeout=60)


def get_acl(uri: str, *, key: str) -> dict:
    return api("GET", "/api/v1/acl", params={"uri": uri}, headers=auth_headers(key), timeout=60)
