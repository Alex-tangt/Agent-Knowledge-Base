# 记忆框架调研（memory-frameworks-survey）

日期：**2026-10-09** · 状态：**调研（census）——不含本项目架构决策**

> 本目录只回答"外部实践长什么样"：形状、共识、分歧、**无人做的那一格**、可借用的现成物。
> **不含**本项目该怎么做——那属于对话（收敛）→ ADR（决策落点），见 `AGENTS.md`「决策落点」。
> 用户点名调研 **Hindsight**；本文件把它单列一节。

## 问题

用户愿景：**一个个人的知识库，不局限于不同应用、不同机器，所有 personal agent 都可访问；
不只是静态检索，还要能整理个人的知识与认知体系、总结个人偏好；接入它，新 agent 不需要重新培养。**

衍生问题：外部实践已经解决了什么？卡在哪？有没有现成物可复用？
（仓库纪律：**复用开源基线优先**——先调研 → 复用基线 → 廉价测量 → 才排贵运行。）

## 假设（开工前）

1. "最火"的框架在**行为改变 / 零培养**这条轴上应该已有答案。
2. 主要分野是**存储形态**（文件 vs DB）。
3. 应该能直接复用评测集或数据交换格式。

**结果：假设 1 被证伪（见"无人做的那一格"第 1 条），假设 2 部分成立（另有"主体"这条更关键的分野），假设 3 成立（见"可借用的现成物"）。**

## 设置

- **只读**调研，未改动仓库任何文件。收工核对：`git status --short` 空 · `git diff --numstat` 空 · HEAD `d712a3b`。
- 4 路并行 subagent，各自 `web_search` + `web_fetch` 一手来源（README / in-repo docs / GitHub API / issues / arXiv）。
  每路被要求：每条事实带 URL、区分**厂商声称**与**可验证行为**、无法核实一律写 `unverified` 而不是猜。
- 覆盖 15 个系统/实践 + 3 篇治理与风险文献：
  - 单列：**Hindsight**（vectorize-io）
  - 框架：**Letta/MemGPT** · **mem0** · **Zep/Graphiti** · **Cognee**
  - file-first / MCP：**Basic Memory** · **官方 MCP memory server** · **Khoj** · **memobase** · **supermemory** · **Obsidian 栈**
  - 治理与失败：生产厂商记忆治理（ChatGPT / Claude / Gemini）· 自我改进框架（LangMem / A-Mem / MemoryOS / ACE）· 风险文献（SEABench / MemoryGraft / A-MemGuard / Always-On Agents 综述）

## 数据 1 · 两个阵营（按真相源）

| 阵营 | 系统 | 真相源 |
|---|---|---|
| **文件/git 为真** | **Letta（MemFS）** · **Basic Memory** · **Obsidian 栈** · **本项目** | Markdown + YAML frontmatter + git |
| **DB/服务为真** | **Hindsight** · **mem0** · **Zep/Graphiti** · **Cognee** · **Khoj** · **memobase** · **supermemory** · **官方 MCP memory server**（单 JSONL） | Postgres / 图 DB / SQL / 单文件 |

**关键对照**：Letta 的 MemFS 与 Hindsight 的存储哲学**正好相反**——
Hindsight 是 "DB 为真相源，markdown 是**投影**"（原话："删掉一个页面什么都没丢，它会从 memory 重新投影"），
并且明确**不做存储抽象**（自称 "a deliberate trade-off"）。
而 Letta/Basic Memory/本项目是 "文件为真相源，索引是派生"。
→ **文件阵营必须自己保证索引可重建；DB 阵营必须自己保证人能读（Hindsight 靠 `fs mount` 投影回来）。两边都在补对方的默认优势。**

## 数据 2 · 总表（主体 / 真相源 / 合成层 / 共享 / 自我改进 / 许可）

