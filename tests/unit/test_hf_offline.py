"""HF hub 缓存感知离线开关（#18 / #46）。

`ensure_hf_offline()` 在 import HF 前决定是否切离线：模型全在缓存 → `HF_HUB_OFFLINE=1`
（+ `TRANSFORMERS_OFFLINE=1`）；有缺失 → 保持联网（首次下载仍可用）；显式设置的环境变量
一律不覆盖。

#46 回归：`huggingface_hub.constants.HF_HUB_OFFLINE` 是 import 期常量，依赖链可能已先把
HF import 进来——此时只设 env 是空操作。`ensure_hf_offline()` 须同时改写已加载的常量副本。
"""
import os
import sys
import types

from ragcore.config import hf


def _make_snapshot(cache_root, model_name, rev="abc123"):
    path = os.path.join(
        cache_root, "models--" + model_name.replace("/", "--"), "snapshots", rev
    )
    os.makedirs(path, exist_ok=True)


def _clear_offline(monkeypatch):
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        monkeypatch.delenv(name, raising=False)


# ------------------------------------------------------------- cache 解析

def test_hub_cache_dir_prefers_hf_hub_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    assert hf.hub_cache_dir() == str(tmp_path)


def test_hub_cache_dir_honours_hf_home(monkeypatch, tmp_path):
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    assert hf.hub_cache_dir() == os.path.join(str(tmp_path), "hub")


def test_is_model_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    assert hf.is_model_cached("org/model") is False
    _make_snapshot(str(tmp_path), "org/model")
    assert hf.is_model_cached("org/model") is True


# -------------------------------------------------------------- 开关决策

def test_enables_offline_when_all_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _clear_offline(monkeypatch)
    _make_snapshot(str(tmp_path), "org/a")
    _make_snapshot(str(tmp_path), "org/b")

    assert hf.ensure_hf_offline(["org/a", "org/b"]) is True
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_stays_online_when_a_model_is_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _clear_offline(monkeypatch)
    _make_snapshot(str(tmp_path), "org/a")

    assert hf.ensure_hf_offline(["org/a", "org/missing"]) is False
    assert "HF_HUB_OFFLINE" not in os.environ
    assert "TRANSFORMERS_OFFLINE" not in os.environ


def test_explicit_off_is_respected(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    _make_snapshot(str(tmp_path), "org/a")

    assert hf.ensure_hf_offline(["org/a"]) is False
    assert os.environ["HF_HUB_OFFLINE"] == "0"


def test_explicit_on_is_respected_even_without_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)

    assert hf.ensure_hf_offline(["org/missing"]) is True


def test_transformer_offline_var_alone_is_respected(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    assert hf.ensure_hf_offline(["org/missing"]) is True
    assert "HF_HUB_OFFLINE" not in os.environ


# ------------------------------------------- 运行时兜底（#46：HF 已被先 import）

def test_runtime_fallback_when_hf_already_imported(monkeypatch, tmp_path):
    """核心回归：依赖链已 import 过 HF、常量已定死 False，仍能切到离线。"""
    import huggingface_hub.constants as hub_constants

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _clear_offline(monkeypatch)
    _make_snapshot(str(tmp_path), "org/a")
    assert "huggingface_hub.constants" in sys.modules
    monkeypatch.setattr(hub_constants, "HF_HUB_OFFLINE", False)

    assert hf.ensure_hf_offline(["org/a"]) is True
    assert hub_constants.HF_HUB_OFFLINE is True


def test_missing_model_keeps_constant_online(monkeypatch, tmp_path):
    import huggingface_hub.constants as hub_constants

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _clear_offline(monkeypatch)
    _make_snapshot(str(tmp_path), "org/a")
    monkeypatch.setattr(hub_constants, "HF_HUB_OFFLINE", False)

    assert hf.ensure_hf_offline(["org/a", "org/missing"]) is False
    assert hub_constants.HF_HUB_OFFLINE is False


def test_explicit_off_does_not_flip_loaded_constant(monkeypatch, tmp_path):
    import huggingface_hub.constants as hub_constants

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    _make_snapshot(str(tmp_path), "org/a")
    monkeypatch.setattr(hub_constants, "HF_HUB_OFFLINE", False)

    assert hf.ensure_hf_offline(["org/a"]) is False
    assert hub_constants.HF_HUB_OFFLINE is False


# ------------------------------------------- 构造器须允许首次下载（#53）

def _reload_with_offline(monkeypatch, module, hf_mod, offline):
    import importlib

    monkeypatch.setattr(hf_mod, "ensure_hf_offline", lambda *a, **k: offline)
    importlib.reload(module)
    return module


def test_embedding_service_allows_download_when_model_missing(monkeypatch):
    """回归：未缓存时 `_OFFLINE=False`，构造器才能联网下载 BGE-M3（否则新机必挂）。"""
    from ragcore.config import hf as hf_mod
    from ragcore.services import local_embedding_service as les

    try:
        _reload_with_offline(monkeypatch, les, hf_mod, False)
        assert les._OFFLINE is False
    finally:
        importlib = __import__("importlib")
        importlib.reload(les)


def test_reranker_service_allows_download_when_model_missing(monkeypatch):
    from ragcore.config import hf as hf_mod
    from ragcore.services import reranker_service as rs

    try:
        _reload_with_offline(monkeypatch, rs, hf_mod, False)
        assert rs._OFFLINE is False
    finally:
        importlib = __import__("importlib")
        importlib.reload(rs)


def test_patch_rewrites_loaded_module_copies(monkeypatch):
    """按实际 import 图改写：`transformers._is_offline_mode` 这类缓存副本也要覆盖。"""
    import huggingface_hub.constants as hub_constants

    monkeypatch.setattr(hub_constants, "HF_HUB_OFFLINE", True)  # 源头已在线，聚焦缓存副本
    fake_hub = types.ModuleType("transformers.utils.hub")
    fake_hub._is_offline_mode = False
    monkeypatch.setitem(sys.modules, "transformers.utils.hub", fake_hub)

    patched = hf._patch_loaded_hf_modules()

    assert fake_hub._is_offline_mode is True
    assert "transformers.utils.hub._is_offline_mode" in patched
