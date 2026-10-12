# 支撑证据 · 进程外串行嵌入在 10 并发客户端下存活

- 结论：**PASS**
- 模型：`C:\Users\Tan\.cache\openviking\models\bge-small-zh-v1.5-f16.gguf`（与被 abort 的 in-process 路径**同一个 GGUF**）
- 服务：`embed_server.py`，`127.0.0.1:8791`，OpenAI 兼容 `/v1/embeddings`，所有 llama 调用共用一把锁（一次一条/一批）

```json
{
  "port": 8791,
  "health": {
    "status": "ok",
    "calls": 0,
    "items": 0,
    "serialized": true,
    "model": "bge-small-zh-v1.5-f16"
  },
  "single": {
    "dims": 512,
    "count": 1
  },
  "batch": {
    "dims": [
      512,
      512,
      512,
      512,
      512,
      512,
      512,
      512
    ]
  },
  "concurrency": {
    "clients": 10,
    "requests_each": 5,
    "total_requests": 50,
    "errors": [],
    "error_count": 0,
    "seconds": 0.3,
    "server_alive_after": true,
    "final_health": {
      "status": "ok",
      "calls": 52,
      "items": 59,
      "serialized": true,
      "model": "bge-small-zh-v1.5-f16"
    }
  },
  "verdict": "PASS"
}
```

对照（task-1 已录）：同一模型 **in-process 10 线程并发直接 abort 进程**（退出码 3221226505）；
本探针把同一模型搬到进程外并串行化后，同样的 10 并发客户端全部成功，服务存活。
