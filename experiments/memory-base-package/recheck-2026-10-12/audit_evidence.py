"""Check only new fixture secrets against intended evidence; never print values."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from memory_base.credentials import get_secret
from memory_base.deploy import PROFILES


def main():
    asset = Path(r"C:\Users\Tan\.mb86\r4")
    assert Path(sys.executable).resolve() == (asset / "f/runtime/Scripts/python.exe").resolve()
    here = Path(__file__).resolve().parent
    names = ("root-key", "upstream-key", *["host-" + profile for profile in PROFILES])
    values = {value.encode() for home in (asset / "d", asset / "f") for name in names
              if (value := get_secret(name, home))}
    assert values, "Fixture credentials were not present for evidence audit"
    paths = [path for path in here.iterdir() if path.suffix in (".py", ".json", ".md")]
    paths += [path for path in asset.iterdir() if path.suffix in (".json", ".log")]
    paths += [path for path in (asset / "normal-native").iterdir() if path.suffix in (".json", ".log")]
    for home in (asset / "d", asset / "f"):
        paths += [path for path in home.iterdir() if path.suffix in (".json", ".conf")]
        paths += list((home / "logs").glob("*"))
    paths = sorted(set(path for path in paths if path.is_file()))
    hits = []
    for path in paths:
        content = path.read_bytes()
        if any(value in content for value in values):
            hits.append(str(path))
    report = {"checked_files": len(paths), "fixture_secret_values_checked": len(values),
              "only_fixture_namespaces_read": True, "plaintext_fixture_secret_found": bool(hits),
              "files_with_hits": hits}
    text = json.dumps(report, indent=2) + "\n"
    (asset / "normal-native/evidence-audit.json").write_text(text, encoding="utf-8")
    (here / "evidence_audit.json").write_text(text, encoding="utf-8")
    print(text, end="")
    assert not hits, "Secret appeared in evidence; inspect without displaying values"


if __name__ == "__main__":
    main()
