"""#69 跨平台回归：CLI 在**非 UTF-8 控制台**（Windows cp1252）也必须能把中文输出出去。

CI 实测（GitHub `windows-latest`，2026-10-07 第一次 run）：`python -m memory_agent.eval.harness`
的 stdio 默认按**本地代码页**编码 → 写中文报告 → `UnicodeEncodeError: 'charmap' codec
can't encode characters ...` → 子进程退出码 1，于是 `test_agent_loop_harness.py::
test_deterministic_subprocess_runs` 变红。同一条路也砸在接入冒烟的 `SKIP：…` 中文诊断上。

修法：入口处 `memory_agent._bootstrap.configure_utf8_stdio()`（与 `--out` 落盘一直用的
`encoding="utf-8"` 对齐）。本文件用 `PYTHONIOENCODING=cp1252` **复现**那个环境，Linux 上同样有效。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _run_cp1252(args: list[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    return subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",  # 我们是按 UTF-8 写出去的，父进程也按 UTF-8 读
    )


def test_harness_cli_survives_cp1252_console():
    proc = _run_cp1252(["-m", "memory_agent.eval.harness"])
    assert proc.returncode == 0, proc.stderr
    json.loads(proc.stdout)  # 报告完整写出（没被编码错误截断/吞掉）


def test_smoke_cli_survives_cp1252_console_when_server_absent():
    proc = _run_cp1252(
        ["memory_agent/eval/opencode_server_smoke_62.py", "--base-url", "http://127.0.0.1:1"]
    )
    assert proc.returncode == 0, proc.stderr  # server 缺席 = SKIP（中文诊断也要写得出去）
    assert "SKIP" in proc.stderr
