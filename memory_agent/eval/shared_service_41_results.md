# #41 共享服务验收结果（第二消费者 = DeepTutor；#44 返工：共享**可读写**全局 KB）

- 日期：2026-09-16 22:33:30
- 脚本：`memory_agent/eval/shared_service_41.py`
- 结论：**PASS**（37/37）
- 临时工作区：`C:\Users\Tan\AppData\Local\Temp\opencode\shared-service-41-1789569146`（临时 KB / 索引 / DeepTutor home；真实语料与真实 daemon 未动）

## PASS 矩阵

| # | 检查 | 结果 | 详情 |
|---|------|------|------|
| 1 | 安装写入 streamableHttp 条目 | PASS | changed: True -> False |
| 2 | 幂等（第二次不改动文件字节） | PASS |  |
| 3 | 合并保留其它服务条目 | PASS |  |
| 4 | dry-run 走通且不动 daemon | PASS |  |
| 5 | 一步安装 PASS（opencode 已注册 + skill 落位） | PASS |  |
| 6 | skill 已落位到 opencode skills 目录 | PASS |  |
| 7 | DeepTutor 读到 memory-agent 且解析为 streamableHttp | PASS | type=streamableHttp url=http://127.0.0.1:59896/mcp |
| 8 | 部署级校验放行 loopback（strict=False） | PASS | [True, ''] |
| 9 | 自服务严格校验拦下 loopback（证明必须走部署级配置） | PASS |  |
| 10 | 共享边界：enabled_tools 含 memory_add | PASS |  |
| 11 | 共享边界：enabled_tools 含 memory_supersede | PASS |  |
| 12 | 共享边界：enabled_tools 含 memory_archive | PASS |  |
| 13 | 维护 / 收录 DDL 不进白名单：memory_reindex | PASS |  |
| 14 | 维护 / 收录 DDL 不进白名单：memory_ingest_include | PASS |  |
| 15 | 维护 / 收录 DDL 不进白名单：memory_ingest_exclude | PASS |  |
| 16 | 共享工具 = 读 + 内容写（不含 reindex） | PASS | memory_search, memory_get, memory_add, memory_supersede, memory_archive, memory_index_status, memory_ingest_list |
| 17 | 索引重建完成且条数正确 | PASS | entries=4 (期望 4) |
| 18 | 两消费者同 index_status().gen | PASS | gen=gen-1 |
| 19 | 两消费者同条数且自洽 | PASS | entries=4 |
| 20 | 同 query 同 top-k id | PASS | ['topics/shared-service-beta', 'topics/shared-service-alpha', 'repo:docs/README.md'] |
| 21 | 改动前 marker 不可见 | PASS |  |
| 22 | DeepTutor 侧（http）搜到外部改动 | PASS | topics/shared-service-alpha |
| 23 | opencode 侧（stdio）搜到外部改动 | PASS | topics/shared-service-alpha |
| 24 | 惰性追平后同 gen（未换代）且自洽 | PASS | gen=gen-1 |
| 25 | 第二消费者（http）memory_add 落盘 | PASS | id=topics/shared-service-write-visibility |
| 26 | DeepTutor 侧（http）搜到自己写入的条目 | PASS | topics/shared-service-write-visibility |
| 27 | opencode 侧（stdio）搜到 DeepTutor 写入的条目 | PASS | topics/shared-service-write-visibility |
| 28 | 第二消费者 memory_supersede(confirm=True) 落盘 | PASS | new_id=topics/shared-service-write-visibility-v2 |
| 29 | 第二消费者 memory_archive(confirm=True) 落盘 | PASS | id=topics/shared-service-write-visibility-v2 |
| 30 | 审计含 memory_add 的成功事件 → agent 身份 | PASS | principal(s)=['deeptutor-agent'] |
| 31 | 审计含 memory_supersede 的成功事件 → agent 身份 | PASS | principal(s)=['deeptutor-agent'] |
| 32 | 审计含 memory_archive 的成功事件 → agent 身份 | PASS | principal(s)=['deeptutor-agent'] |
| 33 | 写审计身份带 principal + role（可归属到 agent） | PASS | 3 条写事件 |
| 34 | 第二消费者身份 role=owner（显式验证可写全局 KB） | PASS | identities=[{'principal': 'deeptutor-agent', 'tenant': None, 'role': 'owner', 'classifications': ['internal', 'private', 'public'], 'residencies': ['cloud', 'local'], 'owners': None}] |
| 35 | 审计不落凭证（token 不出现在审计文件） | PASS |  |
| 36 | 本机 opencode 已注册 memory-agent → proxy.py | PASS | 已注册（type=local → proxy.py，enabled） |
| 37 | 本机全局 skill 已落位 | PASS |  |

