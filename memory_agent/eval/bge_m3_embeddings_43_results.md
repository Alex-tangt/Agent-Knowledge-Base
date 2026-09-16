# #43 验收结果：共享 daemon 暴露 BGE-M3 embeddings 端点

- 日期：2026-09-16 19:43:34
- 脚本：`memory_agent/eval/bge_m3_embeddings_43.py`
- 结论：**PASS**（17/17）
- 临时工作区：`C:\Users\Tan\AppData\Local\Temp\opencode\bge-m3-embeddings-43-1789558983`（临时 KB / 索引 / DeepTutor home + 独立端口；真 daemon 未动）

## PASS 矩阵

| # | 检查 | 结果 | 详情 |
|---|------|------|------|
| 1 | 一步安装（含 embedding profile）返回 0 | PASS |  |
| 2 | embedding profile 已设为 active | PASS | active=embedding-profile-bge-m3 |
| 3 | 保留 LLM profile 及其 key | PASS |  |
| 4 | 幂等（再次合并 changed=False 且不改字节） | PASS |  |
| 5 | daemon 冷启动 + 首次 embeddings 请求成功 | PASS | status=200 |
| 6 | OpenAI 形状 object=list/data[].embedding | PASS |  |
| 7 | dim=1024 | PASS | dim=1024 |
| 8 | 单位范数（normalize_embeddings=True） | PASS | norm=1.0000 |
| 9 | 批量输入返回等长向量 | PASS |  |
| 10 | model 回显请求值 | PASS |  |
| 11 | 非法 input → 400 | PASS | status=400 |
| 12 | 不支持的 encoding_format → 400 | PASS | status=400 |
| 13 | 无效 Bearer → 401 | PASS | status=401 |
| 14 | 无 token 配置 → 零配置直连 | PASS | status=200 |
| 15 | DeepTutor 解析为 local vllm + bge-m3（dim=1024） | PASS | binding=vllm mode=local dim=1024 |
| 16 | DeepTutor 真的取到 2×1024 向量 | PASS | [1024, 1024] |
| 17 | base_url 指向共享 daemon 的 /v1/embeddings | PASS | http://127.0.0.1:51710/v1/embeddings |

## 运行日志

```text
# issue #43 验收 @ 2026-09-16 19:43:03
    workdir = C:\Users\Tan\AppData\Local\Temp\opencode\bge-m3-embeddings-43-1789558983
    临时 daemon = http://127.0.0.1:51710（独立端口，不动真 daemon）

[1] connect.py 写 DeepTutor 配置（embedding profile + 保留 LLM）
  [PASS] 一步安装（含 embedding profile）返回 0
  [PASS] embedding profile 已设为 active — active=embedding-profile-bge-m3
  [PASS] 保留 LLM profile 及其 key
  [PASS] 幂等（再次合并 changed=False 且不改字节）

[2] 启动临时 daemon（venv 路径；验证 transformers 回归已修）
    daemon pid=28592
  [PASS] daemon 冷启动 + 首次 embeddings 请求成功 — status=200

[3] /v1/embeddings 契约
  [PASS] OpenAI 形状 object=list/data[].embedding
  [PASS] dim=1024 — dim=1024
  [PASS] 单位范数（normalize_embeddings=True） — norm=1.0000
  [PASS] 批量输入返回等长向量
  [PASS] model 回显请求值
  [PASS] 非法 input → 400 — status=400
  [PASS] 不支持的 encoding_format → 400 — status=400

[4] authn 与 /mcp 同源（零配置直连 + 无效 token 拒绝）
  [PASS] 无效 Bearer → 401 — status=401
  [PASS] 无 token 配置 → 零配置直连 — status=200

[5] 用 DeepTutor 自身 EmbeddingClient 端到端取向量
  [PASS] DeepTutor 解析为 local vllm + bge-m3（dim=1024） — binding=vllm mode=local dim=1024
  [PASS] DeepTutor 真的取到 2×1024 向量 — [1024, 1024]
  [PASS] base_url 指向共享 daemon 的 /v1/embeddings — http://127.0.0.1:51710/v1/embeddings

结论：PASS  (17/17)
```
