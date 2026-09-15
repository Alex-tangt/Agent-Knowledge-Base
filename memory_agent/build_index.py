"""CLI：从 Markdown 真相源全量重建派生索引。

    venv\\Scripts\\python.exe memory_agent/build_index.py

stdout 只输出统计 JSON（日志走 stderr），便于脚本消费。
"""
from __future__ import annotations

import json
import sys

from memory_agent.memory.errors import IndexConsistencyError  # noqa: E402
from memory_agent.runtime import build_index  # noqa: E402
from memory_agent.settings import INDEX_DIR, KB_DIR, READONLY_ROOTS  # noqa: E402


def main() -> int:
    try:
        stats = build_index()
    except IndexConsistencyError as exc:
        print(f"重建失败：{exc}", file=sys.stderr)
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
