# 子报告 C：循环在宿主 agent 时，skill + 工具面能做到什么

> 由研究子代理产出的**原始报告**（2026-09-16，未编辑）；综合与结论见同目录 `README.md`。
> 标 `[推断]` 的是子代理的推断，其余有明文出处。来源标签：`[SKILLS-OV]`/`[SKILLS-BP]`/`[SKILLS-BLOG]` = Anthropic Skills 官方文档 / best practices / 工程博客；`[AGENTS-BLOG]` = Anthropic「Building effective agents」；`[TOOLS-BLOG]` = Anthropic「Writing tools for agents」；`[MCP-TOOLS]` = MCP 规范（2026-07-28）；`[OC-AGENTS]` = opencode agents/skills/MCP 文档；`[CC-SUBAGENTS]`/`[CC-HOOKS]` = Claude Code subagents/hooks 文档。

## 1. 三层能力表

| 能力 | 靠什么实现（层级） | 可否强制 | 可否确定性测 | 来源 |
|---|---|---|---|---|
| **多步工作流/清单**（多跳检索、先搜后写、逐条核对） | skill 规则：纯提示词步骤 + checklist | **否**。skill 文本是"指示"，无约束力 | 否（模型行为是统计量）；但**其依赖的工具原语可确定性测** | `[SKILLS-BP]`「Research synthesis workflow」「checklist」；`[AGENTS-BLOG]` workflow vs agent 之分 |
| **条件分支/路由**（有替代链→取新弃旧；无链接冲突→交人） | skill 规则；可加工具信号 | 否 | 否（对模型）；原语可测（本仓库 #42 已测 `exclude_retired` + 冲突语义） | `[SKILLS-BP]`「Conditional workflow pattern」；`agent_loop_42.py` |
| **调用脚本**（跑 `build_index.py` / 校验脚本） | skill 规则 + 宿主 bash 工具（Anthropic 侧叫 bundled scripts，**由 bash 执行、只回输出**） | 否（"让 agent 跑"，不是它一定跑） | 脚本自身输出可确定性测 | `[SKILLS-OV]`「Level 3 / Skills and code execution」；`[SKILLS-BLOG]`「deterministic reliability that only code can provide」。⚠️ opencode **未文档化** skill 打包脚本；只能靠宿主 bash 工具跑仓库里的脚本 `[推断]` |
| **把状态写文件**（scratch/plan 文件） | skill 规则 + 宿主 write 工具；或工具侧 stateful handle | 否 | 文件落地可测；"是否写"不可测 | `[SKILLS-BP]`「plan-validate-execute / changes.json」；`[MCP-TOOLS]`「Stateful Tools：创建工具返回 handle，模型负责带回来」 |
| **自我检查清单 / 反馈循环**（validator→fix→repeat） | skill 规则 | 否 | 否 | `[SKILLS-BP]`「Implement feedback loops」「Only proceed when validation passes」 |
| **命中带 owner/provenance** | 工具信号（server 计算，已实现） | **是**（数据必然出现） | **是** | 本仓库 #45/D19；`[TOOLS-BLOG]`「return meaningful context」；`[MCP-TOOLS]` annotations |
| **工具返回下一步建议 / 错误提示** | 工具信号（`hint` 已在 `confirmation_required` 用） | 否（模型可无视） | 提示文本可单测；**采纳不可** | `[TOOLS-BLOG]`「prompt-engineering tool descriptions/error responses」；`[MCP-TOOLS]` isError 可自纠 |
| **只读 verify 工具（确定性判据）** | 引擎/工具信号：把"充分性/冲突"写成代码 | **部分**：工具结果确定；**是否调用**仍靠规劝 | **是**（工具部分） | `[TOOLS-BLOG]`「工具可 consolidate、把 agentic 计算挪进工具」；`[MCP-TOOLS]` structuredContent |
| **hop 预算 / 进度状态** | 工具信号（stateful handle + 服务端计数） | 否；**MCP 无终止 host 循环的能力** | 计数器本身可测 | `[MCP-TOOLS]`「Stateful Tools」；反向证据：规范只说 handle 是普通字符串，模型负责传递 |
| **自主终止（够了就停）** | skill 规则（停止条件）；宿主 `steps` 上限才是硬闸门 | **否**（我们）；**是**（宿主配置，非本包）| 场景级统计测；硬上限是宿主行为 | `[AGENTS-BLOG]`「stopping conditions (max iterations)」；`[OC-AGENTS]` `steps`、permission `ask/deny` |
| **子代理/独立上下文做验证** | 宿主能力（Claude Code subagents；opencode Task） | 是（宿主 tool 限制） | 是 | `[CC-SUBAGENTS]` `tools`/`disallowedTools`/`maxTurns`(部分结果标记) |
| **代码强制拦截工具调用** | **宿主 hooks**（Claude Code `PreToolUse` 可 `deny`；skill frontmatter 可注册 hooks） | **是**（但仅 Claude Code，且非本包） | 是 | `[CC-HOOKS]`「Hook locations：Skill frontmatter」+ `permissionDecision: deny`。⚠️ opencode 无 hooks 文档 `[推断]` |

