# issue #17 dogfood：经 MCP 真实写入 + 跨会话 recall

> 记录时间：2026-09-14 · 仓库 `Alex-tangt/Agent-Knowledge-Base` @ `5059ac8` · 机器：Windows / Python 3.12（venv）
> 拓扑：常驻 daemon（`127.0.0.1:8765`，健康）+ 每会话 stdio 代理（ADR-0013）。
> 真相源 KB：`C:\Users\Tan\.config\opencode\knowledge`（另一个 git 仓库，写入由写入方自做路径级单文件提交）。

## 结论

**PASS**。三条验收全部满足：经用户确认的一条**真实记忆**通过了写入网关（去重 → frontmatter 校验 → 单文件 git commit → 增量刷新索引），并在**两个独立通道**上用一句**改写查询**盲测命中（关键词 grep 全库 0 命中，证明是语义召回）。

| #17 验收项 | 结果 | 证据 |
|---|---|---|
| 真实写入一条记忆（经确认） | ✅ | KB commit `7b37efb`，仅 1 文件；`kb.py check` 0 errors |
| 后续会话/查询能 recall 到该条 | ✅ | 独立进程 + 独立子代理，两路均命中 **#1**（同 score `0.636646…`） |
| 记录过程与证据 | ✅ | 本文 + #17 评论 |

## 1. 写入了什么

用户确认写入素材为 **#19 共享单实例拓扑决策**（源自 `docs/adr/0013`）。

| 字段 | 值 |
|---|---|
| `id` | `decisions/memory-package-shared-single-instance-topology` |
| `domain` / `type` | `decisions` / `decision` |
| `tags` | `agent-knowledge-base`, `mcp`, `architecture`, `decision`（均在受控 `tags.md`） |
| `status` | `current` · 68 行 |
| KB commit | **`7b37efb8bcf9eaccb5f7144827e166044fc482e0`** |

写入调用（**真写网关，不是丢文件**）：

```
memory_add(
  title="共享单实例：一个常驻 MCP daemon + 每会话瘦代理（把一份 BGE-M3 共享给 N 个会话）",
  domain="decisions", type="decision",
  tags=["agent-knowledge-base","mcp","architecture","decision"],
  slug="memory-package-shared-single-instance-topology",
  sources=[docs/adr/0013..., AGENTS.md, memory_agent/eval/issue19_acceptance.md],
)
```

## 2. 写入证据（含一个真实坑）

**坑：`memory_add` 客户端在 20s 超时（opencode `timeout=20000`），但服务端已提交成功。**
本轮写入时并发会话也新增了若干条目，增量刷新一并嵌入（索引条目 70 → **76**），
整个写路径（去重检索 → 校验 → commit → 增量刷新）超过了 20s。客户端超时**不等于**写入失败——
必须靠**服务端真相源**核对，而不是靠工具返回值。

服务端核对（KB 仓库）：

```
$ git show --name-only --format="%H %s" 7b37efb
7b37efb8bcf9eaccb5f7144827e166044fc482e0 kb: 新增 decisions/memory-package-shared-single-instance-topology

decisions/memory-package-shared-single-instance-topology.md
```

- **只含 1 个文件** → 路径级单文件提交成立，未夹带并发会话的在制品。
- frontmatter 校验：`venv\Scripts\python.exe ...\knowledge\tools\kb.py check`
  → `check: 27 entries, 0 errors, 2 warnings`（2 个 warning 是既有的 `demo` tag 演示条目，与本次无关）。
- 索引自洽：`memory_index_status` → `{built:true, gen:"gen-1", entries:76, points:76, consistent:true}`。

## 3. 盲测 recall（给验证方的只有一句改写查询）

验证方**不被提供**标题或正文，只拿到改写后的查询，自行调 `memory_search` 并原样回报。

| 通道 | 说明 | 查询 | 命中 |
|---|---|---|---|
| A | 全新 Python 进程（pid 33472），自己的 MCP stdio 会话，经 `proxy.py` | 「多个 agent 会话同时用记忆功能，怎么避免每个会话都各自吃掉一整份大模型内存？」 | 目标条 `writable_only` 结果内出现（k=3 全库第 3，score 0.6230；第 1 是只读 ADR 镜像） |
| B | 独立 opencode 子代理会话（工具名 `memory-agent_memory_search`） | 「怎么让 N 个 opencode 会话只加载一份向量模型、共享同一个服务？」`writable_only=true` | **#1** `decisions/memory-package-shared-single-instance-topology`，score **0.6366460237685589** |

- 通道 A/B **同查询同结果、score 逐位相同**，互相印证。
- **语义而非关键词**：A 查询里「各自吃掉 / 记忆功能 / 一整份大模型内存」等词，在**整个 KB** 里
  关键词 grep = **0 命中**；B 查询同理不靠字面匹配。命中来自向量语义。
- 通道 B 还回答了交接文档 §4.2 的悬念：**子代理继承了 MCP 工具**（本次 opencode 配置下），
  故“跨会话 recall”既有独立进程、也有独立 agent 会话两重证据。

盲测原始输出：`C:\Users\Tan\AppData\Local\Temp\opencode\dogfood17_recall_evidence.json`
（探针脚本 `dogfood17_recall_probe2.py`，仅临时件，未入仓）。

## 4. 过程记录 / 运维提示

1. **客户端超时 ≠ 写入失败**：见 §2。写路径要新加的运维习惯是“超时后核对 KB `git log`/索引”，
   而不是重试（重试会命中去重、只返回 `duplicate`）。
2. **超时后同一 MCP 会话可能卡住**：本轮超时后，本会话后续 `memory_index_status` 也一直
   `-32001`；独立进程与子代理不受影响。判断 daemon 健康应以 `GET /health` / `proxy.py --status`
   为准，而非单个受损会话。
3. **增量刷新会顺带吸收并发会话的新条目**（70 → 76），这是正确行为，但会让单次写入耗时上升。

## 5. 收尾

- 关闭 **#17**（本票）。
- 关闭 **#7**（记忆能力包 MVP）—— #17 是其收尾票；#15（BEIR）已移入独立叙事 #21。
