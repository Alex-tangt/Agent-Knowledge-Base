# #41 共享服务验收结果（第二消费者 = DeepTutor）

- 日期：2026-09-16 18:54:39
- 脚本：`memory_agent/eval/shared_service_41.py`
- 结论：**PASS**（27/27）
- 临时工作区：`C:\Users\Tan\AppData\Local\Temp\opencode\shared-service-41-1789556034`（临时 KB / 索引 / DeepTutor home；真实语料与真实 daemon 未动）

## PASS 矩阵

| # | 检查 | 结果 | 详情 |
|---|------|------|------|
| 1 | 安装写入 streamableHttp 条目 | PASS | changed: True -> False |
| 2 | 幂等（第二次不改动文件字节） | PASS |  |
| 3 | 合并保留其它服务条目 | PASS |  |
| 4 | dry-run 走通且不动 daemon | PASS |  |
| 5 | 一步安装 PASS（opencode 已注册 + skill 落位） | PASS |  |
| 6 | skill 已落位到 opencode skills 目录 | PASS |  |
| 7 | DeepTutor 读到 memory-agent 且解析为 streamableHttp | PASS | type=streamableHttp url=http://127.0.0.1:55597/mcp |
| 8 | 部署级校验放行 loopback（strict=False） | PASS | [True, ''] |
| 9 | 自服务严格校验拦下 loopback（证明必须走部署级配置） | PASS |  |
| 10 | 只读边界：enabled_tools 不含 memory_add | PASS |  |
| 11 | 只读边界：enabled_tools 不含 memory_supersede | PASS |  |
| 12 | 只读边界：enabled_tools 不含 memory_archive | PASS |  |
| 13 | 只读边界：enabled_tools 不含 memory_reindex | PASS |  |
| 14 | 只读白名单 = search/get/index_status/ingest_list | PASS | memory_search, memory_get, memory_index_status, memory_ingest_list |
| 15 | 索引重建完成且条数正确 | PASS | entries=4 (期望 4) |
| 16 | 两消费者同 index_status().gen | PASS | gen=gen-1 |
| 17 | 两消费者同条数且自洽 | PASS | entries=4 |
| 18 | 同 query 同 top-k id | PASS | ['topics/shared-service-beta', 'topics/shared-service-alpha', 'repo:docs/README.md'] |
| 19 | 改动前 marker 不可见 | PASS |  |
| 20 | DeepTutor 侧（http）搜到外部改动 | PASS | topics/shared-service-alpha |
| 21 | opencode 侧（stdio）搜到外部改动 | PASS | topics/shared-service-alpha |
| 22 | 惰性追平后同 gen（未换代）且自洽 | PASS | gen=gen-1 |
| 23 | stdio 侧 memory_add 落盘 | PASS | id=topics/shared-service-write-visibility |
| 24 | DeepTutor 侧搜到 opencode 写入的条目 | PASS | topics/shared-service-write-visibility |
| 25 | 两消费者对新写入条目可见性一致 | PASS |  |
| 26 | 本机 opencode 已注册 memory-agent → proxy.py | PASS | 已注册（type=local → proxy.py，enabled） |
| 27 | 本机全局 skill 已落位 | PASS |  |

## 运行日志

```text
# issue #41 验收 @ 2026-09-16 18:53:54
    workdir = C:\Users\Tan\AppData\Local\Temp\opencode\shared-service-41-1789556034

[1] 一步安装（DeepTutor 部署级 mcp.json）
  [PASS] 安装写入 streamableHttp 条目 — changed: True -> False
  [PASS] 幂等（第二次不改动文件字节）
  [PASS] 合并保留其它服务条目
  [PASS] dry-run 走通且不动 daemon
  [PASS] 一步安装 PASS（opencode 已注册 + skill 落位）
  [PASS] skill 已落位到 opencode skills 目录

[2] 用 DeepTutor 自身的 load_mcp_config / validate_mcp_url 校验
  [PASS] DeepTutor 读到 memory-agent 且解析为 streamableHttp — type=streamableHttp url=http://127.0.0.1:55597/mcp
  [PASS] 部署级校验放行 loopback（strict=False） — [True, '']
  [PASS] 自服务严格校验拦下 loopback（证明必须走部署级配置）
  [PASS] 只读边界：enabled_tools 不含 memory_add
  [PASS] 只读边界：enabled_tools 不含 memory_supersede
  [PASS] 只读边界：enabled_tools 不含 memory_archive
  [PASS] 只读边界：enabled_tools 不含 memory_reindex
  [PASS] 只读白名单 = search/get/index_status/ingest_list — memory_search, memory_get, memory_index_status, memory_ingest_list

[3] 启动临时 daemon（Stub 嵌入，确定性）并重建索引
    daemon pid=6076 url=http://127.0.0.1:55597/mcp
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

[6] opencode（stdio）写 → DeepTutor（http）读可见
  [PASS] stdio 侧 memory_add 落盘 — id=topics/shared-service-write-visibility
  [PASS] DeepTutor 侧搜到 opencode 写入的条目 — topics/shared-service-write-visibility
  [PASS] 两消费者对新写入条目可见性一致

[7] 本机 opencode 注册 / skill 落位核验（只读）
  [PASS] 本机 opencode 已注册 memory-agent → proxy.py — 已注册（type=local → proxy.py，enabled）
  [PASS] 本机全局 skill 已落位

结论：PASS  (27/27)
（临时 daemon 已停；真实 daemon 未被打扰）
```
