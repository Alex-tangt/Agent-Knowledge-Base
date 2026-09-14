"""CLI：从 Markdown 真相源全量重建派生索引。

    venv\\Scripts\\python.exe memory_agent/build_index.py

stdout 只输出统计 JSON（日志走 stderr），便于脚本消费。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _HERE]
sys.path.insert(0, _ROOT)

from memory_agent.runtime import build_index  # noqa: E402
from memory_agent.settings import INDEX_DIR, KB_DIR, READONLY_ROOTS  # noqa: E402


def main() -> int:
    stats = build_index()
    if not stats["entries"]:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        print("没有发现任何条目：请检查 KB_DIR / READONLY_ROOTS", file=sys.stderr)
        return 1
    print(json.dumps({
        **stats,
        "kb_dir": KB_DIR,
        "readonly_roots": READONLY_ROOTS,
        "index_dir": INDEX_DIR,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
