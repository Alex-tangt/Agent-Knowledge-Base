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
"""
from __future__ import annotations

import os

from ragcore.config.config import LOCAL_EMBEDDING_MODEL, LOCAL_RERANKER_MODEL
from ragcore.utils.logger import logger

_OFFLINE_ENV_VARS = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
_TRUTHY = {"1", "true", "yes", "on"}


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


def ensure_hf_offline(model_names=None) -> bool:
    """目标模型全在本地缓存时，于 import HF 前把进程切到离线模式。

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
    logger.info("HF hub offline mode enabled (all models cached): %s", ", ".join(names))
    return True
