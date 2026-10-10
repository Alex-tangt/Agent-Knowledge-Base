# 个人记忆基座 MVP · 阶段 1–3 实测（M0–M5）

依据：[mvp-protocol.md](../mvp-protocol.md)（验收来源，归 Lead，本目录不改它）。
分支：`feat/memory-mvp-spike` · worktree：`D:\python_work\work2026-4\wk-spike`。

## 一句话结论

候选（OpenViking 0.4.23）**能**原样保存与读回原文、跨消费者共享、按 ACL 做范围授权、显式报告不可用与处理失败、做版本快照与「内容面+检索面」双面备份，并在**进程外串行嵌入**的前提下载干净位置恢复；**不能**就地挂载本地来源（只能导入快照，且读到旧快照无任何信号）；其**自带的 in-process 本地 GGUF 嵌入器并发不安全**（会整进程 abort，见「已登记限制」）。

## 逐项结论

| 项 | 结论 | 证据 |
|---|---|---|
| M0 能部署并启动 | **PASS** | [m0_env.md](evidence/m0_env.md) |
| M1 原文原样保存、跨消费者共享 | **PASS** | [m1_roundtrip.md](evidence/m1_roundtrip.md) |
| M2 既有资料只读接入并追平更新 | **PASS**（快照形态，已登记更新机制/延迟/成本） | [m2_source.md](evidence/m2_source.md) |
| M3 路径范围与授权一致 | **PASS** | [m3_authz.md](evidence/m3_authz.md) |
| M4 不可用与处理失败可见 | **PASS** | [m4_failure.md](evidence/m4_failure.md) |
| M5 版本、备份与恢复有效 | **PASS（条件化）** / 原始记录 **FAIL** | [m5_backup.md](evidence/m5_backup.md) + [m5b_outofprocess_restore.md](evidence/m5b_outofprocess_restore.md) |
| M6 真实 agent 接入与成本 | **未测**（不在本线范围） | — |

## M5.4 的两条记录都保留

- **原始 FAIL 记录（不删除、不淡化）**：[m5_backup.md](evidence/m5_backup.md) —— 用候选**自带的 in-process 本地 GGUF 嵌入器**时，干净位置恢复失败，服务在恢复过程中被原生 abort。
- **复盘结论（条件化 PASS）**：[m5b_outofprocess_restore.md](evidence/m5b_outofprocess_restore.md) ——
  把**同一个 GGUF** 搬到**进程外并串行**后，`ov restore --on-conflict overwrite --vector-mode auto`
  **一次成功**（服务全程存活，日志无 abort），四项验收全过：原文逐字节 == 备份版本、检索面 `grep`/`find`
  取回恢复后的版本字节、只读身份读 200 / 写 403 / 范围外私有命名空间 403、备份点之后写入的条目恢复后不存在。
- 结论：**M5.4 = PASS，条件 = 嵌入提供方必须是进程外**（见下方「已登记限制」）。
- 复盘还暴露了我自己的一个**测试命令错误**：`ov restore` 不接受 `--sudo`（CLI 原文：『--sudo 只支持
  admin、system、reindex、task status 和 task list 命令』）。task-1 后期几轮 M5.4 失败是
  「嵌入崩溃」与「`--sudo` 误用」两个原因叠加；两者都已修正并复测。
- **另一处测试设计缺陷（已修，证据补记）**：第一版复盘把写探针打在了**被恢复的正文路径**上，且发生在
  只读策略生效**之前**（账户 ACL 默认关闭），于是写**成功**并覆盖了落盘物（27 B），而证据里没有记录这次写入
  → 哈希断言当时成立、事后却无法由落盘物复算。现已改为：**核验后先封存**（`m5b/sealed/…` + sha256）、
  **写探针只打 scratch 路径**、证据新增 `artifact_immutability` 与 `write_probe_timeline`；
  `m5b_outofprocess_restore.md` 顶部有更正说明。第三方复算脚本 `scripts/verify_seal.py`：
  落盘物 == 封存副本 == 证据哈希（53 B / `c4dfc7ac…6874`）。
  封存副本同时放进 `evidence/sealed/`（附 `.gitattributes` 标记 `-text`，避免 `core.autocrlf=true`
  在重新检出时改成 CRLF 而改变字节；已实测提交后的 blob 逐字节 == 53 B 同上哈希）。