| 系统 | 主体 | 真相源 | 合成层 | 多 agent 共享 | 自我改进 | 许可 |
|---|---|---|---|---|---|---|
| **Hindsight**（vectorize-io，47.2k★） | **`bank_id`**（"一个 user/agent/project 的大脑"，选择权交给使用者） | **Postgres 15+pgvector**（可嵌 `pg0`）；markdown 是投影 | **强**：observations（证据回指 + proof count + refined 不覆盖）→ mental models（后台重写；读它 = 纯 DB 读）→ knowledge pages | 每 bank 一个 MCP 端点；"一个共享 bank 大家一起指" | 无（行为塑造是**运维预设**的 disposition/directives） | **MIT** |
| **Letta**（注册表已退役 → letta-code） | **agent**（一 agent 一 MemFS；人类 = `human.md` **文件**，非一等实体） | **每 agent 的 git 仓 Markdown**；`persona.md`/`human.md` **每轮注入系统提示** | **有**：agent 重写自己的 persona/human/skill（系统提示**就是**记忆）；无图 | 共享 memory 仓（org 拥有，cloud-only）+ hosted MCP | **有（设计声称最强）**，无受控评测 | Apache-2.0 |
| **mem0**（66.8k★） | `user_id`/`agent_id`/`app_id`/`run_id`；**按说话人归属每条事实** | **SQL DB** 为真相源；向量/实体派生 | **弱**：无画像产物；图是派生的（**Platform-only，OSS 无**） | 同 scope；hosted MCP | 无 | Apache-2.0（Platform 专有） |
| **Zep/Graphiti**（31.6k★） | `group_id` 命名空间；`Person` 实体存在但 **OSS 无 user 对象** | **图 DB**；**episodes = "ground truth stream"** | **强**：实体节点 + 事实边 + 社区摘要 + saga；**双时间戳** `valid_at/invalid_at` vs `created_at/expired_at` | 同 `group_id`；in-repo MCP + REST | 无（只做失效，不删除） | Apache-2.0（Zep Cloud 专有） |
| **Cognee**（31.7k★） | `dataset` + **user principal**（权限/多租户；四者中最接近一等用户） | **原始文件 + 关系库**；图/向量**派生**（`forget(memory_only=True)` 删图/向量、留原始） | **最重**：`improve()` 9 阶段——反馈权重、会话 Q&A **与 agent traces**、`session_learnings` 课程文档、per-user `prefers` 偏好子图、全局上下文索引 | 一个 API server 压共享 Neo4j+Postgres；`cognee-mcp` | **部分**（反馈重排 + 课程沉淀；不改提示/工具策略） | Apache-2.0（生产 Postgres-as-graph 为授权产品） |
| **Basic Memory**（4.1k★） | 笔记/实体；`type: Person` 是约定，**不是用户库** | **Markdown + frontmatter**（`title/type/tags/permalink/created/modified/schema`）；观察语法 `- [category] fact #tag (context)` | 有：journal → wiki projections（`index.md`/`log.md`）+ `bm wiki rebuild\|status\|validate` | 手动 git/Syncthing；cloud 同步 rclone **带冲突解决** | 无 | **AGPL-3.0** |
| **官方 MCP memory server** | 仅实体（建议提示里硬编码 `default_user`） | 单个 `memory.jsonl` | 无 | 无 | 无 | MIT（官方标注**非生产可用**） |
| **Khoj**（37.6k★） | Django 用户（多用户）| **服务端 DB**（文件是输入） | 有：agents、定时邮件研究/简报 | server-authoritative | 无 | **AGPL-3.0** |
| **memobase**（2.9k★） | **用户一等**（per-user profile slots + 事件时间线/gist） | Postgres+Redis | 有：LLM 分批画像，**触发式**（缓冲 ~1024 token 或空闲 ~1h） | server-authoritative | 无 | Apache-2.0 |
| **supermemory**（31k★） | **用户画像**（static/dynamic） | 服务 DB（本地 `./.supermemory`） | 有：矛盾消解 + 自动遗忘（内建 Platform） | 多设备即产品（`containerTag`） | 无 | 仓库 MIT；hosted 专有 |

## 数据 3 · 共识（多数/全部同向 → 大概率是对的）

