import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
base = Path(r"D:\python_work\work2026-4\wk-spike\experiments\memory-framework-spike\mvp\evidence")
for name in sorted(base.glob("*.json")):
    raw = name.read_bytes()
    bom = raw[:3] == b"\xef\xbb\xbf"
    try:
        data = json.loads(raw.decode("utf-8"))
        ok = "OK"
    except Exception as exc:  # noqa: BLE001
        ok = f"INVALID: {exc}"
        data = {}
    extra = ""
    if name.name == "m5_backup.json":
        rp = data.get("recoverable_points")
        extra = (f" | recoverable_points keys={list(rp.keys()) if isinstance(rp, dict) else type(rp).__name__}"
                 f" | restore_cli_response_set={data.get('restore_cli_response') is not None}"
                 f" | verdict={data.get('verdict')}")
    print(f"{name.name}: utf8={ok} bom={bom} bytes={len(raw)}{extra}")