### 已登记限制（保留，不淡化）

> **OpenViking 0.4.23 自带的本地 in-process GGUF 嵌入器并发不安全。** 并发访问同一个 llama.cpp 上下文会触发原生
> `GGML_ASSERT` 并让**整个服务进程退出**（Windows 退出码 3221226505，Python 侧不可捕获），使 `ov restore` /
> `ov import` 这类批量嵌入工作在恢复过程中 abort。逐条排除空输入、56000 字符超长输入、批量 32 条、10 线程加锁串行
> ——只有并发触发；把 `embedding.max_concurrent` 设为 1 **不足以**规避。规避方式：嵌入提供方放**进程外**并串行
> （本次 [embed_server.py](scripts/embed_server.py)，同一个 GGUF，OpenAI 兼容）。
> 证据 [local_embedder_crash.md](evidence/local_embedder_crash.md) + [outofprocess_embedder.md](evidence/outofprocess_embedder.md)。

恢复步骤、需要重建的东西、可恢复时间点与数据损失范围：见 **[RECOVERY.md](RECOVERY.md)**。

## 四项附加检查

| 检查 | 结论 | 证据 |
|---|---|---|
| 原文比对看两处（磁盘字节 + 接口正文） | **PASS**：555 B 输入在**磁盘、`read?raw`、`download`、独立 CLI 进程**四处逐字节相同，sha256 `2d5f9794…43e1` | [m1_roundtrip.md](evidence/m1_roundtrip.md) |
| 形态：指过去 vs 拷一份 | **拷贝（导入快照）**：本地路径 watch 被框架显式拒绝；重复导入生成兄弟目录 `_1`（第二份正文）；来源目录文件清单与 git 提交号在接入前后一致，基座从不回写 | [m2_source.md](evidence/m2_source.md) |
| 同一请求重复提交不得产生第二条 | **PASS**：`mode=create` 幂等冲突返回 409 `ALREADY_EXISTS`；`mode=replace` 就地更新；条目数恒为 1。（**服务端写入请求无幂等键**——结论靠确定性 URI + 响应语义实测，不靠服务端幂等） | [m1_roundtrip.md](evidence/m1_roundtrip.md) |
| 备份/恢复必须覆盖内容面与检索面两处存储 | **PASS（备份侧）**：`ov backup --include-vectors` 产出 169,895 B 包，含 `_ovpack/index_records.jsonl` + `dense.f32`；不加 `--include-vectors` 只有 47,517 B 且无 dense 快照 | [m5_backup.md](evidence/m5_backup.md) |

## 三条必须带走的实测事实（需求相关）

1. **本地来源没有就地挂载**。框架对本地目录的 `--watch-interval` 显式拒绝（原文：“A local path cannot be watched. Use a URL, sitemap, or RSS source, or re-import the local content when it changes.”），`storage.agfs.backend` 只有 `local|s3|memory`（都是基座自身存储）。⇒ 形态是**导入快照**；更新机制 = 单向逐文件 `ov write <uri> --from-file`；**无自动追平**，延迟 = 显式触发时刻。
2. **读到旧快照没有任何信号**。源文件在磁盘上被外部改动后，基座继续返回旧快照，`stat`/`attrs` 不带来源哈希或来源 mtime，本地来源又无 watch。要发现陈旧只能由外部自行比对来源哈希。（已知限制，见 [m2_source.md](evidence/m2_source.md)）
3. **账户级 ACL 默认关闭**（`AclManager.is_enabled` 读账户设置 `acl.enabled`，默认 `false`）。不显式打开时 `acl_mode=restricted` 与 grant 记录照存但**完全不生效——只读身份照样能写**；打开后同一身份同一操作立刻变成 403 `PERMISSION_DENIED`。开关：`PATCH /api/v1/admin/accounts/default/settings {"acl":{"enabled":true}}`，文档落在 AGFS `<workspace>/viking/default/_system/setting.json` 的 `acl.enabled`。

