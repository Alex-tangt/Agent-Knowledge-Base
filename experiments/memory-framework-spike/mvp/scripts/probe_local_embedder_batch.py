"""Probe: is batched embedding (list input) the crashing path?

    python probe_local_embedder_batch.py [batch_size]
"""

import sys

from llama_cpp import Llama

MODEL = r"C:\Users\Tan\.cache\openviking\models\bge-small-zh-v1.5-f16.gguf"
n = int(sys.argv[1]) if len(sys.argv) > 1 else 8

llm = Llama(model_path=MODEL, embedding=True, n_ctx=512, verbose=False)
print(f"model loaded; batch_size={n}", flush=True)

texts = [f"批量嵌入测试条目 {i}：中文内容。" * (1 + i % 3) for i in range(n)]
out = llm.create_embedding(texts)
print("dims:", [len(d["embedding"]) for d in out["data"]], flush=True)
print("BATCH PROBE COMPLETED", flush=True)