**一句话**：skill 层全是**规劝**；工具层能给**确定性的信号与判据**；真正的"强制"只在**宿主配置**（opencode permissions / `steps`，Claude Code hooks）里，**不在我们能发布的包里**。

## 2. 工具面设计选项（现工具面：`memory_search/get/add/supersede/archive/ingest_*/reindex/index_status`）

> 所有选项都只改"我们这侧"；注意 owner 已定「主线不碰检索」（2026-09-16「B」），凡动检索**合成**的都需 owner 放行。

| 选项 | 做什么 | 代价 | 风险 |
|---|---|---|---|
| **A. 命中加描述性信号**（如事后 `score` 分布、`n_hits`、`distinct_owners`、`has_conflict`） | 给模型看"证据够不够"的**事实**，不给结论 | 小（server 计算 + docstring） | 若做成 `sufficient: true/false` 的**布尔判据**＝引入未校准的策略阈值；ADR-0017 明确不对无答案 query 校阈值。**只给描述量** |
| **B. 返回 `next_steps`/`hint`** | 工具主动建议下一步 | 很小 | 注入面 + 模型可能盲从；建议易过期（token 成本）。建议**只在中立分支**给（如 `duplicate`/`confirmation_required` 已这么做） |
| **C. 独立只读 `memory_gather`**（服务端 fan-out 多 query + 去重 + 合并） | 把"多跳"从模型挪进**确定性代码**，减少 host hop/上下文 | 中～大：**改变检索合成**（冻结区）；新工具面重叠 | 与 freeze 冲突；Anthropic 明确警告"工具过多/重叠会分散 agent"。收益是省 hop，**不是**能强制多跳 |
| **D. 独立只读 `memory_verify`**（确定性判据：无证据？有未裁决冲突？supersede 链完整？） | 把"充分性/冲突裁决"变成可测的代码判据 | 中：要定义判据；可能与 `memory_get` 重叠 | 判据若用 LLM = 重新引入非确定性（daemon 特意不调 LLM）；若判据太浅 = 假安全感。**只做纯确定性检查** |
| **E. hop/预算/进度**（`memory_search(plan_id=...)` 返回剩余预算） | 让模型看到"还能搜几次" | 中：要 state + 过期语义；MCP 无 session | 服务端无法知道宿主预算；**无法终止宿主循环**——更像幻觉。`memory_reindex` 的 `cursor` 是同类正例，但那是**算法续调**不是预算 |
| **F. 结构化输出**（`outputSchema`/`structuredContent`） | 让客户端/评测机解析更稳 | 小 | 需客户端支持；opencode 是否消费 `structuredContent` 未文档化 `[推断]`。对**确定性评测**有价值 |
| **G. 错误信息即引导**（ToolError 已具雏形） | 失败时给可执行修正 | 很小 | 无。MCP 明文鼓励 `isError` 可自纠 |
| **H. 安装器写宿主策略**（opencode `permission` / `steps`） | 把"规劝"升级为**真闸门**（如 MCP 工具 `ask`、步数上限） | 小（`connect.py` 已有写宿主配置的先例） | 改用户配置、跨工具不一致；属**部署决定**不是包能力 |

## 3. 可测性：怎么对"提示词驱动的循环"做确定性评测