## 运行日志

```text
# issue #41 验收 @ 2026-09-16 22:32:26
    workdir = C:\Users\Tan\AppData\Local\Temp\opencode\shared-service-41-1789569146

[1] 一步安装（DeepTutor 部署级 mcp.json）
  [PASS] 安装写入 streamableHttp 条目 — changed: True -> False
  [PASS] 幂等（第二次不改动文件字节）
  [PASS] 合并保留其它服务条目
  [PASS] dry-run 走通且不动 daemon
  [PASS] 一步安装 PASS（opencode 已注册 + skill 落位）
  [PASS] skill 已落位到 opencode skills 目录

[2] 用 DeepTutor 自身的 load_mcp_config / validate_mcp_url 校验
  [PASS] DeepTutor 读到 memory-agent 且解析为 streamableHttp — type=streamableHttp url=http://127.0.0.1:59896/mcp
  [PASS] 部署级校验放行 loopback（strict=False） — [True, '']
  [PASS] 自服务严格校验拦下 loopback（证明必须走部署级配置）
  [PASS] 共享边界：enabled_tools 含 memory_add
  [PASS] 共享边界：enabled_tools 含 memory_supersede
  [PASS] 共享边界：enabled_tools 含 memory_archive
  [PASS] 维护 / 收录 DDL 不进白名单：memory_reindex
  [PASS] 维护 / 收录 DDL 不进白名单：memory_ingest_include
  [PASS] 维护 / 收录 DDL 不进白名单：memory_ingest_exclude
  [PASS] 共享工具 = 读 + 内容写（不含 reindex） — memory_search, memory_get, memory_add, memory_supersede, memory_archive, memory_index_status, memory_ingest_list

[3] 启动临时 daemon（Stub 嵌入，确定性）并重建索引
    daemon pid=23588 url=http://127.0.0.1:59896/mcp
  [PASS] 索引重建完成且条数正确 — entries=4 (期望 4)

[4] streamableHttp 与 stdio 代理连同一 daemon
  [PASS] 两消费者同 index_status().gen — gen=gen-1
  [PASS] 两消费者同条数且自洽 — entries=4
  [PASS] 同 query 同 top-k id — ['topics/shared-service-beta', 'topics/shared-service-alpha', 'repo:docs/README.md']

[5] 外部改一个已收录 .md → 两消费者下一次 search 都反映（无重启）
  [PASS] 改动前 marker 不可见
  [PASS] DeepTutor 侧（http）搜到外部改动 — topics/shared-service-alpha
  [PASS] opencode 侧（stdio）搜到外部改动 — topics/shared-service-alpha
  [PASS] 惰性追平后同 gen（未换代）且自洽 — gen=gen-1

[6] DeepTutor（http，带自己的 agent token）写全局 KB → 两消费者读可见
  [PASS] 第二消费者（http）memory_add 落盘 — id=topics/shared-service-write-visibility
  [PASS] DeepTutor 侧（http）搜到自己写入的条目 — topics/shared-service-write-visibility
  [PASS] opencode 侧（stdio）搜到 DeepTutor 写入的条目 — topics/shared-service-write-visibility
  [PASS] 第二消费者 memory_supersede(confirm=True) 落盘 — new_id=topics/shared-service-write-visibility-v2
  [PASS] 第二消费者 memory_archive(confirm=True) 落盘 — id=topics/shared-service-write-visibility-v2

[6b] 审计（每个 tools/call 记身份）：写调用可归属到 agent
  [PASS] 审计含 memory_add 的成功事件 → agent 身份 — principal(s)=['deeptutor-agent']
  [PASS] 审计含 memory_supersede 的成功事件 → agent 身份 — principal(s)=['deeptutor-agent']
  [PASS] 审计含 memory_archive 的成功事件 → agent 身份 — principal(s)=['deeptutor-agent']
  [PASS] 写审计身份带 principal + role（可归属到 agent） — 3 条写事件
  [PASS] 第二消费者身份 role=owner（显式验证可写全局 KB） — identities=[{'principal': 'deeptutor-agent', 'tenant': None, 'role': 'owner', 'classifications': ['internal', 'private', 'public'], 'residencies': ['cloud', 'local'], 'owners': None}]
  [PASS] 审计不落凭证（token 不出现在审计文件）

[7] 本机 opencode 注册 / skill 落位核验（只读）
  [PASS] 本机 opencode 已注册 memory-agent → proxy.py — 已注册（type=local → proxy.py，enabled）
  [PASS] 本机全局 skill 已落位

结论：PASS  (37/37)
（临时 daemon 已停；真实 daemon 未被打扰）
```
