# M1 · 应用输入原样保存、跨消费者共享

- 结论：**PASS**
- 生成时间：2026-10-10T14:25:28+0800
- 引用：`viking://user/default/resources/mvp-spike/2026-10-10/note/m1-app-submit.md`
- 输入：`D:\python_work\work2026-4\wk-spike\experiments\memory-framework-spike\mvp\payload\m1-app-submit.md` · 555 bytes · sha256 `2d5f97940eca9f3c4261a2a7cc4daec48d26b5c5b0cdc5e7d97cb0badc3143e1`
- 环境：隔离实例 http://127.0.0.1:8781（本地 GGUF embedding，无云模型）

| 检查 | 结论 | 关键证据 |
|---|---|---|
| M1.1-submit 公开入口提交成功且报告写入字节数 | **PASS** | `{}` |
| M1.2-disk-bytes 磁盘上存下来的字节 == 提交输入字节 | **PASS** | `{"disk_sha256": "2d5f97940eca9f3c4261a2a7cc4daec48d26b5c5b0cdc5e7d97cb0badc3143e1", "byte_identical": true}` |
| M1.3-api-bytes 写/读接口取回的正文 == 提交输入字节 | **PASS** | `{"bytes": 555, "sha256": "2d5f97940eca9f3c4261a2a7cc4daec48d26b5c5b0cdc5e7d97cb0badc3143e1", "byte_identical": true}` |
| M1.4-download-bytes 原始字节下载端点 == 提交输入字节 | **PASS** | `{"bytes": 555, "sha256": "2d5f97940eca9f3c4261a2a7cc4daec48d26b5c5b0cdc5e7d97cb0badc3143e1", "byte_identical": true}` |
| M1.5-second-consumer 第二消费者（独立进程 ov CLI）读同一引用得到同一正文 | **PASS** | `{"bytes": 555, "sha256": "2d5f97940eca9f3c4261a2a7cc4daec48d26b5c5b0cdc5e7d97cb0badc3143e1", "byte_identical": true}` |
| M1.6-survives-restart 服务重启后同一引用仍读出同一正文 | **PASS** | `{"restart_seconds": 32.4, "bytes": 555, "sha256": "2d5f97940eca9f3c4261a2a7cc4daec48d26b5c5b0cdc5e7d97cb0badc3143e1", "byte_identical": true}` |
| M1.7-duplicate-no-second-entry 同一请求重复提交不产生第二条 | **PASS** | `{"entries_after": 1, "body_unchanged_after_duplicate": true}` |

## 两处原文比对

- 磁盘：`C:\Users\Tan\.openviking-mvp-spike\data\viking\default\user\default\resources\mvp-spike\2026-10-10\note\m1-app-submit.md` → 555 bytes，sha256 `2d5f97940eca9f3c4261a2a7cc4daec48d26b5c5b0cdc5e7d97cb0badc3143e1`，与输入逐字节相同：True
- 接口：`GET /api/v1/content/read?raw=true` → 555 bytes，sha256 `2d5f97940eca9f3c4261a2a7cc4daec48d26b5c5b0cdc5e7d97cb0badc3143e1`，与输入逐字节相同：True
- 原始字节端点：`GET /api/v1/content/download` → 555 bytes，逐字节相同：True

## 附带处理与成本

```json
{
  "semantic_status": "complete",
  "vector_status": "complete",
  "written_bytes": 555,
  "embedding_items_processed": 3,
  "semantic_items_processed": 1,
  "cloud_model_calls": 0,
  "cloud_model_cost": 0.0,
  "note": "embedding provider = local GGUF (bge-small-zh-v1.5-f16); no VLM provider is configured, so the semantic/overview step completes without any model call and writes '[Directory overview is not ready]' placeholders. 原文保存与读取不经过 LLM。"
}
```

## 复现

```powershell
. ./spike-env.ps1
python spike_server.py start
python m1_roundtrip.py --reset
```
