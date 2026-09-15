"""HF hub 缓存感知离线开关（#18）。

`ensure_hf_offline()` 在 import HF 前决定是否切离线：模型全在缓存 → `HF_HUB_OFFLINE=1`
（+ `TRANSFORMERS_OFFLINE=1`）；有缺失 → 保持联网（首次下载仍可用）；显式设置的环境变量
一律不覆盖。
"""
import os

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
