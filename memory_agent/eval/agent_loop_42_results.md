# #42 agent loop 场景评测结果（skill 化读/写/冲突裁决 + exclude_retired）

- 日期：2026-09-16 22:37:49
- 脚本：`memory_agent/eval/agent_loop_42.py`
- 结论：**PASS**（15/15）
- 临时工作区：`C:\Users\Tan\AppData\Local\Temp\opencode\agent-loop-42-1789569373`（临时 KB / 只读语料 / 索引 / daemon；真实全局 KB 未动）
- 口径：真 MCP 经共享 daemon（`proxy.py`）；daemon 注入确定性 Stub 嵌入，
  **不调 LLM 判分**，只断言外部行为（文件 / git / 命中）。

## PASS 矩阵

| # | 检查 | 结果 | 详情 |
|---|------|------|------|
| 1 | 0 索引重建完成且自洽 | PASS | entries=9 gen=gen-1 |
| 2 | A1 默认 False：superseded 条目照常可见（不静默改行为） | PASS | ids=['topics/retired-superseded', 'repo:docs/notes.md', 'topics/retired-archived', 'topics/live-draft', 'topics/live-current'] |
| 3 | A2a exclude_retired=True：superseded 被排除 | PASS | ids=['repo:docs/notes.md', 'topics/live-draft', 'topics/live-current', 'topics/backfill-live-2', 'topics/backfill-live-1'] |
| 4 | A2b exclude_retired=True：archived 被排除 | PASS | ids=['repo:docs/notes.md', 'topics/live-draft', 'topics/live-current', 'topics/backfill-live-2', 'topics/backfill-live-1'] |
| 5 | A3 exclude_retired=True：current / draft 保留 | PASS | current=['topics/live-current', 'repo:docs/notes.md', 'topics/live-draft', 'topics/backfill-live-2', 'topics/backfill-live-1'] draft=['topics/live-draft', 'repo:docs/notes.md', 'topics/live-current', 'topics/backfill-live-2', 'topics/backfill-live-1'] |
| 6 | A4 无 status 的只读语料不被误伤（排除已退役 ≠ 只要 current） | PASS | hit={'id': 'repo:docs/notes.md', 'title': 'Notes', 'source': 'docs/notes.md', 'writable': False, 'type': None, 'tags': [], 'status': None, 'classification': 'private', 'residency': 'local', 'tenant': None, 'owner': None, 'provenance': {'plane': 'local', 'tenant': None}, 'score': 0.36622776601683793, 'snippet': '# Notes A read-only project note without any frontmatter. READONLYNOSTATUS42'} |
| 7 | A5 命中契约：带 owner / status 字段 | PASS | keys=['classification', 'id', 'owner', 'provenance', 'residency', 'score', 'snippet', 'source', 'status', 'tags', 'tenant', 'title', 'type', 'writable'] |
| 8 | A6 退役项占前排时仍回填够 k 条（多取召回池再筛） | PASS | ids=['topics/backfill-live-1', 'topics/backfill-live-2'] status=['current', None] |
| 9 | B1 memory_add 落盘（真 git commit） | PASS | id=topics/agent-loop-dogfood-fact commit=e75379fa4b |
| 10 | B2 新会话召回得到该事实 | PASS | ids=['topics/agent-loop-dogfood-fact', 'repo:docs/notes.md', 'topics/live-draft', 'topics/live-current', 'topics/backfill-live-2'] |
| 11 | B3a supersede 默认只预览、不落盘（confirmation_required） | PASS | status=confirmation_required |
| 12 | B3b 预览后文件与 git 未动（没有偷偷写） | PASS |  |
| 13 | B4 supersede 落盘：新旧双向标注 + 一个 commit + KB 干净 | PASS | new_id=topics/agent-loop-dogfood-fact-v2 commits+1=True clean=True |
| 14 | B5 新会话 exclude_retired=True：只召回新条目（取新弃旧） | PASS | ids=['topics/agent-loop-dogfood-fact-v2', 'repo:docs/notes.md', 'topics/live-draft', 'topics/live-current', 'topics/backfill-live-2'] |
| 15 | B6 新会话默认召回：新旧都在（证明差异来自开关，而非写入） | PASS | ids=['topics/agent-loop-dogfood-fact-v2', 'topics/agent-loop-dogfood-fact', 'repo:docs/notes.md', 'topics/retired-superseded', 'topics/retired-archived'] |

