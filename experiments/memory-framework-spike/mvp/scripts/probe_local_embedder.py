"""Probe: does the local GGUF embedder crash on empty / oversized input?

Run as a standalone process -- a native GGML_ASSERT aborts the whole process,
which is exactly the failure observed in the clean-room restore.

    python probe_local_embedder.py empty|long|normal
"""

import sys

from llama_cpp import Llama

MODEL = r"C:\Users\Tan\.cache\openviking\models\bge-small-zh-v1.5-f16.gguf"

mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
llm = Llama(model_path=MODEL, embedding=True, n_ctx=512, verbose=False)
print("model loaded; mode =", mode, flush=True)

if mode == "empty":
    inputs = ["", "x"]
elif mode == "long":
    inputs = ["这是一个很长的中文测试句子。" * 4000]
else:
    inputs = ["短期记忆检索测试条目"]

for i, text in enumerate(inputs):
    print(f"input[{i}] chars={len(text)}", flush=True)
    out = llm.create_embedding(text)
    vec = out["data"][0]["embedding"]
    print(f"  ok dim={len(vec)}", flush=True)

print("ALL OK", flush=True)
