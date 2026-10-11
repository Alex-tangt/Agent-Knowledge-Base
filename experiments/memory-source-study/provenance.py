"""Read-only comparison of pinned source and installed Python wheel contents.

Run from the project venv. No upstream imports, network calls, or live data access.
Generated version files and platform-specific distributions can legitimately differ;
this checks the Python files shared by the checkout and wheel, not native binaries.
"""
from __future__ import annotations

import ast
import json
from importlib.metadata import PathDistribution
from pathlib import Path


BASE = Path(__file__).resolve().parents[3]
STUDY = Path(__file__).resolve().parent
SOURCE = BASE / "OpenViking" / "openviking"
INSTALLED = Path(r"C:\Users\Tan\.agent-memory-base\runtime\Lib\site-packages\openviking")
DIST = INSTALLED.parent / "openviking-0.4.23.dist-info"


def collect(root: Path) -> dict[str, Path]:
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*.py")}


def parsed(path: Path) -> str:
    return ast.dump(ast.parse(path.read_text(encoding="utf-8-sig")), include_attributes=False)


def main() -> None:
    source, installed = collect(SOURCE), collect(INSTALLED)
    shared = sorted(source.keys() & installed.keys())
    text_equal, ast_equal, different, parse_errors = [], [], [], []
    for relative in shared:
        a, b = source[relative], installed[relative]
        if a.read_text(encoding="utf-8-sig") == b.read_text(encoding="utf-8-sig"):
            text_equal.append(relative)
            ast_equal.append(relative)
            continue
        try:
            if parsed(a) == parsed(b):
                ast_equal.append(relative)
            else:
                different.append(relative)
        except (SyntaxError, UnicodeError) as error:
            parse_errors.append({"path": relative, "type": type(error).__name__})
    metadata = PathDistribution(DIST).metadata
    result = {
        "source_commit": "df32bf6e50a40843438f9491a26069ca4bd08f1f",
        "source_tag": "v0.4.23",
        "wheel_version": metadata.get("Version"),
        "source_python_files": len(source),
        "installed_python_files": len(installed),
        "shared_python_files": len(shared),
        "normalized_text_equal_count": len(text_equal),
        "ast_equal_count": len(ast_equal),
        "ast_different": different,
        "parse_errors": parse_errors,
        "source_only": sorted(source.keys() - installed.keys()),
        "installed_only": sorted(installed.keys() - source.keys()),
        "limits": [
            "Python files only; native RAGFS/AGFS libraries and vector backend binaries are not compared.",
            "AST equality is source correspondence, not a runtime behavior or end-to-end test.",
            "No upstream packages were imported and no production data or services were accessed.",
        ],
    }
    output = STUDY / "provenance.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in {"source_only", "installed_only"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
