"""单一子进程：加载一个模型并计时，把 HF/httpx 的请求日志打到 stderr。

用法（由 probe_hf_offline.py 以子进程方式调用）：
    python load_probe.py embed|rerank

环境由父进程注入，本脚本不读任何项目配置——只观察「local_files_only=True 下
是否仍发外部请求」。
"""
import logging
import os
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(name)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
logging.getLogger("httpx").setLevel(logging.INFO)
logging.getLogger("huggingface_hub").setLevel(logging.DEBUG)

which = sys.argv[1] if len(sys.argv) > 1 else "embed"

t0 = time.perf_counter()
try:
    if which == "embed":
        from sentence_transformers import SentenceTransformer

        SentenceTransformer("BAAI/bge-m3", local_files_only=True)
    elif which == "rerank":
        from sentence_transformers import CrossEncoder

        CrossEncoder(
            "BAAI/bge-reranker-v2-m3",
            trust_remote_code=True,
            local_files_only=True,
            max_length=512,
        )
    elif which == "svc_embed":
        # 走产品服务层：其内部应在 import HF 前调用 ensure_hf_offline（#18 修复验收）
        from ragcore.services.local_embedding_service import LocalEmbeddingService

        LocalEmbeddingService()
    elif which == "svc_rerank":
        from ragcore.services.reranker_service import RerankerService

        RerankerService()
    else:
        raise SystemExit(f"unknown target: {which}")
    print(f"PROBE_RESULT ok\t{which}\t{time.perf_counter() - t0:.2f}s", file=sys.stderr)
except Exception as exc:  # noqa: BLE001
    print(
        f"PROBE_RESULT fail\t{which}\t{time.perf_counter() - t0:.2f}s\t{type(exc).__name__}: {exc}",
        file=sys.stderr,
    )
    raise SystemExit(3)
