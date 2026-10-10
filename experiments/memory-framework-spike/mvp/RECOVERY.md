# 恢复操作手册（干净位置恢复）· 0.4.23 实测

适用：把个人记忆基座从「备份包 + 配置 + 公开恢复命令」在**全新的隔离位置**恢复起来。
依据实测：M5（[evidence/m5_backup.md](evidence/m5_backup.md)）与 M5.4 复盘（[evidence/m5b_outofprocess_restore.md](evidence/m5b_outofprocess_restore.md)）。

## 0. 一句话前提（必须满足，否则恢复走不完）

**嵌入提供方必须在进程外，且对同一模型串行。** 候选自带的 in-process 本地 GGUF 嵌入器在并发下会让
服务**整进程原生 abort**（`GGML_ASSERT`，Windows 退出码 3221226505），`restore`/`import` 会在
批量嵌入阶段死掉。实测把**同一个 GGUF** 搬到进程外并串行化后，同样的恢复流程走通。

## 1. 恢复能恢复什么

| 面 | 是否随包恢复 | 说明 |
|---|---|---|
| 内容面 | **是**（逐字节） | ovpack `files/`，恢复到原 URI 路径 |
| 检索面 | **是**（需 `--include-vectors`） | `_ovpack/index_records.jsonl` + `_ovpack/dense.f32`；不加该开关包内没有 dense 快照，检索面只能 `--vector-mode recompute` 重建 |
| 权限（ACL）记录 | **否** | 实测恢复后 `acl_mode` 仍是 `inherit`、无 direct entries → **只读策略必须在恢复位置重新施加** |
| 身份 / API key 注册表 | **否** | 实测：只在参照实例存在的探针用户 `m5b-probe` **没有**出现在恢复后的干净位置；干净位置恢复前用户列表为空 |
| 快照历史 | 是（基座侧） | `ov snapshot log --branch main --paths <uri>`；`ov snapshot restore <commit> <dir>` 做前向回滚 |

## 2. 精确步骤（实测命令）

```powershell
# ---- A. 备份（参照实例侧）------------------------------------------------
# 0) 向量索引是异步刷新的；若报 "Cannot export incomplete OpenViking vector index snapshot"，
#    先 reindex 再重试（本条实测遇到并已被脚本自动处理）
ov reindex viking://resources --mode vectors_only --wait true
ov backup D:\backup\ov-<date>.ovpack --include-vectors      # 整账户公开范围
#    范围包（只备份某个项目子树，恢复更快、嵌入工作量更小）：
ov export viking://resources/<proj> D:\backup\proj-<date>.ovpack --include-vectors

# ---- B. 干净位置实例（全新 workspace + 配置，端口独立）--------------------
#   ov-ref/ov-clean.conf 的关键键（无 BOM）：
#     storage.workspace                       -> 新目录
#     embedding.dense.provider                = "openai"
#     embedding.dense.api_base                = "http://127.0.0.1:8791/v1"
#     embedding.dense.model                   = "bge-small-zh-v1.5-f16"
#     embedding.dense.dimension               = 512
#     embedding.dense.input                   = "text"
#     embedding.dense.encoding_format         = "float"
#     server.auth_mode                        = "api_key"
#     server.root_api_key                     = "$OPENVIKING_SPIKE_ROOT_KEY"   # 值走进程环境
openviking-server.exe --config <ov-clean.conf> --host 127.0.0.1 --port <port>

# ---- C. 进程外串行嵌入（同一个 GGUF，OpenAI 兼容）------------------------
python embed_server.py --port 8791            # 所有 llama 调用共用一把锁

# ---- D. 先重建身份（必须；`ov restore` 不接受 --sudo）---------------------
#   CLI 原文：『--sudo 只支持 admin、system、reindex、task status 和 task list 命令』
#   数据面身份：POST /api/v1/admin/accounts/default/users {user_id, role, seed}   （root key 调管理面）
#   然后用该身份写 ovcli.conf 的 api_key

# ---- E. 恢复 --------------------------------------------------------------
ov restore D:\backup\ov-<date>.ovpack --on-conflict overwrite --vector-mode auto -o json
#   或范围包： ov import D:\backup\proj-<date>.ovpack viking://resources --on-conflict overwrite --vector-mode auto

# ---- F. 重建权限（必须；ACL 不随包恢复）-----------------------------------
#   账户级开关默认关闭，必须先打开，否则 restricted/grant 完全不生效：
#     PATCH /api/v1/admin/accounts/default/settings {"acl":{"enabled":true}}
#   注意：ACL 不能落在命名空间根（viking://resources 被拒 400 "ACL cannot be set on viking://resources"），
#         必须施加到具体子树：
#     PUT  /api/v1/acl            {"uri":"viking://resources/<proj>","acl_mode":"restricted"}
#     POST /api/v1/acl/grant      {"principal":"user:<reader>","level":"read"}
#     POST /api/v1/acl/grant      {"principal":"user:<writer>","level":"write"}

# ---- G. 验收（四项缺一不可）-----------------------------------------------
#   1) 原文逐字节： GET /api/v1/content/read?uri=<uri>&raw=true  sha256 == 备份时刻登记值
#   2) 检索面：     ov grep <marker> -u viking://resources   命中；取回该引用的字节 == 恢复后正文
#   3) 权限：       只读身份读 200、写 403 PERMISSION_DENIED；范围外私有命名空间读 403
#   4) 损失范围：   备份点之后写入的条目在恢复后**不存在**（实测）
#   5) **封存（必做）**：核验一结束，立刻把恢复出来的正文与「检索面返回的字节」另存为不可变副本
#      （本次 <spike-root>/m5b/sealed/{restored-content.md,retrieval-returned-bytes.md,reference-v1-input.md}），
#      记录 sha256；**此后不要再往被恢复的那条路径写任何东西**。
#      权限类写探针一律打 scratch 路径（如 .../note/m5b-writeprobe.md），**不要打在被恢复的正文上**
#      —— 否则落盘物被覆盖，第三方按路径复算会得到与证据不一致的哈希（本轮踩过）。
#      复算： python verify_seal.py
```

