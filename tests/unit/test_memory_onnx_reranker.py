"""ONNX 重排器（#35）：不加载真实模型——注入 fake onnxruntime session / tokenizer。

真实 ONNX 权重（jina int8）不在 CI 保证存在，故这里只断言**接线与排序逻辑**：
- 分数 = sigmoid(logit)，按降序取 top_k；
- 分批不丢候选；
- 权重解析缺文件且不联网时显式报错；
- `default_reranker_factory` 按 `MEMORY_RERANK_BACKEND` 选后端。
"""
import math
import types

import pytest

import memory_agent.memory.onnx_reranker as onnx_mod


class FakeTokenizer:
    def __init__(self):
        self.max_length = 1024

    @classmethod
    def from_file(cls, path):
        return cls()

    def enable_truncation(self, max_length=1024):
        self.max_length = max_length

    def enable_padding(self, **kwargs):
        pass

    def encode_batch(self, pairs):
        lengths = [len(doc) for _, doc in pairs]
        widest = max(lengths) if lengths else 0
        out = []
        for n in lengths:
            out.append(types.SimpleNamespace(
                ids=[1] * n + [0] * (widest - n),
                attention_mask=[1] * n + [0] * (widest - n),
            ))
        return out


class FakeInput:
    def __init__(self, name):
        self.name = name


class FakeSession:
    def __init__(self, path, sess_options=None, providers=None):
        self.path = path

    def get_inputs(self):
        return [FakeInput("input_ids"), FakeInput("attention_mask")]

    def run(self, outputs, feed):
        # 与 onnxruntime 同形：返回「输出列表」，单个输出为 (batch, 1)。
        return [[[float(sum(row))] for row in feed["input_ids"]]]


@pytest.fixture
def fake_runtime(monkeypatch, tmp_path):
    """让 OnnxReranker 用 fake runtime 构造（不碰真实 onnxruntime / HF 缓存）。"""
    monkeypatch.setattr(
        onnx_mod, "resolve_model_file",
        lambda repo, filename, allow_download=False: str(tmp_path / filename))
    import onnxruntime
    import tokenizers

    monkeypatch.setattr(onnxruntime, "InferenceSession", FakeSession)
    monkeypatch.setattr(
        tokenizers.Tokenizer, "from_file",
        classmethod(lambda cls, path: FakeTokenizer()))
    return tmp_path


def test_rerank_orders_by_sigmoid_logit_desc(fake_runtime):
    reranker = onnx_mod.OnnxReranker("m")
    ranked = reranker.rerank("q", ["x", "xxx", "xx"], top_k=3)
    assert [doc for _, doc in ranked] == ["xxx", "xx", "x"]
    assert ranked[0][0] == pytest.approx(1.0 / (1.0 + math.exp(-3.0)))


def test_rerank_top_k_truncates(fake_runtime):
    reranker = onnx_mod.OnnxReranker("m")
    assert len(reranker.rerank("q", ["a", "bb", "ccc"], top_k=2)) == 2


def test_rerank_empty_documents(fake_runtime):
    assert onnx_mod.OnnxReranker("m").rerank("q", []) == []


def test_batching_keeps_all_candidates(fake_runtime):
    reranker = onnx_mod.OnnxReranker("m", batch_size=2)
    docs = ["", "a", "bb", "ccc", "dddd"]
    ranked = reranker.rerank("q", docs, top_k=5)
    assert [doc for _, doc in ranked] == ["dddd", "ccc", "bb", "a", ""]


def test_resolve_model_file_missing_without_download():
    with pytest.raises(FileNotFoundError):
        onnx_mod.resolve_model_file("some/repo-not-in-cache-xyz", "onnx/model.onnx")


def test_factory_selects_onnx_backend(monkeypatch, fake_runtime):
    import memory_agent.settings as settings

    monkeypatch.setattr(settings, "RERANK_BACKEND", "onnx")
    from memory_agent.memory.retrieval import default_reranker_factory

    assert isinstance(default_reranker_factory(), onnx_mod.OnnxReranker)


def test_factory_defaults_to_torch_backend(monkeypatch):
    import memory_agent.settings as settings
    import ragcore.services.reranker_service as reranker_module

    monkeypatch.setattr(settings, "RERANK_BACKEND", "torch")
    calls = []
    monkeypatch.setattr(
        reranker_module, "RerankerService",
        lambda name, **kwargs: calls.append(name) or object())

    from memory_agent.memory.retrieval import default_reranker_factory

    default_reranker_factory()
    assert calls == [settings.RERANK_MODEL]
