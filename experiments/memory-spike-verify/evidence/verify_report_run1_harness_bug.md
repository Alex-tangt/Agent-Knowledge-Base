# 独立复核（自测）结果

- 阶段：`live`
- 生成时间：2026-10-10T15:31:04+0800
- 判定标准指纹（跑前预注册）：`17d18b7bbe842da6059b341a41319c11fa2824f30817aa191bc96802174f2f4d`
- 结果载荷指纹：`ebaa71f7a225d36d6b2f7e0b331320c79709d16a4992bc11cb1c050d04ec52ff`
- 隔离实例：openviking-server (候选实现, 隔离实例) @ 127.0.0.1:8783

| 检查 | 判定 | 说明 |
|---|---|---|
| C1 | **INCONCLUSIVE** | 原文往返：原样保存与读回（同时核磁盘字节与读取接口正文） |
| C2 | **FAIL** | 形态：接入的项目文件是「指过去」还是「拷一份进来」 |
| C3 | **FAIL** | 重复提交：同一请求提交两次不得产生第二条 |
| C4 | **FAIL** | 来源只读：接入前后源目录清单与提交号一致；通过基座写来源被拒 |
| C5 | **INCONCLUSIVE** | 停机报错：停隔离服务后消费者收到显式不可用错误，而非静默空结果 |

## C1 — 原文往返：原样保存与读回（同时核磁盘字节与读取接口正文） → INCONCLUSIVE
- **C1a**: PASS — 
  - 观测：`{"input_sha256": "225a43fd243fc72dce41aa2c9ec6e9887f2e0881c175daec0fea5f7683ad943a", "raw_sha256": "225a43fd243fc72dce41aa2c9ec6e9887f2e0881c175daec0fea5f7683ad943a"}`
- **C1b**: PASS — 磁盘/ git 对象中找到与输入逐字节相等的持久化正文
  - 观测：`{"disk_full_hits": 1, "disk_marker_hits": 1, "git_blobs": [], "git_examined": 0, "git_truncated": false, "files_scanned": 46}`
- **C1c**: PASS — 默认读回接口若与输入不等，M1 的『原文读回一致』不成立
  - 观测：`{"default_sha256": "225a43fd243fc72dce41aa2c9ec6e9887f2e0881c175daec0fea5f7683ad943a", "default_equal": true}`
- **C1d**: INCONCLUSIVE — 隔离实例未真正重启（旧进程仍在或启动被拒）→ 无法核 C1d
  - 观测：`{"equal": false, "error": "restart_not_started", "reason": "port_already_open", "restart": {"started": false, "reason": "port_already_open", "pid": 16456, "note": "端口已被占用；按纪律不接管别人的实例，停止操作"}}`

## C2 — 形态：接入的项目文件是「指过去」还是「拷一份进来」 → FAIL
- **C2a**: FAIL — 外部改源文件后应在上限内读到新正文（PASS=指过去；FAIL=拷贝/快照未追平）
  - 观测：`{"window_s": 120, "propagated": false, "samples": [{"t": 0, "equal_to_modified": false, "sha256": "308779e1943fabb33ef27589097fdd3d0f2c2ffaf59ab4bde526c8c25d0352b4"}, {"t": 10, "equal_to_modified": false, "sha256": "308779e1943fabb33ef27589097fdd3d0f2c2ffaf59ab4bde526c8c25d0352b4"}, {"t": 20, "equal_to_modified": false, "sha256": "308779e1943fabb33ef27589097fdd3d0f2c2ffaf59ab4bde526c8c25d0352b4"}, {"t": 30, "equal_to_modified": false, "sha256": "308779e1943fabb33ef27589097fdd3d0f2c2ffaf59ab4bde526c8c25d0352b4"}, {"t": 40, "equal_to_modified": false, "sha256": "308779e1943fabb33ef27589097fdd3d0`
