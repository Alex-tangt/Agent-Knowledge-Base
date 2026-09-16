"""#46 A/B：HF 已被依赖链先 import 时，`ensure_hf_offline()` 的运行时兜底是否仍生效。

复现 #46 的证据形态：`qdrant_client` 先于产品开关把 `huggingface_hub` import 进来，
`huggingface_hub.constants.HF_HUB_OFFLINE` 这个 **import 期常量** 就被定死为 `False`。
在**不可达** HF endpoint + 无 shell env 下走产品服务层加载 BGE-M3：

- `fixed`：正常顺序——`memory_agent.runtime` 的 `configure_hf_offline()` 触发运行时兜底，
  改写已加载常量 → **0 次外呼**、加载成功。
- `legacy`：把兜底 patch 打成 no-op（模拟 #46 修复**前**的代码）→ 常量仍是 `False` →
  sentence_transformers 照发元数据请求 → 不可达时报错（`ValueError`）。

用法（工作树里需 PYTHONPATH 指向工作树）：
    python experiments/hf-offline-warmup/probe_46_runtime_fallback.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

PROXY_VARS = (
    "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy",
)


def _child(mode: str) -> int:
    import logging
    import time

    logging.basicConfig(
        level=logging.INFO,
        format="%(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("huggingface_hub").setLevel(logging.DEBUG)

    for name in PROXY_VARS:
        os.environ.pop(name, None)
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)
    os.environ["HF_ENDPOINT"] = "http://10.255.255.1"  # 不可达：模拟弱网 / 代理挂
    os.environ["HF_HUB_ETAG_TIMEOUT"] = "3"
    os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "3"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

    # 外呼计数：huggingface_hub 的 session 基于 requests.HTTPAdapter；离线时挂的是
    # OfflineAdapter（在 send 之前抛错、不计），联网时挂 UniqueRequestIdAdapter → 计。
    import requests.adapters

    counter = {"n": 0}
    _orig_send = requests.adapters.HTTPAdapter.send

    def _counting_send(self, request, *args, **kwargs):
        counter["n"] += 1
        return _orig_send(self, request, *args, **kwargs)

    requests.adapters.HTTPAdapter.send = _counting_send

    # 依赖链先 import HF：这正是 #46 的空操作条件。
    import qdrant_client  # noqa: F401
    if mode == "legacy":
        import ragcore.config.hf as hf

        hf._patch_loaded_hf_modules = lambda: []  # 模拟修复前：运行时兜底不存在

    import memory_agent.runtime  # noqa: F401  （fixed 下此处触发兜底 patch）
    import huggingface_hub.constants as constants

    t0 = time.perf_counter()
    try:
        from ragcore.services.local_embedding_service import LocalEmbeddingService

        LocalEmbeddingService()
        payload = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    payload.update({
        "mode": mode,
        "elapsed_s": round(time.perf_counter() - t0, 1),
        "HF_HUB_OFFLINE": constants.HF_HUB_OFFLINE,
        "external_hf_calls": counter["n"],
    })
    print(json.dumps(payload, ensure_ascii=False), file=sys.stdout, flush=True)
    return 0


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--child":
        return _child(sys.argv[2])

    rows = []
    for mode in ("fixed", "legacy"):
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--child", mode],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        line = next(
            (l for l in reversed(proc.stdout.splitlines()) if l.strip().startswith("{")),
            "{}",
        )
        row = json.loads(line)
        rows.append(row)
        print(f"[{mode}] {json.dumps(row, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
