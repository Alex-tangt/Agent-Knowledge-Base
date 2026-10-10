# M5.4 复盘 · 进程外串行真模型嵌入下的干净位置恢复

- 结论：**PASS**（M5.4 改判 PASS，条件：嵌入提供方必须是进程外）
- 生成时间：2026-10-10T15:45:31+0800
- 嵌入：进程外真模型 `embed_server.py` → `http://127.0.0.1:8791/v1`（同一个 GGUF，所有 llama 调用共用一把锁）
- 候选配置键：`embedding.dense.provider=openai` / `api_base` / `model=bge-small-zh-v1.5-f16` / `dimension=512` / `input=text` / `encoding_format=float`

| 检查 | 结论 |
|---|---|
| M5B.1-reference-ok 进程外嵌入下参照实例可读写、快照与回滚正常（对照 M5.1/M5.2） | **PASS** |
| M5B.2-backup-both-faces 进程外嵌入下备份仍同时覆盖内容面与检索面 | **PASS** |
| M5B.3-clean-room-restore-outofprocess 用进程外串行真模型嵌入，干净位置恢复走通（原文/引用/权限/损失范围四项） | **PASS** |

## 与 task-1 原始 FAIL 的关系

task-1 的 M5.4 FAIL 证据**原样保留、不淡化**：候选自带的 **in-process** 本地 GGUF 嵌入器
在并发访问同一 llama.cpp 上下文时触发原生 `GGML_ASSERT` 并让整进程退出
（`evidence/local_embedder_crash.md`，退出码 3221226505），导致 `restore`/`import` 走不完。
本次把**同一个模型搬到进程外并串行化**后，同样的恢复流程走通 —— 说明卡点是**嵌入器实现**，不是备份/恢复设计。

## 复现

```powershell
$env:SPIKE_PROFILE='authz'
python probe_outofprocess_embedder.py    # 先证进程外嵌入在 10 并发下存活
python m5b_outofprocess_restore.py       # 再跑干净位置恢复
```