## 运行日志

```text
# issue #42 场景评测 @ 2026-09-16 22:36:13
    沙箱 = C:\Users\Tan\AppData\Local\Temp\opencode\agent-loop-42-1789569373  daemon port = 54716

[0] 启动临时 daemon（Stub 嵌入，确定性）并重建索引
    daemon pid=35368
  [PASS] 0 索引重建完成且自洽 — entries=9 gen=gen-1

[A] 确定性：exclude_retired（真 MCP 经 daemon）
  [PASS] A1 默认 False：superseded 条目照常可见（不静默改行为） — ids=['topics/retired-superseded', 'repo:docs/notes.md', 'topics/retired-archived', 'topics/live-draft', 'topics/live-current']
  [PASS] A2a exclude_retired=True：superseded 被排除 — ids=['repo:docs/notes.md', 'topics/live-draft', 'topics/live-current', 'topics/backfill-live-2', 'topics/backfill-live-1']
  [PASS] A2b exclude_retired=True：archived 被排除 — ids=['repo:docs/notes.md', 'topics/live-draft', 'topics/live-current', 'topics/backfill-live-2', 'topics/backfill-live-1']
  [PASS] A3 exclude_retired=True：current / draft 保留 — current=['topics/live-current', 'repo:docs/notes.md', 'topics/live-draft', 'topics/backfill-live-2', 'topics/backfill-live-1'] draft=['topics/live-draft', 'repo:docs/notes.md', 'topics/live-current', 'topics/backfill-live-2', 'topics/backfill-live-1']
  [PASS] A4 无 status 的只读语料不被误伤（排除已退役 ≠ 只要 current） — hit={'id': 'repo:docs/notes.md', 'title': 'Notes', 'source': 'docs/notes.md', 'writable': False, 'type': None, 'tags': [], 'status': None, 'classification': 'private', 'residency': 'local', 'tenant': None, 'owner': None, 'provenance': {'plane': 'local', 'tenant': None}, 'score': 0.36622776601683793, 'snippet': '# Notes A read-only project note without any frontmatter. READONLYNOSTATUS42'}
  [PASS] A5 命中契约：带 owner / status 字段 — keys=['classification', 'id', 'owner', 'provenance', 'residency', 'score', 'snippet', 'source', 'status', 'tags', 'tenant', 'title', 'type', 'writable']
  [PASS] A6 退役项占前排时仍回填够 k 条（多取召回池再筛） — ids=['topics/backfill-live-1', 'topics/backfill-live-2'] status=['current', None]

[B] dogfood：存 -> 新会话召回 -> supersede -> 取新弃旧
  [PASS] B1 memory_add 落盘（真 git commit） — id=topics/agent-loop-dogfood-fact commit=e75379fa4b
  [PASS] B2 新会话召回得到该事实 — ids=['topics/agent-loop-dogfood-fact', 'repo:docs/notes.md', 'topics/live-draft', 'topics/live-current', 'topics/backfill-live-2']
  [PASS] B3a supersede 默认只预览、不落盘（confirmation_required） — status=confirmation_required
  [PASS] B3b 预览后文件与 git 未动（没有偷偷写）
  [PASS] B4 supersede 落盘：新旧双向标注 + 一个 commit + KB 干净 — new_id=topics/agent-loop-dogfood-fact-v2 commits+1=True clean=True
  [PASS] B5 新会话 exclude_retired=True：只召回新条目（取新弃旧） — ids=['topics/agent-loop-dogfood-fact-v2', 'repo:docs/notes.md', 'topics/live-draft', 'topics/live-current', 'topics/backfill-live-2']
  [PASS] B6 新会话默认召回：新旧都在（证明差异来自开关，而非写入） — ids=['topics/agent-loop-dogfood-fact-v2', 'topics/agent-loop-dogfood-fact', 'repo:docs/notes.md', 'topics/retired-superseded', 'topics/retired-archived']

结论：PASS  (15/15)
（临时 daemon 已停；真实 daemon / 真实全局 KB 未被打扰）
```
