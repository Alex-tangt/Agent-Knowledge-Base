"""H2 程序化断言（#61）：插件创建的子会话的**工具输出（检索 chunk）**不得出现在父会话。

用途：`memory_research` 插件（形态 B）只把**最终文本**作为工具结果回主对话；本脚本对
**真实运行留下的 opencode 存储**做断言——子会话里 `memory_search/get` 的返回正文（chunk），
不得出现在父会话的任何 part 里。子会话最后的 assistant `text`（= 插件正常回传的答案）
**不参与**检查。

只读 `~/.local/share/opencode/opencode.db`（opencode 的会话存储）；不改任何状态。

用法（仓库根，主树 venv）：
    venv\\Scripts\\python.exe memory_agent/eval/h2_parent_isolation_check.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

DEFAULT_DB = os.path.expanduser("~/.local/share/opencode/opencode.db")
TITLE_LIKE = "memory_research:%"
FRAG = 200  # 每个片段的字符数


def _raw_parts(conn: sqlite3.Connection, session_id: str) -> list[str]:
    return [row["data"] for row in conn.execute(
        "select data from part where session_id=? order by time_created", (session_id,)
    ).fetchall()]


def _tool_outputs(conn: sqlite3.Connection, session_id: str) -> list[str]:
    """子会话里 `type=="tool"` 且 `status=="completed"` 的 `state.output`（chunk 正文）。"""
    out: list[str] = []
    for data in _raw_parts(conn, session_id):
        try:
            part = json.loads(data)
        except (ValueError, TypeError):
            continue
        if part.get("type") != "tool":
            continue
        state = part.get("state") or {}
        if state.get("status") != "completed":
            continue
        output = state.get("output")
        if isinstance(output, str) and output:
            out.append(output)
    return out


def check(db_path: str = DEFAULT_DB, limit: int = 5) -> int:
    if not os.path.isfile(db_path):
        print(f"FAIL: 找不到 opencode 存储：{db_path}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    children = conn.execute(
        "select id, parent_id, title from session where title like ? "
        "order by time_created desc limit ?", (TITLE_LIKE, limit)
    ).fetchall()
    if not children:
        print(f"FAIL: 没有 title like {TITLE_LIKE!r} 的子会话（先跑一次 memory_research）",
              file=sys.stderr)
        return 1

    failed = 0
    for child in children:
        parent_blob = "\n".join(_raw_parts(conn, child["parent_id"]))
        outputs = _tool_outputs(conn, child["id"])
        checked = leaks = 0
        for output in outputs:
            for start in range(0, max(1, len(output) - FRAG), FRAG):
                fragment = output[start:start + FRAG]
                if len(fragment.strip()) < 50:
                    continue
                checked += 1
                if fragment in parent_blob:
                    leaks += 1
        verdict = "PASS" if leaks == 0 else "FAIL"
        failed += 1 if leaks else 0
        print(f"[{verdict}] child={child['id']} tools_completed={len(outputs)} "
              f"chunk_bytes={sum(len(o) for o in outputs)} frags={checked} leaks={leaks}")
        print(f"         title={child['title'][:70]}")

    print(f"\nRESULT: {'PASS' if failed == 0 else 'FAIL'} ({len(children)} children)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(check())