- **C2b**: INCONCLUSIVE — workspace 内出现源文件正文的独立副本即判『拷贝』
  - 观测：`{"workspace_full_content_hits": [], "marker_hits": ["C:\\Users\\Tan\\.openviking-mvp-verify\\data\\viking\\default\\resources\\verify-20261010T152758-d39f9f\\src\\a.md", "C:\\Users\\Tan\\.openviking-mvp-verify\\data\\viking\\default\\resources\\verify-20261010T152758-d39f9f\\src\\b.md", "C:\\Users\\Tan\\.openviking-mvp-verify\\data\\viking\\default\\resources\\verify-20261010T152758-d39f9f\\src\\sub\\c.md", "C:\\Users\\Tan\\.openviking-mvp-verify\\data\\viking\\default\\user\\default\\verify\\20261010T152758-d39f9f\\roundtrip.md"]}`
- **C2c**: PASS — 同一源重复接入不应新增第二条
  - 观测：`{"count_after_first": 0, "count_after_second": 0}`

## C3 — 重复提交：同一请求提交两次不得产生第二条 → FAIL
- **C3a**: PASS — 同一 URI 同正文 write 两次（replace）后应只有 1 条
  - 观测：`{"name_count": 1, "ls": ["viking://user/default/verify/20261010T152758-d39f9f/idem/.abstract.md", "viking://user/default/verify/20261010T152758-d39f9f/idem/.overview.md", "viking://user/default/verify/20261010T152758-d39f9f/idem/replace-twice.md"]}`
- **C3b**: PASS — create 重复提交必须幂等或显式冲突，不得产生第二条
  - 观测：`{"name_count": 1, "second_rejected": true, "second_error": {"exception": "AlreadyExistsError", "module": "openviking_sdk.errors", "message": "File already exists: viking://user/default/verify/20261010T152758-d39f9f/idem/create-twice.md"}, "ls": ["viking://user/default/verify/20261010T152758-d39f9f/idem/.abstract.md", "viking://user/default/verify/20261010T152758-d39f9f/idem/.overview.md", "viking://user/default/verify/20261010T152758-d39f9f/idem/create-twice.md", "viking://user/default/verify/20261010T152758-d39f9f/idem/replace-twice.md"]}`
- **C3c**: PASS — 同一源 + 同一显式 to 重复提交不得新增条目
  - 观测：`{"entry_count": 1, "ls": ["viking://resources/verify-20261010T152758-d39f9f/idem-src", "viking://resources/verify-20261010T152758-d39f9f/src", "viking://resources/verify-20261010T152758-d39f9f/.abstract.md", "viking://resources/verify-20261010T152758-d39f9f/.overview.md"], "second_error": null}`
- **C3d**: FAIL — 公开写入接口无 request_id 幂等键（携带该字段被 extra=forbid 拒绝）→ 无法按 request_id 判重
  - 观测：`{"status": 400, "body": "{\"status\":\"error\",\"error\":{\"code\":\"INVALID_ARGUMENT\",\"message\":\"Invalid request parameters: body.request_id: Extra inputs are not permitted\",\"details\":{\"validation_errors\":[{\"loc\":[\"body\",\"request_id\"],\"message\":\"Extra inputs are not permitted\",\"type\":\"extra_forbidden\"}]}}}"}`

## C4 — 来源只读：接入前后源目录清单与提交号一致；通过基座写来源被拒 → FAIL
- **C4a**: PASS — 接入前后源目录文件清单与 git 提交号必须一致
  - 观测：`{"added": [], "removed": [], "changed": [], "git_head_same": true, "git_status_same": true, "before_listing_sha256": "1819f017d78d368d9cd20a69e266012eddc9600b457ecba78e05f00cfc63bbbe", "after_listing_sha256": "1819f017d78d368d9cd20a69e266012eddc9600b457ecba78e05f00cfc63bbbe"}`