- **官方范式**（`[TOOLS-BLOG]`）：用 `while` 循环包住 LLM+tool 的 **eval agent**，每题配**可验证 outcome**（精确串比 → 可上 LLM judge）；同时收 **tool-call 数 / token / 错误 / 耗时**；用**留出集**防过拟合；**不要**规定唯一工具序列（多条正解）。
- **官方范式**（`[SKILLS-BP]`）：先建 3 个 eval 场景 + `expected_behavior` 清单；并**明确承认"目前没有内置 runner，需自建"**。
- **本仓库已有正例**：`memory_agent/eval/agent_loop_42.py` —— 真 MCP + 沙箱 + Stub 嵌入 + **不调 LLM 判分**，只断言**外部行为**（文件/git/命中顺序）。这是"对工具与 skill 原语做确定性测"的模板；**能扩展成场景集**。
- **诚实的边界**：「多跳/充分性/终止」的**策略选择**在模型里，只能做**统计性场景评测**（通过率、平均 hop 数），**没有确定性 pass/fail**。把确定性寄托在**工具原语**上（`exclude_retired`、`owner`、冲突链、`memory_get` 回溯）——这正是 #42 做的。
- **可靠续调**（确定性"循环"）：`memory_reindex` 的 `cursor` 续调到 `done=true`，是 MCP「stateful tools」官方模式；这类**算法循环**能确定性测。

## 4. 明确归类

**[skill 就能做]（规劝，可场景评测）**
多步工作流/清单、条件分支、冲突裁决政策、停止条件措辞、让 agent 去跑仓库脚本、plan/scratch 文件纪律、把 `owner`/`status`/`supersedes` 用作判断依据。→ 本仓库 SKILL.md 已基本覆盖；增量是**把停止条件显式化**（"≥2 独立来源一致才回答；否则明说没查到"）。

**[需新增只读工具]（把判断变确定、可单测）**
`memory_verify`（纯确定性：缺证据 / 未裁决冲突 / supersede 链）；可选 `memory_gather`（合并多跳，**动检索合成须 owner 放行**）；命中加**描述性**信号（勿做充分性布尔）。**注意**：这些都只是"提供判据"，**是否调用仍不可强制**。

**[需求改引擎/数据库]（本项目暂不需要）**
服务端**强制**多跳 / 强制充分性 / **终止宿主循环** / hop 预算闸门 / 服务端 LLM 裁判。MCP 没有这些原语；它们在**宿主**里（opencode `steps`、permissions；Claude Code hooks），或需要真 DB 的会话状态。→ 按 ADR-0025，本项目不做。

## 5. 结论：值得做 vs 幻觉

**值得做（低代价、可诚实测、与现状不冲突）**
1. **SKILL.md 显式化循环规则**：多跳触发条件、冲突裁决、**停止条件**、失败即回退。纯文本、零依赖，用 #42 风格场景集回归。
2. **只读 `memory_verify`（只做确定性检查）** —— 把"充分性/冲突"从模型直觉挪进可测代码，是 Anthropic「把 agentic 计算塞进工具」的正解。
3. **命中描述性信号**（分布/来源数/是否有冲突字段）而非布尔结论。
4. **错误信息即引导**（已有雏形）——最低成本的"使能"。
5. 若 owner 放行：**`memory_gather` 合并多跳**（省 hop、省上下文），但明确它**不能强制**多跳。

**是幻觉（别做）**
1. **靠 MCP 工具/服务端强制多跳、充分性判断、或终止**——宿主循环不在我们手里；`steps`/permissions 是**宿主配置**，且 opencode skills **不支持 hooks/脚本**（只有 name/description/metadata）。
2. **服务端 hop 预算**假装能终止循环——MCP 无此能力，规范里 handle 只是普通字符串。
3. **`sufficient: bool` 或服务端 LLM 判分**——前者引入未校准阈值（违反 ADR-0017 精神），后者破坏 daemon「不调 LLM」的确定性。
4. **对"模型是否按 skill 做了循环"要求确定性 pass/fail**——只有**工具原语**能确定性测，模型行为只能统计测（带留出集）。
5. 期待 **skill 文本有约束力**——官方从未承诺；约束只在宿主 hooks/permissions。

> 一句话结论：在"循环在宿主"前提下，我们能把 agentic **做实的只有两件事**——(a) 用 SKILL.md 把**策略讲清楚**（规劝，靠场景评测量化），(b) 用**确定性工具**提供**判据与信号**（可强制出现、可单测）。**强制多跳/充分性/终止**不在我们可及范围，属宿主或引擎，做了也是自欺。
