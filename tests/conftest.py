"""跨用例的**进程级 env 隔离**（#69 CI 实测出的必要守卫）。

问题（2026-10-07，第一次 CI run 量到）：产品代码会**直接改 `os.environ`** 而不是走
调用方的 monkeypatch——最典型的是 `ragcore.config.hf.ensure_hf_offline()`：模型全在缓存
时它会置 `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1`（这是**设计行为**，不是 bug）。
`monkeypatch.delenv` 只恢复它自己改过的那几个键，**管不到产品代码的写入**，于是这个开关会
泄漏给同一 pytest session 的后续用例。

后果在**没有缓存**的机器上最难看（CI 全新 runner 正是）：`fastembed` 读 `HF_HUB_OFFLINE`
（`model_management.py` 里 `hf_offline = os.environ.get("HF_HUB_OFFLINE", "")`）→ 判本地模式
→ `Qdrant/bm25` 不在缓存里 → `ValueError: Could not load model Qdrant/bm25 from any source.`
——3 个 store 单测就这么红的，错误信息与真实原因（前一个测试留下的开关）看起来毫无关系。

本地复现（同一台机器，只把 fastembed 缓存指到空目录）：
    $env:MEMORY_BM25_CACHE_DIR = <空目录>
    pytest tests/unit -q            # → 3 failed（与 CI 逐字相同）
    pytest tests/unit/test_hf_offline.py tests/unit/test_memory_store_port.py -q   # → 1 failed（锁定污染源）

守卫只恢复「进程级开关」这一小类，不动普通 env / cwd / 文件系统：粒度小、误伤面为零。
"""
from __future__ import annotations

import os
import sys

import pytest

from ragcore.config import hf

_OFFLINE_ENV_VARS = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")


@pytest.fixture(autouse=True)
def _restore_process_level_switches():
    """逐用例快照 / 恢复 HF 离线开关（env + 已加载 HF 模块里的常量副本 + `_SELF_SET`）。"""
    env_before = {name: os.environ.get(name) for name in _OFFLINE_ENV_VARS}
    self_set_before = getattr(hf, "_SELF_SET", None)
    consts_before = {
        (module_name, attr): getattr(sys.modules[module_name], attr, None)
        for module_name, attr in hf._LOADED_OFFLINE_BINDINGS
        if module_name in sys.modules
    }
    yield
    for (module_name, attr), value in consts_before.items():
        if value is not None and hasattr(sys.modules[module_name], attr):
            setattr(sys.modules[module_name], attr, value)
    for name, value in env_before.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    if self_set_before is not None:
        hf._SELF_SET = self_set_before
