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

import pytest

from ragcore.config import hf


@pytest.fixture(autouse=True)
def _isolate_hf_state(monkeypatch):
    """每个用例前清掉离线 env + 重置 `_SELF_SET`（模块级状态会跨用例泄漏）。"""
    monkeypatch.setattr(hf, "_SELF_SET", False)
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        monkeypatch.delenv(name, raising=False)
    yield


def _write(path, *names):
    for name in names:
        with open(os.path.join(path, name), "w", encoding="utf-8") as handle:
            handle.write("{}")


def _make_snapshot(cache_root, model_name, rev="abc123", *, complete=True):
    path = os.path.join(
        cache_root, "models--" + model_name.replace("/", "--"), "snapshots", rev
    )
    os.makedirs(path, exist_ok=True)
    if complete:  # #53：只有含标记文件的 snapshot 才算「已缓存」
        _write(path, "config.json", "pytorch_model.bin", "tokenizer.json")
    return path


def _clear_offline(monkeypatch):
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(hf, "_SELF_SET", False)


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


def test_is_model_cached_rejects_empty_snapshot(monkeypatch, tmp_path):
    """#53：只建目录（中断下载残留）不算已缓存。"""
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _make_snapshot(str(tmp_path), "org/model", complete=False)
    assert hf.is_model_cached("org/model") is False


def test_is_model_cached_requires_tokenizer(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    path = _make_snapshot(str(tmp_path), "org/model", complete=False)
    _write(path, "config.json", "pytorch_model.bin")  # 缺 tokenizer
    assert hf.is_model_cached("org/model") is False


def test_is_model_cached_requires_weights(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    path = _make_snapshot(str(tmp_path), "org/model", complete=False)
    _write(path, "config.json", "tokenizer.json")  # 缺权重
    assert hf.is_model_cached("org/model") is False


def test_is_model_cached_requires_config(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    path = _make_snapshot(str(tmp_path), "org/model", complete=False)
    _write(path, "pytorch_model.bin", "tokenizer.json")  # 缺 config
    assert hf.is_model_cached("org/model") is False


def test_is_model_cached_accepts_safetensors_and_sentencepiece(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    path = _make_snapshot(str(tmp_path), "org/model", complete=False)
    _write(path, "config.json", "model.safetensors", "sentencepiece.bpe.model")
    assert hf.is_model_cached("org/model") is True


def _write_ref(cache_root, model_name, rev):
    path = os.path.join(cache_root, "models--" + model_name.replace("/", "--"), "refs")
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "main"), "w", encoding="utf-8") as handle:
        handle.write(rev)


def test_is_model_cached_follows_main_ref_even_if_another_snapshot_is_complete(
        monkeypatch, tmp_path):
    """#53 核心：main 指向半成品时，旁边有完整 snapshot 也不能算已缓存。"""
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _make_snapshot(str(tmp_path), "org/model", rev="good")           # 完整
    _make_snapshot(str(tmp_path), "org/model", rev="bad", complete=False)
    _write_ref(str(tmp_path), "org/model", "bad")
    assert hf.is_model_cached("org/model") is False


def test_is_model_cached_main_ref_complete(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _make_snapshot(str(tmp_path), "org/model", rev="good")
    _write_ref(str(tmp_path), "org/model", "good")
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


def test_missing_model_clears_self_set_offline(monkeypatch, tmp_path):
    """#53：embed 已缓存先置离线，随后 reranker 缺失必须能撤销离线以允许下载。"""
    import huggingface_hub.constants as hub_constants

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _clear_offline(monkeypatch)
    _make_snapshot(str(tmp_path), "org/embed")
    monkeypatch.setattr(hub_constants, "HF_HUB_OFFLINE", False)

    assert hf.ensure_hf_offline(["org/embed"]) is True
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert hub_constants.HF_HUB_OFFLINE is True

    # 另一个模型缺失 → 撤销自置离线（env + 已加载常量都要回 False）
    assert hf.ensure_hf_offline(["org/embed", "org/missing"]) is False
    assert "HF_HUB_OFFLINE" not in os.environ
    assert "TRANSFORMERS_OFFLINE" not in os.environ
    assert hub_constants.HF_HUB_OFFLINE is False


def test_user_set_offline_not_cleared_by_missing_model(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    monkeypatch.setattr(hf, "_SELF_SET", False)

    assert hf.ensure_hf_offline(["org/missing"]) is True
    assert os.environ["HF_HUB_OFFLINE"] == "1"  # 用户设置不被撤销


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

def _make_fake_st(seen):
    class _FakeST:
        def __init__(self, name, local_files_only=True):
            seen["local_files_only"] = local_files_only

        def get_embedding_dimension(self):
            return 1024

    return _FakeST


def test_embedding_service_allows_download_when_model_missing(monkeypatch):
    """回归：未缓存时构造器传 `local_files_only=False`，新机才能下 BGE-M3。"""
    from ragcore.services import local_embedding_service as les

    seen = {}
    monkeypatch.setattr(les, "SentenceTransformer", _make_fake_st(seen))
    monkeypatch.setattr(les, "ensure_hf_offline", lambda names=None: False)
    les.LocalEmbeddingService("BAAI/bge-m3")
    assert seen == {"local_files_only": False}


def test_embedding_service_reads_locally_when_cached(monkeypatch):
    from ragcore.services import local_embedding_service as les

    seen = {}
    monkeypatch.setattr(les, "SentenceTransformer", _make_fake_st(seen))
    monkeypatch.setattr(les, "ensure_hf_offline", lambda names=None: True)
    les.LocalEmbeddingService("BAAI/bge-m3")
    assert seen == {"local_files_only": True}


def test_reranker_service_allows_download_when_model_missing(monkeypatch):
    from ragcore.services import reranker_service as rs

    seen = {}

    class _FakeCE:
        def __init__(self, name, **kwargs):
            seen.update(kwargs)
            self.max_seq_length = kwargs.get("max_length", 8192)

    monkeypatch.setattr(rs, "CrossEncoder", _FakeCE)
    monkeypatch.setattr(rs, "ensure_hf_offline", lambda names=None: False)
    rs.RerankerService("BAAI/bge-reranker-v2-m3", max_seq_length=512)
    assert seen["local_files_only"] is False


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
