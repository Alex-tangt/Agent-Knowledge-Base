"""Validate report link targets locally, without network or upstream imports.

Checks target existence, fixed source commits, and line bounds. It does not
validate whether a cited passage proves the surrounding prose.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


STUDY = Path(__file__).resolve().parent
REPO = STUDY.parents[1]
BASE = REPO.parent
SOURCES = {
    "volcengine/OpenViking": (BASE / "OpenViking", "df32bf6e50a40843438f9491a26069ca4bd08f1f"),
    "MemTensor/MemOS": (BASE / "MemOS", "a7367d07e55db61099f7b4e2c1108bc5831a24f3"),
}
FILES = [STUDY / f for f in ("README.md", "openviking.md", "memos-python.md", "memos-local.md")]
FILES += [REPO / "docs/architecture-memory-base-source-study-2026-10-11.md"]


def main() -> None:
    errors, external = [], []
    checked_source = checked_local = 0
    for file in FILES:
        if not file.is_file():
            errors.append({"file": file.name, "error": "missing_report"})
            continue
        lines, fence = [], None
        for line in file.read_text(encoding="utf-8-sig").splitlines():
            marker = re.match(r"^\s*(`{3,}|~{3,})", line)
            if marker:
                if fence is None:
                    fence = marker[1][0]
                elif marker[1][0] == fence:
                    fence = None
                continue
            if fence is None:
                lines.append(line)
        body = "\n".join(lines)
        definitions = {name.casefold() for name in re.findall(r"^\[([^\]]+)\]:", body, re.MULTILINE)}
        for name in re.findall(r"\[[^\]\n]+\]\[([^\]\n]+)\]", body):
            if name.casefold() not in definitions:
                errors.append({"file": file.name, "target": name, "error": "undefined_reference_link"})
        targets = re.findall(r"\[[^\]]*\]\(([^)]+)\)", body)
        targets += re.findall(r"^\[[^\]]+\]:\s*(\S+)", body, re.MULTILINE)
        for raw in targets:
            target = raw.strip("<>")
            url = urlsplit(target)
            if url.scheme in {"http", "https"}:
                parts = unquote(url.path).strip("/").split("/")
                repository = "/".join(parts[:2])
                if url.netloc != "github.com" or repository not in SOURCES or len(parts) < 5 or parts[2] not in {"blob", "tree"}:
                    external.append(target)
                    continue
                root, commit = SOURCES[repository]
                path = root.joinpath(*parts[4:])
                checked_source += 1
                error = None
                if parts[3] != commit:
                    error = "not_fixed_study_commit"
                elif not path.exists():
                    error = "missing_source_target"
                elif url.fragment and path.is_file():
                    fragment = re.fullmatch(r"L(\d+)(?:-L(\d+))?", url.fragment)
                    if fragment:
                        start = int(fragment[1])
                        end = int(fragment[2] or fragment[1])
                        count = len(path.read_text(encoding="utf-8-sig").splitlines())
                        if start < 1 or start > end or end > count:
                            error = "line_range_out_of_bounds"
                if error:
                    errors.append({"file": file.name, "target": target, "error": error})
            elif not url.scheme and url.path:
                checked_local += 1
                if not (file.parent / unquote(url.path)).exists():
                    errors.append({"file": file.name, "target": target, "error": "missing_local_target"})
    result = {"reports": len(FILES), "source_links_checked": checked_source,
              "local_links_checked": checked_local, "other_web_links_not_checked": len(external),
              "errors": errors, "scope": "Existence, fixed SHA and line bounds; not citation semantics or web availability."}
    (STUDY / "link-check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
