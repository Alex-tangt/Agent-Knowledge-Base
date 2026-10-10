# M2 · 既有资料只读接入并追平更新

- 结论：**PASS**（逐项见下表与 `m2_source.json`）
- 生成时间：2026-10-10T14:38:31+0800
- 运行 profile：`authz`（api_key 认证；dev profile 恒为 ROLE=root，无法表达只读身份）
- 来源目录：`C:\Users\Tan\.openviking-mvp-spike\source-repo`（git，提交号 `c9fb9d5d5884`）
- 基座侧引用：`viking://resources/wk-spike-source/source-repo`
- 就地挂载入口：**不存在**（本地路径 watch 被框架显式拒绝）

| 检查 | 结论 |
|---|---|
| M2.1-no-in-place-watch 本地目录能否就地挂载/持续追平（有就地入口 → 才可能『指过去』） | **PASS** |
| M2.2-ingest-copy 基座读取来源副本（导入快照） | **PASS** |
| M2.3-copy-not-pointer 形态判定：导入 = 拷一份 | **PASS** |
| M2.3b-readonly-policy 登记只读策略（ACL restricted + 只读身份 read 授权） | **PASS** |
| M2.4-copy-fidelity 基座侧正文与来源文件逐字节相同（未被解析器改写） | **PASS** |
| M2.5-source-untouched 接入前后来源目录文件清单与 git 提交号一致 | **PASS** |
| M2.6-write-to-source-refused 通过基座写『该来源』被拒绝（只读身份） | **PASS** |
| M2.7-reimport-is-not-an-update 重复 add-resource 是否更新同一引用（否则产生第二份正文） | **PASS** |
| M2.7b-staleness-signal-probe 实测并登记『快照相对来源过期』是否有任何信号 | **PASS** |
| M2.8-update-read-new-content 外部改动后按登记的更新机制读到新正文（同一引用的新版本，多入口一致） | **PASS** |
| M2.9-no-writeback 基座不向来源目录回写（来源仍是权威） | **PASS** |

## 形态（拷一份 vs 指过去）

- 结论：拷贝（导入快照），不是『指过去』
- 证据：
  - 框架对本地路径 watch 显式拒绝，并指示 re-import
  - 导入后基座侧 workspace 下出现独立正文（与来源文件逐字节相同）
  - 重复导入生成兄弟目录 _1（第二份正文），不是原地更新
  - 来源目录文件清单与 git 提交号在接入前后一致，且基座从不回写
- 后果：更新必须显式触发（单向推送）；无自动追平；步骤/延迟/成本已登记

## 更新机制（测试前登记）

- 登记机制：**单向逐文件推送** `ov write <uri> --from-file <来源文件> --wait`（源目录为权威，基座侧不回流）
- 框架自述：Client error: A local path cannot be watched. Use a URL, sitemap, or RSS source, or re-import the local content when it changes.
- 自动追平延迟：**无**；实际延迟 = 显式触发时刻

## 陈旧信号（需求相关，Lead 指定补测）

- 实测：`framework_can_report_staleness = False`，`base_still_serves_pre_change_bytes = True`
- 结论：源文件在磁盘上被外部改动后，基座**继续返回旧快照**，且 `stat` / `attrs` 不带任何来源哈希或来源 mtime，
  本地来源又无 watch → **读到旧快照没有任何信号**。已登记为已知限制（见下）。

## 已知限制（需求相关，必须显式登记）

### stale-snapshot-is-silent

- 事实：本地来源没有就地挂载也没有 watch。源文件被外部改动后，基座继续返回旧快照，且没有任何信号（无来源指纹/版本字段/漂移检查入口）表明它已过期。
- 影响：需求里的『读到的是旧快照』目前不可被系统或消费者发现，只能由外部自行比对来源哈希。
- 证据：checks[M2.7b-staleness-signal-probe]
- 今天可用的兜底：由调用方在登记时刻记录来源文件哈希，事后自行比对；基座侧无内建机制

## 成本

```json
{
  "first_ingest_wall_seconds": 1.78,
  "ingest_embedding_items": 9,
  "ingest_semantic_items": 1,
  "update_wall_seconds": 0.93,
  "update_embedding_items": 3,
  "cloud_model_calls": 0,
  "cloud_model_cost": 0.0,
  "storage_overhead": "基座侧另存一份正文（拷贝）；来源目录体积不变",
  "operator_maintenance": [
    "首次导入：ov add-resource <本地目录> --parent-auto-create <uri> --wait",
    "只读策略：账户 ACL 开关 + 目标 restricted + 只读身份 read 授权（见 M2.3b）",
    "来源变更：逐文件 ov write <uri> --from-file <来源文件> --wait（无自动追平）"
  ]
}
```

## 复现

```powershell
$env:SPIKE_PROFILE='authz'; python spike_server.py start
python m2_source.py --reset
```
