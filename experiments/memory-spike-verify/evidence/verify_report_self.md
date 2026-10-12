# 独立复核（自测）结果

- 阶段：`live`
- 生成时间：2026-10-10T15:43:13+0800
- 判定标准指纹（跑前预注册）：`17d18b7bbe842da6059b341a41319c11fa2824f30817aa191bc96802174f2f4d`
- 结果载荷指纹：`5730a1269e1bf409e25315aae0c9fe5ffc38f1376f70a800869f5760093db38c`
- 隔离实例：openviking-server (候选实现, 隔离实例) @ 127.0.0.1:8783

| 检查 | 判定 | 说明 |
|---|---|---|
| C1 | **PASS** | 原文往返：原样保存与读回（同时核磁盘字节与读取接口正文） |
| C2 | **FAIL** | 形态：接入的项目文件是「指过去」还是「拷一份进来」 |
| C3 | **FAIL** | 重复提交：同一请求提交两次不得产生第二条 |
| C4 | **INCONCLUSIVE** | 来源只读：接入前后源目录清单与提交号一致；通过基座写来源被拒 |
| C5 | **PASS** | 停机报错：停隔离服务后消费者收到显式不可用错误，而非静默空结果 |

## C1 — 原文往返：原样保存与读回（同时核磁盘字节与读取接口正文） → PASS
- **C1a**: PASS — 
  - 观测：`{"input_sha256": "7889b2ade8fd1767894ec397b5d88ab8befca137d13bc9c20853b71078dbd605", "raw_sha256": "7889b2ade8fd1767894ec397b5d88ab8befca137d13bc9c20853b71078dbd605"}`
- **C1b**: PASS — 磁盘/ git 对象中找到与输入逐字节相等的持久化正文
  - 观测：`{"disk_full_hits": 1, "disk_marker_hits": 1, "git_blobs": [], "git_examined": 0, "git_truncated": false, "files_scanned": 143}`
- **C1c**: PASS — 默认读回接口若与输入不等，M1 的『原文读回一致』不成立
  - 观测：`{"default_sha256": "7889b2ade8fd1767894ec397b5d88ab8befca137d13bc9c20853b71078dbd605", "default_equal": true}`
- **C1d**: PASS — 重启后原文仍可读回同一内容
  - 观测：`{"equal": true, "sha256": "7889b2ade8fd1767894ec397b5d88ab8befca137d13bc9c20853b71078dbd605"}`

## C2 — 形态：接入的项目文件是「指过去」还是「拷一份进来」 → FAIL
- **C2a**: FAIL — 外部改源文件后应在上限内读到新正文（PASS=指过去；FAIL=拷贝/快照未追平）
  - 观测：`{"window_s": 120, "propagated": false, "samples": [{"t": 0, "equal_to_source_bytes": false, "read_sha256": "5b687743ed4da48d8caf34ca519c8772f39d99323168101db0994f6d34d53953", "read_bytes": 78}, {"t": 10, "equal_to_source_bytes": false, "read_sha256": "5b687743ed4da48d8caf34ca519c8772f39d99323168101db0994f6d34d53953", "read_bytes": 78}, {"t": 20, "equal_to_source_bytes": false, "read_sha256": "5b687743ed4da48d8caf34ca519c8772f39d99323168101db0994f6d34d53953", "read_bytes": 78}, {"t": 30, "equal_to_source_bytes": false, "read_sha256": "5b687743ed4da48d8caf34ca519c8772f39d99323168101db0994f6d34d5`
- **C2b**: FAIL — workspace 内出现源文件正文的独立副本即判『拷贝』
  - 观测：`{"source_a": {"path": "C:\\Users\\Tan\\.openviking-mvp-verify\\source-tree\\a.md", "sha256": "5b687743ed4da48d8caf34ca519c8772f39d99323168101db0994f6d34d53953"}, "workspace_full_content_hits": [{"path": "C:\\Users\\Tan\\.openviking-mvp-verify\\data\\viking\\default\\resources\\verify-20261010T153910-e48bc7\\src\\a.md", "size": 78, "file_sha256": "5b687743ed4da48d8caf34ca519c8772f39d99323168101db0994f6d34d53953", "full_offset": 0}], "marker_hits": ["C:\\Users\\Tan\\.openviking-mvp-verify\\data\\viking\\default\\resources\\verify-20261010T153910-e48bc7\\src\\a.md", "C:\\Users\\Tan\\.openviking-m`
