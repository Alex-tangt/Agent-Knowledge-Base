# M5 · 版本、备份与恢复有效

- 结论：**FAIL**
- 生成时间：2026-10-10T15:24:12+0800
- 运行 profile：`authz`（端口 8781）

| 检查 | 结论 |
|---|---|
| M5.1-versions-identifiable 历史版本可识别（快照提交可列举、可回看单个文件） | **PASS** |
| M5.2-historical-restore 按公开方式恢复历史版本，内容面与检索面同时生效 | **PASS** |
| M5.3-backup-covers-both-faces 备份同时覆盖内容面与检索面两处存储 | **PASS** |
| M5.4-clean-room-restore 在干净的隔离位置恢复：原文、引用与权限符合登记的恢复语义 | **FAIL** |
| M5.5-reference-intact 参照实例（主隔离实例）在 clean-room 演练后未被破坏 | **PASS** |
| M5.6-restore-blocker-characterised 已定位并登记阻断干净位置恢复的原因（本地嵌入并发原生崩溃） | **PASS** |

## 运行期健壮性观察

- 本地 GGUF 嵌入在本次演练中触发原生断言并**整进程退出**（`GGML_ASSERT(i01 >= 0 && i01 < ne01) failed`，llama.cpp CPU kernel），已自动重启继续测量；重启次数 = 1。
- 影响：该路径下的嵌入崩溃不是可捕获的 Python 异常，会让服务整体不可用（对消费者表现为 M4 的『服务不可用』）。

## 可恢复时间点与数据损失范围

```json
{
  "recovery_points": [
    {
      "kind": "workspace snapshot commit",
      "ref": "242f2da0cc1eb3b39426ba4cdc90cbcb6206b408",
      "message": "M5 v1",
      "granularity": "显式提交时的工作区状态；可逐文件回看（ov snapshot show --path）",
      "retention": "保留在账户快照历史（ov snapshot log），不依赖人工整理；未测保留上限"
    },
    {
      "kind": "workspace snapshot commit",
      "ref": "99d3732372aa80d9b8e6c8d7e227ef1d446c4e0b",
      "message": "M5 v2",
      "granularity": "同上；一次 commit = 一个可恢复点",
      "retention": "同上"
    },
    {
      "kind": "backup package",
      "path": "C:\\Users\\Tan\\.openviking-mvp-spike\\m5-artifacts\\m5-backup-with-vectors.ovpack",
      "created_at": "2026-10-10T15:21:37+0800",
      "sha256": "e967872a5657666cd78049d184b00bb7d45126f512515f843d926b99e3fb318e",
      "covers": "内容面 files/ + 检索面 _ovpack/index_records.jsonl + dense.f32",
      "granularity": "包生成时刻的全量公开范围；恢复是覆盖式（--on-conflict overwrite）"
    },
    {
      "kind": "backup package (content only)",
      "path": "C:\\Users\\Tan\\.openviking-mvp-spike\\m5-artifacts\\m5-backup-content-only.ovpack",
      "sha256": "4281932cf87a6ba0a50512ba1bc439d198daac390f8e6343c0c6d96e7a21d48f",
      "covers": "仅内容面；检索面需 --vector-mode recompute 重建（成本另计）"
    }
  ],
  "data_loss_range": {
    "from": "最后一次快照提交 / 备份包生成时刻",
    "to": "故障时刻",
    "lost": [
      "备份点之后、下次备份之前的全部写入（实测：备份后写入的条目在 clean-room 恢复后不存在）",
      "两次快照提交之间写入的内容（快照点是离散的，没有连续日志/时间点恢复）"
    ],
    "not_lost": [
      "备份点之前的正文（恢复后逐字节相同；本次校验 sha256 已记录）",
      "主实例自身数据（备份不删除源数据；演练后主实例内容仍在）"
    ],
    "post_backup_entry_sha256": "ca9ba77009f49233c3ca6cfc75c5d04e38be5c3e1b149b7071bfd96ffcbd39cf",
    "recovery_point_interval": "由触发频率决定，系统不自动调度（无定时备份/快照机制内置）"
  },
  "what_restore_cannot_do": [
    "无法表达『回到任意时刻』：没有连续日志，只有离散快照点与备份包",
    "备份包不携带身份/API key 注册表（实测 users 在恢复前为空），必须在新位置重新登记消费者",
    "备份包不携带 ACL 记录（实测 acl_mode 未随包恢复），只读策略必须在恢复位置重新施加",
    "身份与 ACL 这两项只能表达为『恢复步骤』，不能在包内表达"
  ],
  "restore_entry_points": {
    "used_in_this_run": "ov backup / ov restore（CLI，--include-vectors、--on-conflict、--vector-mode）",
    "http_routes_present_in_source": [
      "POST /api/v1/pack/export",
      "POST /api/v1/pack/backup",
      "POST /api/v1/pack/import",
      "POST /api/v1/pack/restore"
    ],
    "note": "HTTP 形态在 0.4.23 的 pack 路由中存在，但本轮未实测；本轮结论只覆盖 CLI 形态。"
  },
  "retrieval_rebuild_option": "ov restore --vector-mode recompute（无 dense 快照时重建索引）",
  "local_curation_required": false
}
```

## 两个存储面

- 内容面：`<workspace>/viking`（AGFS）→ ovpack `files/`
- 检索面：`<workspace>/vectordb` → ovpack `_ovpack/index_records.jsonl` + `_ovpack/dense.f32`
- **`--include-vectors` 必须显式加上**，否则包内没有 dense 快照，检索面只能靠 `--vector-mode recompute` 重建

## 复现

```powershell
$env:SPIKE_PROFILE='authz'; python spike_server.py start
python m5_backup.py
```
