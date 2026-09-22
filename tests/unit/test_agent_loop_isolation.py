"""守卫：**运行时（agent_loop / trace）不得依赖评测**（ADR-0030 D7）。

两层：
1. 静态：包内源码不得出现 import 评测；
2. 运行时：import 整个包后，`sys.modules` 里不得出现 `memory_agent.eval*`。
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIRS = [ROOT / "memory_agent" / "agent_loop"]
SOURCE_FILES = [ROOT / "memory_agent" / "trace.py"]

_IMPORT_EVAL = re.compile(r"^\s*(?:from|import)\s+[^\n]*\beval\b")


def _runtime_sources():
    files = list(SOURCE_FILES)
    for directory in SOURCE_DIRS:
        files.extend(sorted(directory.glob("*.py")))
    return files


def test_runtime_does_not_import_eval_statically():
    offenders = []
    for path in _runtime_sources():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _IMPORT_EVAL.match(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, "运行时不得 import 评测：" + "; ".join(offenders)


def test_runtime_does_not_import_eval_at_runtime():
    code = (
        "import memory_agent.agent_loop, sys;"
        "bad = [m for m in sys.modules if m.startswith('memory_agent.eval')];"
        "print('OK' if not bad else 'BAD=' + ','.join(bad))"
    )
    result = subprocess.run([sys.executable, "-c", code],
                            capture_output=True, text=True, cwd=str(ROOT))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK", result.stdout