- **C2c**: FAIL — 同一源重复接入不应新增第二条（条目数 3 → 4）
  - 观测：`{"count_after_first": 3, "count_after_second": 4, "adds_succeeded": 2, "ls_succeeded": 2, "ls_after_1": ["viking://resources/verify-20261010T153910-e48bc7/dup-parent/source-tree", "viking://resources/verify-20261010T153910-e48bc7/dup-parent/.abstract.md", "viking://resources/verify-20261010T153910-e48bc7/dup-parent/.overview.md"], "ls_after_2": ["viking://resources/verify-20261010T153910-e48bc7/dup-parent/source-tree", "viking://resources/verify-20261010T153910-e48bc7/dup-parent/source-tree_1", "viking://resources/verify-20261010T153910-e48bc7/dup-parent/.abstract.md", "viking://resources/veri`

## C3 — 重复提交：同一请求提交两次不得产生第二条 → FAIL
- **C3a**: PASS — 同一 URI 同正文 write 两次（replace）后应只有 1 条
  - 观测：`{"name_count": 1, "ls": ["viking://user/default/verify/20261010T153910-e48bc7/idem/.abstract.md", "viking://user/default/verify/20261010T153910-e48bc7/idem/.overview.md", "viking://user/default/verify/20261010T153910-e48bc7/idem/replace-twice.md"]}`
- **C3b**: PASS — create 重复提交必须幂等或显式冲突，不得产生第二条
  - 观测：`{"name_count": 1, "second_rejected": true, "second_error": {"exception": "AlreadyExistsError", "module": "openviking_sdk.errors", "message": "File already exists: viking://user/default/verify/20261010T153910-e48bc7/idem/create-twice.md"}, "ls": ["viking://user/default/verify/20261010T153910-e48bc7/idem/.abstract.md", "viking://user/default/verify/20261010T153910-e48bc7/idem/.overview.md", "viking://user/default/verify/20261010T153910-e48bc7/idem/create-twice.md", "viking://user/default/verify/20261010T153910-e48bc7/idem/replace-twice.md"]}`
- **C3c**: PASS — 同一源 + 同一显式 to 重复提交不得新增条目
  - 观测：`{"entry_count": 1, "ls": ["viking://resources/verify-20261010T153910-e48bc7/dup-parent", "viking://resources/verify-20261010T153910-e48bc7/idem-src", "viking://resources/verify-20261010T153910-e48bc7/src", "viking://resources/verify-20261010T153910-e48bc7/.abstract.md", "viking://resources/verify-20261010T153910-e48bc7/.overview.md"], "second_error": null}`
- **C3d**: FAIL — 公开写入接口无 request_id 幂等键（携带该字段被 extra=forbid 拒绝）→ 无法按 request_id 判重
  - 观测：`{"status": 400, "body": "{\"status\":\"error\",\"error\":{\"code\":\"INVALID_ARGUMENT\",\"message\":\"Invalid request parameters: body.request_id: Extra inputs are not permitted\",\"details\":{\"validation_errors\":[{\"loc\":[\"body\",\"request_id\"],\"message\":\"Extra inputs are not permitted\",\"type\":\"extra_forbidden\"}]}}}"}`

## C4 — 来源只读：接入前后源目录清单与提交号一致；通过基座写来源被拒 → INCONCLUSIVE
- **C4a**: PASS — 接入前后源目录文件清单与 git 提交号必须一致
  - 观测：`{"added": [], "removed": [], "changed": [], "git_head_same": true, "git_status_same": true, "before_listing_sha256": "0d574d5acf9c735f86081228f085de3a29ab5c959e7ec096f8e219ee55dff269", "after_listing_sha256": "0d574d5acf9c735f86081228f085de3a29ab5c959e7ec096f8e219ee55dff269"}`
- **C4b**: INCONCLUSIVE — 基座只持有导入副本：副本可写、且写不改动真实源目录 —— 物理上没有『写来源』这个动作，无法给出『写来源被拒』的证据（不得记 PASS）
  - 观测：`{"accepted": true, "read_back": "INJECTED-BY-VERIFY\n", "source_dir_unchanged": true, "imported_copy_changed": true}`
- **C4c**: PASS — 本地文件系统路径作为来源的 HTTP 请求应被显式拒绝
  - 观测：`{"status": 403, "body": "{\"status\":\"error\",\"result\":null,\"error\":{\"code\":\"PERMISSION_DENIED\",\"message\":\"HTTP server only accepts remote resource URLs or temp-uploaded files; direct host filesystem paths are not allowed.\",\"details\":{}},\"telemetry\":null,\"profile\":null}"}`

