# 用户故事 #17 验收：三个项目仓库只读语料

> 记录时间：2026-09-15 · 仓库 `Alex-tangt/Agent-Knowledge-Base` · 机器：Windows / Python 3.12（venv）
> 拓扑：stdio MCP（本验收用，避免污染常驻 daemon）；真相源 KB：`C:\Users\Tan\.config\opencode\knowledge`。
> 脚本：`memory_agent/eval/readonly_corpus_17.py`（走 MCP stdio 接缝，只断言外部行为）。

## 结论

**PASS（15/15）**。三个项目仓库的 **Markdown 文档**已被索引进只读语料，三个仓库各自都能被
**语义检索**命中；只读条目一律 `writable=false`，`supersede` / `archive` **结构上拒写**；
`memory_get` 能按只读 id 读回真实 Markdown。

| 用户故事 #17 验收项 | 结果 |
|---|---|
| 三个项目仓库文档可被语义召回 | ✅ 三仓库各有命中（agent-infra / kg-triplet-sft / agent-knowledge-base） |
| 只读语料被标 `writable:false` | ✅ 本仓库文档全部 false；`writable_only=true` 只回可写 KB |
| 写入不污染项目仓库 | ✅ supersede / archive 直接拒绝，消息含"只读参考语料不可写入" |
| 只索引文档（不索引代码） | ✅ 三仓库语料全部是 `.md` |

## 语料组成

`readonly_repos.json`（gitignored，本机）：

| label | path | 文档条数（首次全量重建时） |
|---|---|---|
| `agent-knowledge-base` | `D:\python_work\work2026-4\Agent-Knowledge-Base` | 51 |
| `kg-triplet-sft` | `D:\Study\SFT\kg-triplet-sft` | 33 |
| `agent-infra` | `D:\python_work\work2026-8\Agent-infra` | 22 |

索引 `gen-2`：验收脚本运行时 **134 条 = 26 可写 KB + 108 只读文档**（含中途补写的 ADR-0014
与博客）；写完本文后一度为 **135 条**，随后移除仓库内 blog 副本（发布版以 Tech-blog 为唯一出处），**最终 134 条 = 26 可写 + 108 只读**。自洽：`entries == points`，`consistent=true`。

## 验收输出（MCP 工具返回值，节选）

```
[PASS] 1. MCP 暴露 7 个记忆工具
[PASS] 2. 索引自洽且含三仓库语料 - gen=gen-2 entries=134 points=134 consistent=True
[PASS] 3.agent-infra 语义检索命中本仓库文档
        top=[('agent-infra/skills/agent-design/SKILL.md', 0.633, False),
             ('projects/agent-infra/vision.md', 0.629, True), ...]
[PASS] 3.kg-triplet-sft 语义检索命中本仓库文档
        top=[('kg-triplet-sft/AGENTS.md', 0.576, False), ...]
[PASS] 3.agent-knowledge-base 语义检索命中本仓库文档
        top=[('agent-knowledge-base/docs/adr/0014-readonly-corpus-labeled-repos.md', 0.73, False), ...]
[PASS] 4. writable_only=true 只返回可写 KB 条目
[PASS] 5. memory_get 读回只读条目真实 Markdown - id=repo:agent-infra/skills/agent-design/SKILL.md chars=3138
[PASS] 6. supersede 拒写只读语料 - ...repo:agent-infra/... 是只读参考语料，不可写入
[PASS] 7. archive 拒写只读语料 - ...repo:agent-infra/... 是只读参考语料，不可写入

15/15 passed
```

## 过程中发现并修掉的缺陷

**MCP 工具错误消息被吞。** 工具里 `raise ValueError` 被 `mcp` 2.x 当作**崩溃**处理，客户端只
收到 `Error executing tool <name>`；写入网关那些面向调用方的提示（去重候选、frontmatter 校验
失败、只读拒写原因、confirm 提示）全部不可见。改为 `raise ToolError`（SDK 约定的"预期失败"），
消息随 `is_error=True` 返回。见 `mcp_server.py` 顶部注释。

## 复现

```powershell
# 1) 配置（本机）：memory_agent/readonly_repos.json = [{label, path}...]（模板 .example.json）
# 2) 全量重建（~十几分钟 CPU）：venv\Scripts\python.exe memory_agent/build_index.py
# 3) 验收：venv\Scripts\python.exe memory_agent/eval/readonly_corpus_17.py
```

> 约束：`READONLY_ROOTS` 在 import 时求值——**改完配置必须重启 daemon**，否则写入触发的增量
> 刷新会把"不在 daemon 语料里"的条目当孤儿删掉（见 `docs/adr/0014`）。