1. **派生层压在一个权威源上，且必须可重建**。四个框架全部如此；Cognee/Graphiti 明确给权威源命名（raw files / episodes）；Basic Memory 写进 `DOMAIN_MODEL.md`（"full-text rows, vector embeddings, chunks, observation rows, relation rows are **derived projections**… must be rebuildable"）。**本项目已实现（代目录 + CURRENT 指针 + 自洽核对）。**
2. **Provenance 是入场券**：Letta 用 git 历史、mem0 用 ADD 事件历史、Graphiti 用 `MENTIONS` 边 + episode 回指、Cognee 用 per-document 节点/边归属。**本项目有 git commit/写入 + `sources` 字段，但 `sources` 未进新鲜度检查。**
3. **MCP 是共享边界**（全部出货或托管 MCP 服务端），HTTP/REST 次之。**本项目已有第二消费者共享 daemon。**
4. **scoping ID 是强制的**（agent / user·agent·app·run / group_id / dataset+user / bank_id）。**本项目有 tenant + owner + section；但没有"人"这个 ID。**
5. **写入以追加为主，破坏性操作显式且（大体）带闸门。** **本项目有 supersede/archive + 默认预览。**
6. **LLM 抽取的原子单元优先于原始流水**（mem0 / Graphiti / Cognee）。**唯一例外是 Letta**——而 Letta 的 LoCoMo **74.0% 是用普通文件 grep 工具拿到的**（自报），这**削弱了"必须有专用存储"的论断**，对本项目的文件立场是**支持性证据**。

## 数据 4 · 分歧（及各自 hinge）

1. **更新 vs 失效 vs 遗忘**：mem0 **ADD-only**（承认会留下过时/矛盾事实，open issue #4956；文档自认"知识更新是加法式架构最难的一类"）｜Graphiti **只失效不删除**（双时间戳）｜Cognee **真有粒度化 `forget()` 且级联清理**（证据被删后，受污染的会话缓存轮次也清掉）｜Letta 交给 **agent 判断**。
   **hinge = 产品的承诺是"时间史"还是"当前真相"。**
2. **表示**：文件树 vs 向量事实 vs 双时间图 vs 多存储（图+摘要）。Letta 的 74% 说明**文件 + 检索可以很有竞争力**。
3. **主体是谁**：agent 为中心（Letta）｜user/agent 分区（mem0）｜纯命名空间（Graphiti OSS）｜user+dataset 权限（Cognee）｜bank（Hindsight）｜用户一等（memobase）。**只有 mem0 默认把事实归属到"是谁说的"。**
4. **基准数字全部是厂商互撕状态**：Zep↔Mem0 的 LoCoMo 之争（Zep 84% → 被质疑后**更正为 75.14% ±0.17 / 10 次**，反方给 58.44%）；Letta 无法复现 Mem0 当年把 LoCoMo 回填进 MemGPT 的做法。
   → **当前没有任何一个头条数字可信**；mem0 自承其分数来自**专有 Platform 代码**而非 OSS。

## 数据 5 · **无人做的那一格**（本调研最重要的产出）