## C5 — 停机报错：停隔离服务后消费者收到显式不可用错误，而非静默空结果 → PASS
- **C5a**: PASS — 在线对照必须成功
  - 观测：`{"online_read_ok": true}`
- **C5b**: PASS — 停机后读取必须抛显式错误，不能返回空结果
  - 观测：`{"stop": {"expected_launcher_pid": 34252, "recorded_listener_pid": 15268, "port_owner_pid": 15268, "ownership": {"pid": 15268, "cmdline": "\"D:\\Users\\Tan\\AppData\\Local\\Programs\\Python\\Python312\\python.exe\" \"D:\\python_work\\work2026-4\\Agent-Knowledge-Base\\venv-ov\\Scripts\\openviking-server.exe\" --config C:\\Users\\Tan\\.openviking-mvp-verify\\ov.conf --host 127.0.0.1 --port 8783", "owns": true}, "stopped": true, "killed_pids": [15268, 34252]}, "raised": true, "error": {"exception": "ConnectError", "module": "httpx", "message": "All connection attempts failed"}, "returned": null}`
- **C5c**: PASS — 停机后检索必须抛显式错误，不能返回空命中
  - 观测：`{"stop": {"expected_launcher_pid": 34252, "recorded_listener_pid": 15268, "port_owner_pid": 15268, "ownership": {"pid": 15268, "cmdline": "\"D:\\Users\\Tan\\AppData\\Local\\Programs\\Python\\Python312\\python.exe\" \"D:\\python_work\\work2026-4\\Agent-Knowledge-Base\\venv-ov\\Scripts\\openviking-server.exe\" --config C:\\Users\\Tan\\.openviking-mvp-verify\\ov.conf --host 127.0.0.1 --port 8783", "owns": true}, "stopped": true, "killed_pids": [15268, 34252]}, "raised": true, "error": {"exception": "ConnectError", "module": "httpx", "message": "All connection attempts failed"}, "returned": null}`
- **C5d**: PASS — 需要『无匹配』与『服务不可用』两档同时存在且类型不同；『拒绝访问』一档需非 ROOT 身份，本实例无法构造（如实标为无法核实）
  - 观测：`{"distinct": true, "classes": {"not_found": "NotFoundError", "unavailable": "ConnectError"}, "permission_denied_unreachable_with_dev_identity": true, "permission_denied_evidence": {"status": 403, "body": "{\"status\":\"error\",\"result\":null,\"error\":{\"code\":\"PERMISSION_DENIED\",\"message\":\"HTTP server only accepts remote resource URLs or temp-uploaded files; direct host filesystem paths are not allowed.\",\"details\":{}},\"telemetry\":null,\"profile\":null}"}}`

## 附加观测（非预注册 probe；只作描述性证据，不参与判定）

```json
{
  "download": {
    "status": 200,
    "bytes": 519,
    "sha256": "7889b2ade8fd1767894ec397b5d88ab8befca137d13bc9c20853b71078dbd605",
    "equal_to_input_bytes": true
  },
  "ov_version": {
    "args": [
      "--version"
    ],
    "rc": 0,
    "stdout": "openviking 0.4.24.dev0\n",
    "stderr": ""
  },
  "ov_read": {
    "args": [
      "read",
      "viking://user/default/verify/20261010T153910-e48bc7/roundtrip.md"
    ],
    "rc": 0,
    "stdout": "# 原文往返核验 OVVERIFY-RT-MARKER-20261010T153910-e48bc7\n\n这是一条用于独立复核的中文原文，包含 Markdown 语法与唯一标记。\n\n- 唯一标记：`OVVERIFY-RT-MARKER-20261010T153910-e48bc7`\n- 用途：验证提交 → 落盘 → 原文读回是否逐字节一致\n- 期望：任何摘要/解析/清洗都不得改写本正文\n\n```text\nOVVERIFY-RT-MARKER-20261010T153910-e48bc7:code-block-payload-0123456789\n```\n\n结尾无多余空行，末尾带一个制表符\t与一个全角空格　结束。\n\n",
    "stderr": "",
    "stdout_sha256": "9fb0905fd6dfc01f5f1f4b78843a8ca6150d3db5ebb08a17411fb8c1bb45a5c2",
    "equal_to_input": false,
    "equal_after_strip": true
  }
}
```