## 原始 M5.4 阻断的成因与规避（已定位、独立复现、已验证规避）

本地 GGUF 嵌入（llama-cpp-python 0.3.36 + `bge-small-zh-v1.5-f16`）在**并发**访问同一 Llama 上下文时触发原生 `GGML_ASSERT` 并**整进程退出**（Windows 退出码 `3221226505`）。逐条排除：空输入、56000 字符超长输入、批量 32 条、10 线程加锁串行**全部正常**；只有并发崩溃。

- 复现证据：[local_embedder_crash.md](evidence/local_embedder_crash.md)（7 个探针的退出码）
- 影响：`ov restore` / `ov import` 这类批量嵌入工作会在恢复过程中 abort 服务，干净位置恢复走不完。候选默认 `embedding.max_concurrent=10`；改成 1 仍不足。
- **已验证的规避**：[outofprocess_embedder.md](evidence/outofprocess_embedder.md)（同一 GGUF 移到进程外串行，10 并发客户端全成功）→ 再用它跑 [m5b_outofprocess_restore.md](evidence/m5b_outofprocess_restore.md)，干净位置恢复一次成功。
- 建议：框架侧给本地嵌入调用加互斥（或串行化 embedding worker）后重测；否则把嵌入提供方固定在进程外。

## 运行方式

```powershell
cd experiments\memory-framework-spike\mvp\scripts
# 参照实例（dev 认证，M0/M1 用；ACL 不生效）
python spike_server.py start                       # 端口 8781，冷启动约 45 s
python m0_inventory.py
python m1_roundtrip.py --reset
# 授权线（api_key + 两名消费者，M2/M3/M4/M5 用）
$env:SPIKE_PROFILE='authz'
python spike_server.py restart
python m2_source.py --reset
python m3_authz.py --reset
python m4_failure.py
python probe_local_embedder_results.py
python m5_backup.py                 # 原始记录（in-process 嵌入 → M5.4 FAIL）
# M5.4 复盘：进程外串行真模型嵌入
python probe_outofprocess_embedder.py
python m5b_outofprocess_restore.py  # → M5.4 条件化 PASS
python validate_evidence.py
```

隔离：数据与配置全在 `%USERPROFILE%\.openviking-mvp-spike\`（仓库外）；服务端口 8781、进程外嵌入端口 8791；
未触碰真实 KB、真实索引、8765 旧 daemon、8766/8770 与其它线的 8782/8783。凭据只走进程环境/仓外文件，证据只记变量名与 principal，不含值。

## 目录

```
mvp/
  README.md    本文件（逐项结论 + 四条实测事实 + 已登记限制）
  RECOVERY.md  干净位置恢复操作手册（步骤 / 需重建什么 / 恢复点 / 损失范围 / 做不到什么）
  scripts/     可复现脚本
    spike_common.py spike_server.py spike_identity.py spike_embed.py   # 隔离实例、身份、进程外嵌入
    m0_inventory.py m1_roundtrip.py m2_source.py m3_authz.py m4_failure.py m5_backup.py
    m5b_outofprocess_restore.py                                        # M5.4 复盘
    embed_server.py                                                    # 进程外串行 OpenAI 兼容嵌入
    probe_local_embedder*.py probe_outofprocess_embedder.py            # 崩溃归因与规避验证
    validate_evidence.py
  payload/     测试输入（M1 提交原文）
  evidence/    m0..m5 + 嵌入崩溃 + 进程外嵌入 + M5.4 复盘的 JSON/MD（全部 UTF-8 无 BOM，validate_evidence.py 可校验）
```
