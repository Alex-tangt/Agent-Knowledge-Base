# 个人记忆基座 MVP · 阶段 1–3 实测（M0–M5）

依据：[mvp-protocol.md](../mvp-protocol.md)（验收来源，归 Lead，本目录不改它）。
分支：`feat/memory-mvp-spike` · worktree：`D:\python_work\work2026-4\wk-spike`。

## 一句话结论

候选（OpenViking 0.4.23）**能**原样保存与读回原文、跨消费者共享、按 ACL 做范围授权、显式报告不可用与处理失败、做版本快照与「内容面+检索面」双面备份；**不能**就地挂载本地来源（只能导入快照，且读到旧快照无任何信号），**且**本机干净位置恢复被本地嵌入的并发原生崩溃阻断（M5.4 FAIL）。

## 逐项结论

| 项 | 结论 | 证据 |
|---|---|---|
| M0 能部署并启动 | **PASS** | [m0_env.md](evidence/m0_env.md) |
| M1 原文原样保存、跨消费者共享 | **PASS** | [m1_roundtrip.md](evidence/m1_roundtrip.md) |
| M2 既有资料只读接入并追平更新 | **PASS**（快照形态，已登记更新机制/延迟/成本） | [m2_source.md](evidence/m2_source.md) |
| M3 路径范围与授权一致 | **PASS** | [m3_authz.md](evidence/m3_authz.md) |
| M4 不可用与处理失败可见 | **PASS** | [m4_failure.md](evidence/m4_failure.md) |
| M5 版本、备份与恢复有效 | **FAIL**（M5.1/2/3/5/6 PASS；M5.4 干净位置恢复被嵌入崩溃阻断） | [m5_backup.md](evidence/m5_backup.md) |
| M6 真实 agent 接入与成本 | **未测**（不在本线范围） | — |

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

## 阻断 M5.4 的原因（已定位，独立复现）

本地 GGUF 嵌入（llama-cpp-python 0.3.36 + `bge-small-zh-v1.5-f16`）在**并发**访问同一 Llama 上下文时触发原生 `GGML_ASSERT` 并**整进程退出**（Windows 退出码 `3221226505`）。逐条排除：空输入、56000 字符超长输入、批量 32 条、10 线程加锁串行**全部正常**；只有并发崩溃。

- 复现证据：[local_embedder_crash.md](evidence/local_embedder_crash.md)（7 个探针的退出码）
- 影响：`ov restore` / `ov import` 这类批量嵌入工作会在恢复过程中 abort 服务，干净位置恢复走不完。候选默认 `embedding.max_concurrent=10`；clean-room 配置改成 1 仍不足。
- 建议：框架侧给本地嵌入调用加互斥（或串行化 embedding worker）后重测；或换进程外/HTTP 嵌入提供方。

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
python m5_backup.py
python validate_evidence.py
```

隔离：数据与配置全在 `%USERPROFILE%\.openviking-mvp-spike\`（仓库外）；端口 8781；未触碰真实 KB、真实索引、8765 旧 daemon、`~/.openviking`。凭据只走进程环境/仓外文件，证据只记变量名与 principal，不含值。

## 目录

```
mvp/
  scripts/   可复现脚本（spike_common / spike_server / spike_identity / m0..m5 / probe_* / validate_evidence）
  payload/   测试输入（M1 提交原文）
  evidence/  m0..m5 与嵌入崩溃探针的 JSON + MD 证据（全部 UTF-8 无 BOM，validate_evidence.py 可校验）
```
