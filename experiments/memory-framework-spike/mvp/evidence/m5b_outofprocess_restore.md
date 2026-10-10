# M5.4 复盘 · 进程外串行真模型嵌入下的干净位置恢复

- 结论：**PASS**（M5.4 改判 PASS，条件：嵌入提供方必须是进程外）
- 生成时间：2026-10-10T16:00:41+0800
- 嵌入：进程外真模型 `embed_server.py` → `http://127.0.0.1:8791/v1`（同一个 GGUF，所有 llama 调用共用一把锁）
- 候选配置键：`embedding.dense.provider=openai` / `api_base` / `model=bge-small-zh-v1.5-f16` / `dimension=512` / `input=text` / `encoding_format=float`

## 更正说明（证据完整性修复）

- **问题**：第一次复盘（独立复算由 Lead 发现）里，`content_sha256_after_restore` 与 `retrieval_face.served_sha256_of_first_hit` 都是**写探针之前**测的；随后那次写探针**打在被恢复的正文路径上**并成功落盘，把落盘物覆盖成 27 B，而证据里没有任何字段记录这次写入。
- **为何算硬伤**：哈希断言在当时成立，但落盘物随后被覆盖 → 验收方按路径去比对会得到不一致，对『备份可恢复』这条必须项是硬伤。
- **原因**：写探针是在**只读策略生效之前**用只读身份发的（账户 ACL 默认关闭 / restricted 尚未施加），因此那次写**成功**，落到被恢复的正文路径上。属测试设计缺陷，不是恢复语义问题。
- **修复**：
  - 核验一结束就把『恢复出来的正文』与『检索面返回的字节』另存为不可变副本（m5b/sealed/），并把 sha256 记进证据；封存后再次复算校验。
  - 所有写探针改打在 scratch 路径 `…/note/m5b-writeprobe.md`，**不再触碰被恢复的正文路径**。
  - 证据新增 `artifact_immutability`（核验后再次读取被恢复路径，必须与核验时逐字节相同）与 `write_probe_timeline`（每次写探针的身份/目标/状态码），使『恢复 → 核验 → 后续写入』时序无歧义。
  - 本文件同轮重跑，落盘物与封存副本一致；不再有任何写入落在被恢复路径上。

- **封存副本 sha256**（第三方可直接对文件复算）：

```json
{
  "restored_content": "c4dfc7ac258fd5875cfc2a3f0759d62dd03eab385e29267e93f325797eee6874",
  "retrieval_returned": "c4dfc7ac258fd5875cfc2a3f0759d62dd03eab385e29267e93f325797eee6874",
  "reference_input": "c4dfc7ac258fd5875cfc2a3f0759d62dd03eab385e29267e93f325797eee6874",
  "pack": "b05ccf653ef93aada3091c76ebd17e6bc959a22b7f301d6bad9be18e46cc5fc2"
}
```

- **仓内封存副本**（不依赖本机隔离目录，可直接离线复算）：

  - `experiments/memory-framework-spike/mvp/evidence/sealed/restored-content.md` sha256 `c4dfc7ac258fd5875cfc2a3f0759d62dd03eab385e29267e93f325797eee6874`
  - `experiments/memory-framework-spike/mvp/evidence/sealed/retrieval-returned-bytes.md` sha256 `c4dfc7ac258fd5875cfc2a3f0759d62dd03eab385e29267e93f325797eee6874`
  - `experiments/memory-framework-spike/mvp/evidence/sealed/reference-v1-input.md` sha256 `c4dfc7ac258fd5875cfc2a3f0759d62dd03eab385e29267e93f325797eee6874`
- 一键复算：`python scripts/verify_seal.py`（对落盘物与仓内副本分别算哈希并与本文件断言比对）

- **未改动**：任务1 的原始 FAIL 证据 evidence/m5_backup.json 一字未改；M5.4 的条件化 PASS 结论不变（修的是证据时序，不是结论）

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
