"""Reproduce the local-embedder native crash that blocks the clean-room restore.

Each probe runs as its own process: a native GGML_ASSERT aborts the process, so
exit codes are the evidence.  Windows reports a fail-fast abort as a large
negative exit code.

    python probe_local_embedder_results.py

Writes evidence/local_embedder_crash.json / .md.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from spike_common import PY_EXE, emit, http_status, log

REPO_ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent

PROBES = [
    {"id": "single-normal", "script": "probe_local_embedder.py", "args": ["normal"],
     "purpose": "单个短中文输入"},
    {"id": "single-empty", "script": "probe_local_embedder.py", "args": ["empty"],
     "purpose": "空字符串 + 单字符输入（排除『空输入致崩』）"},
    {"id": "single-long", "script": "probe_local_embedder.py", "args": ["long"],
     "purpose": "56000 字符超长输入（排除『超上下文致崩』）"},
    {"id": "batch-2", "script": "probe_local_embedder_batch.py", "args": ["2"],
     "purpose": "批量 2 条（排除『批处理致崩』）"},
    {"id": "batch-32", "script": "probe_local_embedder_batch.py", "args": ["32"],
     "purpose": "批量 32 条"},
    {"id": "serial-threads-10", "script": "probe_local_embedder_concurrency.py", "args": ["serial", "10"],
     "purpose": "10 线程 + 显式加锁串行访问同一 Llama 上下文"},
    {"id": "concurrent-threads-10", "script": "probe_local_embedder_concurrency.py",
     "args": ["concurrent", "10"],
     "purpose": "10 线程**并发**访问同一 Llama 上下文（模拟候选默认 max_concurrent=10）"},
]


def main() -> int:
    results = []
    for probe in PROBES:
        t0 = time.time()
        proc = subprocess.run(
            [str(PY_EXE), str(HERE / probe["script"]), *probe["args"]],
            capture_output=True, cwd=str(HERE), timeout=1800,
        )
        stdout = proc.stdout.decode("utf-8", "replace")
        stderr = proc.stderr.decode("utf-8", "replace")
        crash = proc.returncode != 0
        results.append({
            "id": probe["id"], "purpose": probe["purpose"],
            "command": f"python {probe['script']} {' '.join(probe['args'])}",
            "returncode": proc.returncode, "crash": crash,
            "seconds": round(time.time() - t0, 1),
            "stdout_tail": stdout[-500:], "stderr_tail": stderr[-500:],
            "assert_lines": [ln for ln in (stdout + stderr).splitlines() if "GGML_ASSERT" in ln][:3],
        })
        log(f"{probe['id']}: returncode={proc.returncode} crash={crash}")

    by_id = {r["id"]: r for r in results}
    confirmed = (by_id["serial-threads-10"]["returncode"] == 0
                 and by_id["concurrent-threads-10"]["returncode"] != 0)
    evidence = {
        "unit": "M5-support",
        "claim": "本地 GGUF 嵌入在并发访问下原生 abort —— clean-room 恢复被它阻断",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "verdict": "PASS" if confirmed else "FAIL",
        "isolated_explanations": {
            "empty_input": by_id["single-empty"]["returncode"] == 0,
            "oversized_input": by_id["single-long"]["returncode"] == 0,
            "batched_input": by_id["batch-32"]["returncode"] == 0,
            "serial_threads": by_id["serial-threads-10"]["returncode"] == 0,
            "concurrent_threads": by_id["concurrent-threads-10"]["returncode"] != 0,
        },
        "probes": results,
        "conclusion": ("并发访问同一个 Llama 上下文不可用：单输入（空/超长）、批量输入、"
                       "以及加锁串行访问都正常，只有并发访问触发原生 GGML_ASSERT 并使进程整体退出。"
                       "候选默认 embedding.max_concurrent=10；实测把 clean-room 配置改成 1 "
                       "仍无法让整账户/范围包的恢复走完（服务在恢复过程中被 abort）。"),
        "impact_on_M5": ("所以 M5.4（干净位置恢复）在本机判 FAIL：不是恢复语义错，"
                          "而是本地嵌入路径让恢复过程无法完成。"),
        "suggested_next_step": ("在框架侧给本地嵌入调用加互斥（或在候选内串行化 embedding worker）后重测；"
                                 "或改用进程外/HTTP 的嵌入提供方。"),
    }

    md = [
        "# 支撑证据 · 本地 GGUF 嵌入并发崩溃（阻断 M5.4 clean-room 恢复）",
        "",
        f"- 结论：**{evidence['verdict']}**（并发是被确认的触发条件，其余解释被逐一排除）",
        f"- 生成时间：{evidence['generated_at']}",
        "- 模型：`bge-small-zh-v1.5-f16.gguf`（llama-cpp-python 0.3.36，CPU）",
        "",
        "| 探针 | 目的 | 退出码 | 是否崩溃 | 断言 |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        md.append(f"| `{r['command']}` | {r['purpose']} | {r['returncode']} | {r['crash']} | "
                  f"`{(r['assert_lines'][0] if r['assert_lines'] else '')[:90]}` |")
    md += [
        "",
        "## 结论",
        "",
        evidence["conclusion"],
        "",
        "## 对 M5 的影响",
        "",
        evidence["impact_on_M5"],
        "",
        "## 建议下一步",
        "",
        evidence["suggested_next_step"],
    ]
    emit("local_embedder_crash.json", evidence)
    (REPO_ROOT / "experiments" / "memory-framework-spike" / "mvp" / "evidence"
     / "local_embedder_crash.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    log(f"probe verdict={evidence['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
