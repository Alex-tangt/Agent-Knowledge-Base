"""Probe: is the local GGUF embedder safe under concurrent use?

OpenViking's default `embedding.max_concurrent` is 10 and the local provider
shares one llama.cpp context. A native GGML_ASSERT aborts the whole process, so
this must run standalone.

    python probe_local_embedder_concurrency.py serial|concurrent [n]
"""

import sys
import threading

from llama_cpp import Llama

MODEL = r"C:\Users\Tan\.cache\openviking\models\bge-small-zh-v1.5-f16.gguf"
mode = sys.argv[1] if len(sys.argv) > 1 else "concurrent"
n = int(sys.argv[2]) if len(sys.argv) > 2 else 10

llm = Llama(model_path=MODEL, embedding=True, n_ctx=512, verbose=False)
print(f"model loaded; mode={mode} threads={n}", flush=True)

errors: list[str] = []
lock = threading.Lock()


def worker(i: int) -> None:
    for k in range(20):
        text = f"并发嵌入测试条目 {i}-{k}；中文内容用于触发多序列批处理。" * (1 + i % 5)
        try:
            if mode == "serial":
                with lock:
                    out = llm.create_embedding(text)
            else:
                out = llm.create_embedding(text)
            dim = len(out["data"][0]["embedding"])
            if dim != 512:
                errors.append(f"bad dim {dim}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")


threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
for t in threads:
    t.start()
for t in threads:
    t.join()

print("errors:", errors[:5], "count:", len(errors), flush=True)
print("PROBE COMPLETED", flush=True)
