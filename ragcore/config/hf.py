"""HF hub 离线开关（缓存感知，issue #18）。

`sentence_transformers` / `transformers` 在解析模型 **revision / 元数据** 时会走
`huggingface_hub` 的 model-info API（`GET /api/models/<repo>`、`commits`、`discussions` …）；
构造器里的 `local_files_only=True` **只挡文件下载、不挡这条路径**。于是即便模型已缓存，
冷启动仍会外呼 huggingface.co：弱网 / 代理不可达时会被拖到分钟级，甚至直接加载失败
（复现与数据见 `experiments/hf-offline-warmup/`）。

本模块在 **import HF 之前** 调用：若目标模型都已在本地缓存，则把进程切到离线模式
（`HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1`），元数据解析走本地、零外呼；若有模型
缺失则保持联网（首次下载仍可用）并告警。

显式设置了 `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE`（含 `0`）时一律不覆盖。

**运行时兜底（issue #46）**：`huggingface_hub.constants.HF_HUB_OFFLINE` 是 **import 期
常量**；`memory_agent` 的依赖链（qdrant_client → huggingface_hub）会先于本模块的调用把
HF import 进来，此时只设 `os.environ` 是空操作（新进程里常量仍是 `False`）。因此切离线时
同时**改写已加载模块里的常量副本**，见 `_patch_loaded_hf_modules()`。
"""
from __future__ import annotations

import os
import sys

from ragcore.config.config import LOCAL_EMBEDDING_MODEL, LOCAL_RERANKER_MODEL
from ragcore.utils.logger import logger

_OFFLINE_ENV_VARS = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
_TRUTHY = {"1", "true", "yes", "on"}

# 已加载 HF 模块里的 offline 判定副本（按实际 import 图枚举，issue #46）：
# - `huggingface_hub.constants.HF_HUB_OFFLINE` 是源头；其余 HF 子模块按属性访问读它
#   （`constants.HF_HUB_OFFLINE`，调用期读取），改这里即覆盖。
# - `transformers.utils.hub._is_offline_mode` 是 transformers 在 import 期缓存的副本，
#   `is_offline_mode()` 只读它，不改则漏。
# - `transformers.commands.serving.HF_HUB_OFFLINE` 是唯一的 `from ... import` 绑定
#   （CLI 用），名字已绑到别处，需单独改。
_LOADED_OFFLINE_BINDINGS = (
    ("huggingface_hub.constants", "HF_HUB_OFFLINE"),
    ("transformers.utils.hub", "_is_offline_mode"),
    ("transformers.commands.serving", "HF_HUB_OFFLINE"),
)


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in _TRUTHY


def hub_cache_dir() -> str:
    """HF hub 缓存根，按 huggingface_hub 的优先级解析（不 import HF）。"""
    if os.environ.get("HF_HUB_CACHE"):
        return os.environ["HF_HUB_CACHE"]
    if os.environ.get("HF_HOME"):
        return os.path.join(os.environ["HF_HOME"], "hub")
    if os.environ.get("XDG_CACHE_HOME"):
        return os.path.join(os.environ["XDG_CACHE_HOME"], "huggingface", "hub")
    return os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")


def is_model_cached(model_name: str, cache_dir: str | None = None) -> bool:
    """`models--<org>--<name>/snapshots/<rev>` 存在即视为已缓存。"""
    repo_dir = os.path.join(cache_dir or hub_cache_dir(),
                            "models--" + model_name.replace("/", "--"))
    snapshots = os.path.join(repo_dir, "snapshots")
    if not os.path.isdir(snapshots):
        return False
    try:
        return any(
            os.path.isdir(os.path.join(snapshots, name)) for name in os.listdir(snapshots)
        )
    except OSError:
        return False


def _patch_loaded_hf_modules() -> list[str]:
    """把已 import 的 HF 模块里的 offline 判定副本改成 `True`（#46 运行时兜底）。

    返回被改写的 `模块.属性` 列表（供日志 / 诊断）。未加载的模块跳过——它们 import 时
    会读到已置位的环境变量，无需处理。
    """
    patched = []
    for module_name, attr in _LOADED_OFFLINE_BINDINGS:
        module = sys.modules.get(module_name)
        if module is None or getattr(module, attr, None) is True:
            continue
        setattr(module, attr, True)
        patched.append(f"{module_name}.{attr}")
    return patched


def ensure_hf_offline(model_names=None) -> bool:
    """目标模型全在本地缓存时，把进程切到离线模式。

    设计上应在 import HF **之前**调用；但依赖链可能已经先 import 了 HF（#46），故设完
    环境变量后再调用 `_patch_loaded_hf_modules()` 改写已加载常量，保证运行时兜底。
    `model_names` 省略时取 ragcore 全部本地模型（embed + rerank）。返回当前是否离线。
    """
    if any(name in os.environ for name in _OFFLINE_ENV_VARS):
        return any(_env_flag(name) for name in _OFFLINE_ENV_VARS)

    names = list(model_names or [LOCAL_EMBEDDING_MODEL, LOCAL_RERANKER_MODEL])
    missing = [n for n in names if not is_model_cached(n)]
    if missing:
        logger.warning(
            "HF hub offline mode NOT enabled: %s not cached; cold start may call "
            "huggingface.co (slow / may hang on weak network). Pre-download the model "
            "or set HF_HUB_OFFLINE=1 to force offline.",
            ", ".join(missing),
        )
        return False

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    patched = _patch_loaded_hf_modules()
    logger.info(
        "HF hub offline mode enabled (all models cached): %s%s",
        ", ".join(names),
        f"; patched already-imported: {', '.join(patched)}" if patched else "",
    )
    return True
