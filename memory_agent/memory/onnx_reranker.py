"""ONNX 交叉编码器重排器（**memory 作用域**：#35 / ADR-0022 D8/D10）。

为什么不用 `optimum`：ADR-0022 当时记录 jina int8 ONNX 要经 `sentence-transformers`
的 onnx backend，而它依赖 `optimum[onnxruntime]`——实测会把 `transformers` 5.5.4
降到 4.57.6、`huggingface_hub` 1.31 → 0.36（**整仓副作用**）。本模块直接用已经装好的
`onnxruntime` + `tokenizers` 跑导出的 ONNX 图，**零新依赖、不动 transformers**。

jina-reranker-v2 的 ONNX 图输入 = `input_ids` + `attention_mask`，输出 = `logits (batch,1)`；
pair 由 tokenizer 的 post-processor 拼成 `<s> q </s> </s> d </s>`。分数取 sigmoid(logit)
（单调，排序与原始 logit 一致）。

权重解析**优先走 HF 缓存**（`try_to_load_from_cache`，离线可用）；缺文件且
`allow_download=True` 才联网拉取。
"""
from __future__ import annotations

import math
import os
from typing import Sequence

DEFAULT_ONNX_FILE = "onnx/model_int8.onnx"
DEFAULT_TOKENIZER_FILE = "tokenizer.json"
DEFAULT_MAX_LENGTH = 1024
DEFAULT_BATCH = 16


def resolve_model_file(repo_id: str, filename: str, *, allow_download: bool = False) -> str:
    """在 HF 缓存里定位单个文件；缓存没有且允许联网时才 `hf_hub_download`。

    返回本地绝对路径。缓存未命中且不允许联网 → `FileNotFoundError`（带可操作提示）。
    """
    from huggingface_hub import try_to_load_from_cache

    cached = try_to_load_from_cache(repo_id, filename)
    if isinstance(cached, str) and os.path.isfile(cached):
        return cached
    if not allow_download:
        raise FileNotFoundError(
            f"{repo_id}/{filename} 不在本地 HF 缓存里。先联网拉一次（设 "
            f"MEMORY_RERANK_ALLOW_DOWNLOAD=1），或预置该文件到 HF 缓存。"
        )
    from huggingface_hub import hf_hub_download

    return hf_hub_download(repo_id, filename)


class OnnxReranker:
    """与 `ragcore.services.reranker_service.RerankerService` 同形接口的 ONNX 重排器。

    `rerank(query, documents, top_k)` → 按分数**降序**的 `[(score, document)]`。
    """

    def __init__(self, model_name: str, *, onnx_file: str = DEFAULT_ONNX_FILE,
                 tokenizer_file: str = DEFAULT_TOKENIZER_FILE,
                 max_length: int = DEFAULT_MAX_LENGTH, batch_size: int = DEFAULT_BATCH,
                 threads: int | None = None, allow_download: bool = False):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.model_name = model_name
        self.max_length = int(max_length)
        self.batch_size = max(1, int(batch_size))

        onnx_path = resolve_model_file(model_name, onnx_file, allow_download=allow_download)
        tokenizer_path = resolve_model_file(
            model_name, tokenizer_file, allow_download=allow_download)

        options = ort.SessionOptions()
        if threads is not None:
            options.intra_op_num_threads = max(1, int(threads))
        self._session = ort.InferenceSession(
            onnx_path, sess_options=options, providers=["CPUExecutionProvider"])
        self._input_names = {i.name for i in self._session.get_inputs()}

        self._tokenizer = Tokenizer.from_file(tokenizer_path)
        self._tokenizer.enable_truncation(max_length=self.max_length)
        self._tokenizer.enable_padding()

    @staticmethod
    def _sigmoid(x: float) -> float:
        if x >= 0:
            return 1.0 / (1.0 + math.exp(-x))
        exp_x = math.exp(x)
        return exp_x / (1.0 + exp_x)

    def rerank(self, query: str, documents: Sequence[str],
               top_k: int = 5) -> list[tuple[float, str]]:
        if not documents:
            return []
        scores: list[float] = []
        for start in range(0, len(documents), self.batch_size):
            batch = list(documents[start:start + self.batch_size])
            encodings = self._tokenizer.encode_batch([(query, doc) for doc in batch])
            ids = [enc.ids for enc in encodings]
            mask = [enc.attention_mask for enc in encodings]
            feed = {}
            if "input_ids" in self._input_names:
                feed["input_ids"] = ids
            if "attention_mask" in self._input_names:
                feed["attention_mask"] = mask
            logits = self._session.run(None, feed)[0]
            scores.extend(self._sigmoid(float(row[0])) for row in logits)

        ranked = sorted(zip(scores, documents), key=lambda item: item[0], reverse=True)
        return ranked[:top_k]