1. **没有任何框架证明"记忆改变了行为"**。全部只发布 **recall 类基准**；**没有一个测量"在冻结策略下，累积的记忆是否提升后续任务成功率"**——而这**正是"新 agent 免培养"的定义**。
   最近的邻：**ForgetEval**（[arXiv 2606.15903](https://arxiv.org/abs/2606.15903)，MIT）：1000 条用例 + 385 条对抗层 + 六方法 adapter（~130 行）把异构存储插进同一 harness——**它测的是控制面（遗忘/保留），不是 recall**。
   另一条独立来源（Always-On Agents 综述，435 篇编码）也给出同一结论：**没有"长期是否有益"的可接受度量**。
2. **没有把"人"做成一等的、可寻址的对象**（带 consent / retention / correction 生命周期）。最近的邻：Letta 的 `human.md` 文件、mem0 的 speaker 列、Graphiti 的 `Person` 节点、Cognee 的 user principal、Hindsight 的 bank_id。**没有任何一个把"人"建模成记忆的所有者、并给出可携带的权利。**
3. **除了 Cognee 的 COGX 交换格式，没有跨存储可携性**；没有签名的、自描述的记忆包。
4. **没有任何一家有 decay / TTL 语义**——只有"失效"或"硬删"；**没有对"默认该忘什么"的原则性答案**。
5. **没有自我修改安全**：Letta 允许 agent 重写自己的系统提示，**没有评测、没有回滚闸门、没有 diff 审阅要求**（其"应用前由 agent 自查"不构成审批）。
6. **没有多 agent 冲突解决**——共享是"命名空间"（group_id / 共享仓），**不是合并策略**。另：**没有任何系统提供"agent 关于自己写了什么"的逐条审阅面**（Claude 的 Topics 只覆盖**用户**，不覆盖 agent 的规则）。

## 数据 6 · 可借用的现成物（含许可风险）

| 物件 | 来源 | 借什么 | 许可 |
|---|---|---|---|
| **ForgetEval + Adapter Protocol** | [arXiv 2606.15903](https://arxiv.org/abs/2606.15903) | 1000 用例 + 385 对抗 + 六方法 adapter（~130 行）；**测控制面而非 recall**——正对 L0 验证闸门的形状 | **MIT（最干净，可直接 vendor）** |
| **mem0 开放评测 harness** | [memory-benchmarks](https://github.com/mem0ai/memory-benchmarks) | LoCoMo / LongMemEval / BEAM 可跑；`--top-k-cutoffs`、token 计量、`--predict-only` / `--evaluate-only` / `--resume` → **本项目自造 51 条评测集缺的外部锚点** | Apache-2.0 |
| **LongMemEval / LoCoMo 题集** | 同上 + Hindsight 论文 | 外部可比锚点（**但别用它们的分数当目标**，见"分歧"第 4 条） | 见各自仓库 |
| **COGX 交换格式** | Cognee | 跨厂商记忆导入/导出（已有 Mem0/Letta/Zep/Graphiti adapter） | Apache-2.0 |
| **Graphiti MCP 工具 schema** | getzep/graphiti | `add_memory` / `add_triplet` / `search_memory_facts`(带 `valid_at`/`invalid_at`) / `get_episode_entities`(回指) / `build_communities` | Apache-2.0 |
| **MemFS 约定** | Letta | Markdown + YAML + `MEMORY.md` 索引 + git 提交即审计/回滚；**根文件每轮注入 ⇒ token 预算是硬约束**（`/doctor` 正是防它漂移） | Apache-2.0 |
| **哨兵块** `<!-- @generated -->` / `<!-- @user -->` | Obsidian 生态（obsidian-second-brain） | **生成区与手写区同文件共存，重跑只替换生成区**（"never overwrites the notes you added by hand"） | MIT 插件 |
| **`locked: true` → HTTP 423** | Basic Memory | **工具层的"此条不得改写"**——"自我/偏好域 agent 只读"的现成强制形态 | AGPL-3.0（**只借约定，不借代码**） |
| **新鲜度策略（OKM）** | Obsidian 生态 | 每条事实是 **timeless / dated / pointer** 之一，带 `status`/`stale_after`/`verified` + linter | MIT 插件 |
| **双时间戳** | Graphiti | `valid_at`/`invalid_at`（世界时间）vs `created_at`/`expired_at`（摄入时间） | Apache-2.0 |
| **`doctor`（file↔DB 差集）列为一等命令** | Basic Memory · Letta `/doctor` | 一致性核验做成一等命令 | 同上 |
| **确定性合并 / 无模型 pass** | ACE（[arXiv 2510.04618](https://arxiv.org/abs/2510.04618)）· open-second-brain 的 `dream`（"算法里没有模型"） | **合并能用确定性算法就别用 LLM 重写** | 见各自仓库 |
| **`bm wiki` / 定时 pass** | Basic Memory · obsidian-second-brain | 夜间巩固 / 周回顾 / vault 健康检查是**已有形态** | 同上 |

## 数据 7 · 失败证据（这些决定闸门形状）

| 失败 | 证据 |
|---|---|
| **记忆投毒无触发条件且持久** | [MemoryGraft](https://ar5iv.labs.arxiv.org/html/2512.16962)：110 条种子掺 **10 条毒记录** → **干净查询 PRP 47.9%**，**跨会话持续**直到清库 |
| **自强化误差** | [A-MemGuard](https://ar5iv.labs.arxiv.org/html/2510.02373)：LLM 检测器**漏掉 66%** 毒条目；点名"被污染的结果被存成先例"；共识校验把 ASR 压低 >95% |
| **自演化提高完成率但破坏安全** | [SEABench](https://arxiv.org/abs/2609.35596)：48 条纵向序列（控制器指令 / 记忆协议 / 工具技能），安全失败**在配对非演化 agent 身上不存在**；CoT 分歧是可用监测量 |
| **自演化** | LangMem `create_prompt_optimizer` 直接改系统提示，**流程里没有人工闸门**；出货的个人助理**没有一个默认这么做** |
| **生成视图静默永不执行** | Basic Memory 一个**已发布**的 wiki projection 特性，在**所有被 agent 写过的项目**上永远报 `partial`、`log.md` 永远空——**只有一次回归跑才抓到** |
| **阈值判重失效** | Basic Memory 报告："改写"与"仅仅相关"两类笔记**都落在 0.78–0.94**，故改为**追加一条"相似笔记"建议**而非硬拦 |
| **多写者文件重复** | Basic Memory [#1275](https://github.com/basicmachines-co/basic-memory/issues/1275)：macOS+Syncthing 的 **NFC/NFD 文件名变体**造出孪生实体，832 条笔记两周内 **34 对重复**；`reindex --full` 能合并但会复现 |
| **索引漂移可致错写** | Basic Memory [#765](https://github.com/basicmachines-co/basic-memory/issues/765)：陈旧 FTS 幽灵行熬过 `reset --reindex`；DB 失同步时 `write_note(overwrite=True)` **写到另一个文件并毁掉它**（10 个僵尸 MCP 进程） |
| **删除 ≠ 抹除** | ChatGPT 文档：删掉对话**不会**删掉由它派生的记忆——**"删除是检索层面的编辑，不是抹除"**，被综述列为独立失败模式 |
| **依赖腐化** | `mcp-obsidian` 在 `mcp>=2.0` 上直接崩（`Server` 无 `list_tools`） |

## 结论（**census 层面**，非架构决策）

1. **"主体 = 人"落在空位里**：15 个系统里没有一个把"人"做成**一等的、可寻址的、带生命周期（同意/保留/更正）**的对象。最接近的 cognee 是 `dataset + user principal`，memobase 是 per-user profile slots——**都不是"记忆的所有者 + 可携带的权利"**。
2. **愿景的第三块（要能改变行为、让新 agent 免培养）落在一个"没人测量"的区域**：全场只测 recall，**没有人在冻结策略下测"记忆是否提升后续任务成功率"**。这不是我们落后，是**这一格是空的**——而本仓库的 L0 可验证性闸门（没有可复现评测/回归 → 不进基线）正好是对着这一格的。
3. **文件型真相源阵营的共识与我们一致，且有一条支持性证据**：派生索引可重建是全部四框架的共识；Letta 用**普通文件 grep 工具**拿到自报 LoCoMo 74.0%，削弱了"必须专用存储"的论断。
4. **本项目的两个"稀缺项"已经落地**：Always-On Agents 综述（435 篇）把 **`validate` 列为"缺失的闸门"、`rollback` 列为"最稀缺的能力"**；而本项目在写入面**已有**校验前置 + 每写一个 commit + 干净回滚 + 审计归属——**`write_path_sandbox.py:382-401` 已证明"校验失败不落盘、HEAD 不动"与"`git reset --hard` 干净撤回"**。
5. **本项目没有、而外部已有现成形态的**（按证据强度）：**双时间戳**（Graphiti）· **内容锁**（Basic Memory `locked`）· **哨兵块**（Obsidian）· **新鲜度三分类 timeless/dated/pointer + `stale_after`**（OKM）· **确定性合并**（ACE / `dream`）· **`doctor` 一等命令**（Basic Memory / Letta）· **控制面评测**（ForgetEval，MIT）· **外部 recall 锚点**（mem0 harness）。
6. **风险面被外部证据点名**：本项目"全局 KB 所有 agent 可读写"正是 **MemoryGraft/A-MemGuard** 描述的投毒面（无需触发、跨会话持续、LLM 检测器漏 66%）；而**档③（让 agent 改自己的常驻规则）**正对 **SEABench** 的"提高完成率 + 破坏安全"。

## 未验证 / 已知不确定

- Hindsight 的"独立复现"（声称 Virginia Tech Sanghani Center + Washington Post）**未找到报告原文**；其分数为厂商自报。
- Gemini 的"已保存信息"逐条审阅面**未验证**（帮助页正文被截断）。
- **"agent 自己写 AGENTS.md"的流行度未验证**——那份 2,303 文件的研究测的是**人类维护**（59–67% 多次提交，演化形态是**小幅追加、删除极少，中位数 <15 词**）。
- Basic Memory 的生成文件如何避免被重新索引：**未验证**。
- Khoj 是否有官方 MCP 服务端：**未验证**（只找到第三方包装）。
- Letta `letta` 仓已退役、`letta-code` 才是现在（499 open issues）。
- 星数/issue 数是 2026-10-09 的快照。
- **全部 LoCoMo / LongMemEval / BEAM 数字视为厂商互撕状态，不作依据。**

## 资产与复原

本目录**只有本 README，无 `data/`、无 `store/`**（外部调研，非本地测量）。
全部资产是上面列出的外部 URL；无派生索引、无成本可省。
