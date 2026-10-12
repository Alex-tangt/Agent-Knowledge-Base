#!/usr/bin/env python
r"""scope_probe.py -- how do identity headers actually scope the namespace on this instance?

M6 asks "身份与范围如何生效". Rather than assert, measure: write once with one identity and then
read the same URI back under (a) the same identity, (b) a different account, (c) a different user
in the same account, (d) no identity headers at all. Then record where the bytes landed on disk.

Runs against THIS line's instance only (127.0.0.1:8782) with stdlib urllib.

Usage:
    python scope_probe.py --json-out evidence/scope-probe.json
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = os.environ.get("HOST_BASE_URL", "http://127.0.0.1:8782")
ROOT = Path(os.environ.get("HOST_ROOT") or (Path(os.path.expanduser("~")) / ".openviking-mvp-host"))
URI = "viking://resources/mvp-host-connect/scope-probe.md"
CONTENT = "SCOPE-PROBE marker=mvp-host-connect\n"


def call(method: str, path: str, *, body: dict | None = None, params: dict | None = None,
         headers: dict | None = None, timeout: float = 120.0) -> dict:
    url = BASE + path
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    hdrs = {"Accept": "application/json"}
    if data is not None:
        hdrs["Content-Type"] = "application/json; charset=utf-8"
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"status": resp.status, "body": json.loads(resp.read().decode("utf-8") or "null")}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        return {"status": exc.code, "error": raw[:600]}
    except Exception as exc:
        return {"status": 0, "error": f"{type(exc).__name__}: {exc}"}


def ident(account: str | None, user: str | None) -> dict:
    h = {}
    if account:
        h["X-OpenViking-Account"] = account
    if user:
        h["X-OpenViking-User"] = user
    return h


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--account-a", default="scope-a")
    ap.add_argument("--account-b", default="scope-b")
    ap.add_argument("--user-a1", default="user-a1")
    ap.add_argument("--user-a2", default="user-a2")
    args = ap.parse_args()

    out: dict = {"base_url": BASE, "uri": URI, "workspace": str(ROOT / "data"), "probes": {}}

    # 1. write as account A / user A1
    w = call("POST", "/api/v1/content/write",
             body={"uri": URI, "content": CONTENT, "mode": "replace", "wait": True},
             headers=ident(args.account_a, args.user_a1))
    out["probes"]["write_as_A_a1"] = w

    # 2..5 read the SAME uri under different identities
    reads = {
        "read_same_identity_A_a1": ident(args.account_a, args.user_a1),
        "read_other_account_B": ident(args.account_b, args.user_a1),
        "read_other_user_same_account": ident(args.account_a, args.user_a2),
        "read_no_identity": {},
    }
    for name, hdrs in reads.items():
        r = call("GET", "/api/v1/content/read",
                 params={"uri": URI, "raw": "true", "offset": 0, "limit": -1}, headers=hdrs)
        text = ""
        if r.get("status") == 200 and isinstance(r.get("body"), dict):
            text = str(r["body"].get("result", ""))
        out["probes"][name] = {
            "identity": hdrs,
            "status": r.get("status"),
            "visible": CONTENT.strip() in text,
            "content_head": text[:200],
            "error": r.get("error", "")[:300],
        }

    # where did the bytes land?
    landed = []
    viking = ROOT / "data" / "viking"
    if viking.is_dir():
        for p in viking.rglob("scope-probe.md"):
            landed.append({"path": str(p), "bytes": p.stat().st_size})
    out["landed_on_disk"] = landed
    out["accounts_on_disk"] = sorted(p.name for p in viking.iterdir()) if viking.is_dir() else []

    text = json.dumps(out, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
        print(f"[evidence] {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