- **C4b**: FAIL — 写请求改变了源目录内容
  - 观测：`{"target": "viking://resources/verify-20261010T152758-d39f9f/src/a.md", "write_result": {"uri": "viking://resources/verify-20261010T152758-d39f9f/src/a.md", "root_uri": "viking://resources/verify-20261010T152758-d39f9f/src", "context_type": "resource", "mode": "replace", "written_bytes": 19, "content_updated": true, "semantic_status": "complete", "vector_status": "complete", "queue_status": {"Semantic": {"processed": 1, "requeue_count": 0, "error_count": 0, "errors": []}, "Embedding": {"processed": 3, "requeue_count": 0, "error_count": 0, "errors": []}}}, "accepted": true, "source_after_write_`
- **C4c**: PASS — 本地文件系统路径作为来源的 HTTP 请求应被显式拒绝
  - 观测：`{"status": 403, "body": "{\"status\":\"error\",\"result\":null,\"error\":{\"code\":\"PERMISSION_DENIED\",\"message\":\"HTTP server only accepts remote resource URLs or temp-uploaded files; direct host filesystem paths are not allowed.\",\"details\":{}},\"telemetry\":null,\"profile\":null}"}`

## C5 — 停机报错：停隔离服务后消费者收到显式不可用错误，而非静默空结果 → INCONCLUSIVE
- **C5a**: PASS — 在线对照必须成功
  - 观测：`{"online_read_ok": true}`
- **C5b**: INCONCLUSIVE — 停机后读取必须抛显式错误，不能返回空结果（停机未成功 → 无法裁决）
  - 观测：`{"stop": {"pid_file": 34468, "port_owner_pid": 16456, "stopped": false, "note": "端口所有者 PID 与登记 PID 不一致 → 拒绝停止（可能是别人的实例）"}, "raised": false, "error": null, "returned": "'# 原文往返核验 OVVERIFY-RT-MARKER-20261010T152758-d39f9f\\n\\n这是一条用于独立复核的中文原文，包含 Markdown 语法与唯一标记。\\n\\n- 唯一标记：`OVVERIFY-RT-MARKER-20261010T152758-d39f9f`\\n- 用途：验证提交 → 落盘 → 原文读回是否逐字节一致\\n- 期望：任何摘要/解析/清洗都不得改写本正文\\n\\n```text\\nOVVERIFY-RT-MARKER-20261010T152758-d39f9f:code-block-payload-0123456789\\n```\\n\\n结尾无多余空行"}`
- **C5c**: INCONCLUSIVE — 停机后检索必须抛显式错误，不能返回空命中（停机未成功 → 无法裁决）
  - 观测：`{"stop": {"pid_file": 34468, "port_owner_pid": 16456, "stopped": false, "note": "端口所有者 PID 与登记 PID 不一致 → 拒绝停止（可能是别人的实例）"}, "raised": false, "error": null, "returned": "{\"memories\": [], \"resources\": [{\"context_type\": \"resource\", \"uri\": \"viking://resources/verify-20261010T152758-d39f9f/src/sub/c.md\", \"level\": 2, \"score\": 0.8452326059341431, \"abstract\": \"\", \"tags\": []}, {\"context_type\": \"resource\", \"uri\": \"viking://resources/verify-20261010T152758-d39f9f/idem-src/sub"}`
- **C5d**: PASS — 本实例为 dev/ROOT 身份，无法构造越权调用 → permission_denied 一档无法用身份区分核算
  - 观测：`{"not_found": {"exception": "NotFoundError", "module": "openviking_sdk.errors", "message": "File not found: viking://user/default/verify/20261010T152758-d39f9f/definitely-missing-20261010T152758-d39f9f.md"}, "unavailable": null, "permission_denied": {"status": 403, "body": "{\"status\":\"error\",\"result\":null,\"error\":{\"code\":\"PERMISSION_DENIED\",\"message\":\"HTTP server only accepts remote resource URLs or temp-uploaded files; direct host filesystem paths are not allowed.\",\"details\":{}},\"telemetry\":null,\"profile\":null}"}}`

