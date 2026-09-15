"""验证 memory_agent 与 `legal_web/.env` 解耦（issue #22 / ADR-0016）。

做法：所有子进程都注入一个 `sitecustomize.py` 守卫——它在 `dotenv` 上挂钩，凡以
`legal_web` 路径调用 `load_dotenv` 立即 `AssertionError`，并把每次调用的路径记到文件。
再把 LLM 凭证从子进程环境里剥掉（`legal_web/.env` 本来也不会被读）。

两段证据：
1. **导入冒烟**：`import memory_agent.runtime` + `import ragcore.services.vector_store_service`
   （曾经的传递性耦合来源）在不含 LLM 凭证的环境里成功；并断言 `ragcore.config.llm`（llm 层）
   根本没被 import。
2. **全量单测**：`pytest tests/unit -q` 在同一守卫下全绿——构建 / 检索 / 写入路径
   （以 fake store 驱动）都不触碰 `legal_web/.env`。

memory_agent 自己的 `.env` 用 `MEMORY_ENV_FILE` 指向一个临时哨兵文件，验证它确实被加载。

    venv\\Scripts\\python.exe memory_agent/eval/config_independence_22.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))

SMOKE = r"""
import json, os, sys
from memory_agent import runtime          # 立 stderr 日志 + 装配索引
import ragcore.services.vector_store_service as vss  # 曾经的传递性耦合来源
assert "ragcore.config.llm" not in sys.modules, "memory_agent 不应 import llm 层 (ragcore.config.llm)"
print("@@" + json.dumps({
    "imported_runtime": True,
    "imported_vector_store": True,
    "llm_layer_imported": False,
    "own_env_loaded": os.environ.get("MEMORY_TEST_INDEPENDENCE"),
}))
"""

REQUIRE_LLM = r"""
import sys
from ragcore.config.llm import require_llm

try:
    require_llm()
except ValueError as exc:
    print("@@EXPECTED_FAILURE:" + str(exc))
    sys.exit(0)
print("@@UNEXPECTED_SUCCESS")
sys.exit(3)
"""

SITECUSTOMIZE = r'''
import os, pathlib

_REC = os.environ.get("INDEPENDENCE_GUARD_RECORDS")


def _record(path):
    if _REC:
        with open(_REC, "a", encoding="utf-8") as fh:
            fh.write(path + "\n")


try:
    import dotenv

    _orig = dotenv.load_dotenv

    def _guard(path=None, *args, **kwargs):
        resolved = "" if path is None else str(pathlib.Path(path).resolve())
        resolved = resolved.replace("\\", "/")
        _record(resolved)
        if "legal_web" in resolved:
            raise AssertionError("非法读取 legal_web/.env: " + resolved)
        return _orig(path, *args, **kwargs)

    dotenv.load_dotenv = _guard
except Exception:
    pass
'''


def _child_env(guard_dir: str, records_path: str, sentinel_env: str) -> dict:
    env = dict(os.environ)
    for name in ("API_KEY", "BASE_URL", "Model",
                 "LANGSMITH_API_KEY", "LANGSMITH_PROJECT",
                 "LANGSMITH_ENDPOINT", "LANGSMITH_TRACING"):
        env.pop(name, None)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (guard_dir, REPO_ROOT, env.get("PYTHONPATH", "")) if p
    )
    env["INDEPENDENCE_GUARD_RECORDS"] = records_path
    env["MEMORY_ENV_FILE"] = sentinel_env
    env["MEMORY_READONLY_ROOTS"] = ""  # 不扫描真实语料仓库
    return env


def main() -> int:
    workdir = tempfile.mkdtemp(prefix="memory-independence-")
    guard_dir = workdir
    records_path = os.path.join(workdir, "dotenv_calls.txt")
    sentinel_env = os.path.join(workdir, "agent.env")
    with open(sentinel_env, "w", encoding="utf-8") as fh:
        fh.write("MEMORY_TEST_INDEPENDENCE=own-env-loaded\n")
    with open(os.path.join(guard_dir, "sitecustomize.py"), "w", encoding="utf-8") as fh:
        fh.write(SITECUSTOMIZE)

    env = _child_env(guard_dir, records_path, sentinel_env)
    report: dict = {"workdir": workdir}

    # 1) 导入冒烟
    smoke = subprocess.run(
        [sys.executable, "-c", SMOKE],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )
    report["import_smoke_returncode"] = smoke.returncode
    report["import_smoke_stdout_tail"] = smoke.stdout.strip().splitlines()[-1:] or []
    report["import_smoke_stderr_tail"] = smoke.stderr.strip().splitlines()[-3:]

    smoke_ok = smoke.returncode == 0 and "@@" in smoke.stdout
    if smoke_ok:
        report["import_smoke"] = json.loads(smoke.stdout.split("@@", 1)[1].strip())

    # 2) 全量单测（同守卫）
    tests = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/unit", "-q"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )
    report["pytest_returncode"] = tests.returncode
    report["pytest_summary"] = tests.stdout.strip().splitlines()[-1:] or []

    # 3) 守卫记录：不得出现任何 legal_web 路径
    calls: list[str] = []
    if os.path.isfile(records_path):
        with open(records_path, "r", encoding="utf-8") as fh:
            calls = [line.strip() for line in fh if line.strip()]
    report["dotenv_calls"] = calls
    report["legal_web_reads"] = [c for c in calls if "legal_web" in c]

    # 4) 适配层仍显式失败：没有 legal_web/.env 时 require_llm 必须抛 ValueError。
    if os.path.isfile(os.path.join(REPO_ROOT, "legal_web", ".env")):
        report["explicit_failure_ok"] = "skipped (legal_web/.env present)"
    else:
        failed = subprocess.run(
            [sys.executable, "-c", REQUIRE_LLM],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True,
        )
        report["explicit_failure_returncode"] = failed.returncode
        report["explicit_failure_marker"] = failed.stdout.strip()
        report["explicit_failure_ok"] = (
            failed.returncode == 0 and "@@EXPECTED_FAILURE:" in failed.stdout
        )

    report["passed"] = (
        smoke_ok
        and tests.returncode == 0
        and not report["legal_web_reads"]
        and report.get("import_smoke", {}).get("own_env_loaded") == "own-env-loaded"
        and report.get("import_smoke", {}).get("llm_layer_imported") is False
        and report.get("explicit_failure_ok") in (True, "skipped (legal_web/.env present)")
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