## 3. 可恢复时间点与数据损失范围

- **恢复点**：① 工作区快照提交（`ov snapshot commit`，可逐文件 `snapshot show --path` 回看）；② 备份包生成时刻（全量公开范围，覆盖式恢复）。两者都是**离散点**。
- **粒度/保留**：快照保留在账户快照历史里，不依赖人整理；包保留在文件系统上（本次 169,895 B / 含向量）。系统**不内置**定时备份或快照。
- **会丢**：最后一次恢复点之后到故障时刻的全部写入；两次快照之间写入的内容（没有连续日志，**不能回到任意时刻**）。
- **不会丢**：恢复点之前的正文（逐字节）；源实例本身的数据（备份不删除源数据）。
- **重建代价**：不带 `--include-vectors` 时检索面要按全量语料重新嵌入（本机 47 MB 小模型、单线程串行；语料越大越贵）。

## 4. 恢复做不到什么（明确登记，不留隐含）

1. **不能回到任意时刻** —— 只有离散快照点与备份包，无连续日志。
2. **身份/API key 注册表不在包内** —— 必须在新位置重建身份；重建用同 seed 可得到同一 principal 字符串，但这是**手工步骤**。
3. **ACL 记录不在包内** —— 只读策略必须重新施加（且账户级开关默认关闭）。
4. **命名空间根不能加 ACL** —— `viking://resources` 级只读做不到，必须落到具体子树。
5. **`ov restore` 不接受 `--sudo`** —— 恢复动作需要先在目标位置存在一个数据面身份。

## 5. 若换环境仍遇嵌入崩溃

- 现象：服务日志出现 `GGML_ASSERT(...) failed`，进程消失，HTTP 全部连接失败（消费侧表现为「服务不可用」）。
- 已定位触发条件：**并发**访问同一个 Llama 上下文（空输入、56000 字符超长、批量 32 条、加锁串行都不触发）。证据 [evidence/local_embedder_crash.md](evidence/local_embedder_crash.md)。
- 已验证的规避：把模型放到进程外并串行（[embed_server.py](scripts/embed_server.py)，OpenAI 兼容 `/v1/embeddings`），候选侧 `embedding.dense.provider=openai` 指过去。证据 [evidence/outofprocess_embedder.md](evidence/outofprocess_embedder.md)。
- 只改 `embedding.max_concurrent=1` **不足**（实测）。
