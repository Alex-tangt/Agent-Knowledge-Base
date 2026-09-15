"""RerankerService 可配序列长度上限（issue #28）。

不加载真实模型：monkeypatch `CrossEncoder`，只断言 init 参数与排序行为。
"""
import pytest

import services.reranker_service as reranker_module
from config import config as core_config


class FakeCrossEncoder:
    calls: list = []

    def __init__(self, model_name, **kwargs):
        FakeCrossEncoder.calls.append((model_name, kwargs))
        self.max_seq_length = kwargs.get("max_length", 8192)

    def predict(self, pairs, **kwargs):
        return [float(i) for i in range(len(pairs))]


@pytest.fixture
def spy(monkeypatch):
    FakeCrossEncoder.calls = []
    monkeypatch.setattr(reranker_module, "CrossEncoder", FakeCrossEncoder)
    return FakeCrossEncoder


def test_explicit_max_seq_length_passed(spy):
    service = reranker_module.RerankerService("some-model", max_seq_length=512)
    assert spy.calls[-1] == (
        "some-model",
        {"trust_remote_code": True, "local_files_only": True, "max_length": 512},
    )
    assert service.model.max_seq_length == 512


def test_none_max_seq_length_omits_kwarg(spy):
    reranker_module.RerankerService("some-model", max_seq_length=None)
    assert "max_length" not in spy.calls[-1][1]


def test_default_max_seq_length_comes_from_config(spy):
    reranker_module.RerankerService("some-model")
    assert spy.calls[-1][1]["max_length"] == core_config.RERANK_MAX_SEQ_LENGTH


def test_default_model_name_comes_from_config(spy):
    reranker_module.RerankerService()
    assert spy.calls[-1][0] == core_config.LOCAL_RERANKER_MODEL


def test_rerank_sorts_desc_and_truncates(spy):
    service = reranker_module.RerankerService("m", max_seq_length=128)
    ranked = service.rerank("q", ["a", "b", "c"], top_k=2)
    assert [doc for _, doc in ranked] == ["c", "b"]


def test_rerank_empty_documents(spy):
    service = reranker_module.RerankerService("m", max_seq_length=128)
    assert service.rerank("q", []) == []


def test_config_env_override(monkeypatch):
    monkeypatch.setenv("RERANK_MAX_SEQ_LENGTH", "256")
    assert core_config._rerank_max_seq_length(512) == 256


def test_config_env_disable_falls_back_to_model_default(monkeypatch):
    for raw in ("", "none", "off", "0"):
        monkeypatch.setenv("RERANK_MAX_SEQ_LENGTH", raw)
        assert core_config._rerank_max_seq_length(512) is None


def test_config_env_unset_uses_default(monkeypatch):
    monkeypatch.delenv("RERANK_MAX_SEQ_LENGTH", raising=False)
    assert core_config._rerank_max_seq_length(512) == 512
