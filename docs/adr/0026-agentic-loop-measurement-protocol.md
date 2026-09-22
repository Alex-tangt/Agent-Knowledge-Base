# 0026 agentic RAG：先量检索头寸，再谈机制与工具

Status: **accepted**（2026-09-17 owner 拍板）。

Relates: **#21**（检索优化叙事）、**#24**（检索评测基座）、**#42**（agent loop 已闭环）、
**#47**（本决策的执行票）、ADR-0017（不校无答案阈值）、ADR-0022（融合 / 池默认）、
ADR-0025 D19（读 = 整张基表 / 写 = 全局知识库）；调研见 `experiments/agentic-rag-loop/`。

## 背景

调研（`experiments/agentic-rag-loop/README.md`）已定：检索循环跑在**宿主 agent**，本包只交付
**SKILL.md + MCP 工具**；"强制多跳 / 充分性 / 终止"不在可及范围（`steps` / `permissions` 在宿主，
MCP 无此原语）。能做实的是两件：skill 策略（规劝，统计测）与**确定性工具信号**（可测）。

原计划的"先量"——在 #24 的 51 条集上跑 `hops=1/2/3`——**不成立**：核对该集排名，
59 个标注相关条目 **@k=5 覆盖 91.5%、@k=10 覆盖 96.6%**，k=10 唯一缺口是 q035 两份同名跨仓文件；
且每条 query 由**单一 `source_entry`** 生成，**结构上表示不了多跳**。一次性检索已近满覆盖，
在现有集上跑多跳是空实验。

## 决策

- **D1 先量检索头寸、分两层**：层 1 = 确定性检索 census（不调 LLM）；层 2 = 端到端 host-agent eval
  （LLM 在环、holdout、统计）。**层 1 显示头寸才做层 2**。
- **D2 层 1 语料 = 外部 MultiHop-RAG**（COLM 2024，ODC-BY；2556 query / 609 篇 / gold evidence
  跨 2–4 篇 / inference·comparison·temporal·null）：本语料内不存在多跳集，自造成本高且有出题偏差。
  隔离用**独立 store / collection**，不用尚未实现的具名视图（ADR-0025 D14）。
- **D3 层 1 指标 = gold-evidence recall@k（分题型）**，确定性、**不改检索合成**。
- **D4 预登记信号**：recall@k ≈ 1.0 → agentic loop 作为*检索*问题关闭（至多留"诚实弃答"条款）；
  显著 < 1.0 → 有头寸，再判是**查询表述**（分解 / 迭代可救）还是**索引 / 嵌入**（救不了），才排层 2。
- **D5 外部语料只能反证、不能正证本 KB**：它规模更大、多跳更密集——它无头寸则我们更不可能有；
  它**有头寸也不证明我们有**。域 / 语言 / 题型不可迁移；它测**引擎**，不测包边界。
- **D6 范围 = 只 skill + 读侧确定性工具，不碰检索默认 / 合成**（沿 2026-09-16「主线不碰检索」）。
  `memory_gather` 出局；`memory_verify` **暂不建**，若建只做确定性判据（supersede 链完整性、
  残留退役项、近重复 `current` 提示、命中描述量），**不做** `sufficient: bool`、不做语义矛盾判定、不调 LLM。
- **D7 A（SKILL.md 显式化循环规则）暂缓**：等 census 划定机制缺口再写，避免先写再测。
- **D8 宿主 enforcer（opencode `steps` / `permissions`）= 部署决定、显式非目标**；安装器不写宿主 `steps`。

## 理由

- 分钟级确定性测量先于贵运行（「廉价测量优先」）；census 能排除、不能证明。
- 不动检索：检索默认归 #21；无端到端证据前改合成，是不确定性换不确定性。
- 外部基准换掉自造集的出题偏差，代价是 D5 的方向性局限——**不得当产品增益引用**。

Considered options：

- **A 自造本语料多跳集（备选）**——in-domain，但成本高、偏差大；层 1 有头寸后如需 in-domain
  佐证可补小集。
- **B 外部 MultiHop-RAG census（采用）**。
- **C 直接做层 2 端到端（弃）**——未先证检索头寸，贵且理由不足。
- **D 在现有 51 条集上跑 hops（弃）**——背景已排除的结构性空实验。

## Consequences

- 需一条"外部语料 → 独立 store"的接缝（现 `retrieval_eval.py --rebuild` 走死 `load_corpus()`）；
  执行步骤与验收见 **#47**。
- 层 1 结论进 `experiments/`（一页）+ `memory_agent/eval/`；判"有头寸"才开层 2 票。
- 数据集不入库（gitignored），遵守 ODC-BY 归属。
- 本 ADR 不修改 ADR-0025；与其"读侧可见域、写侧单目标"一致。

## 追加（2026-09-17）：D7 的 A 已落地

D7 暂缓的 **A（SKILL.md 显式化循环规则）** 已按 **Phase B 的机制缺口**写入
（`memory_agent/skill/SKILL.md`「迭代检索」节）：

- **两跳预算**（默认 ≤2 次追加检索）——hop 曲线显示两跳拿走大部分增益；
- **迭代而非改写**——裸改写无可靠增益；下一跳 query 必须由**已检索到的证据**里的缺口 / 实体导出；
- **停止 = 条件 OR**（要点覆盖 ∪ 无新 id ∪ 预算）+ **别早停**（早停 35.8% 是主要失效）；
- **证据不足就明说**（不硬答）。

执行结果（#47 D4 有头寸 / #48 三臂）见 `experiments/agentic-rag-census/`。
**skill 是规劝、非强制**；统计验证（场景评测）后置。

## 追加（2026-09-20）：宿主能力核实 + "不污染主对话"的落地形态

背景：主对话里跑迭代检索会把 hop / 召回块 / judge 输出灌进主上下文（Phase B 证据上限
20 篇 × 6000 字 ≈ 120k）。曾讨论两种"把循环挪出主对话"的方案：① MCP sampling 驱动的
服务端 loop；② 宿主侧隔离 subagent。核实（一手源码 + 官方文档）后拍板：

- **D9 服务端 loop（sampling）在本宿主不可行，本阶段不做**。opencode 的 MCP 客户端
  （`packages/opencode/src/mcp/index.ts`）`capabilities` **只声明 `roots`**；`sampling` /
  `elicitation` / `tasks` 均被注释掉（跟踪 issue #11948 / #23066 / #28567）。未声明
  capability → server 发 `sampling/createMessage` 不可用。**换宿主**（支持 sampling 的
  客户端）才重开此路。
- **D10 "不污染主对话"用 opencode 原生 subagent 落地**（`mode: subagent` + Task 工具 →
  独立子会话；`docs/agents`）。交付形态 = 随包的 `memory-research` agent 定义 + 落位
  （随 skill 一起，沿 ADR-0028 D4 的幂等 / 备份 / `--dry-run` 纪律）。**不新增服务端 LLM、
  不动 daemon 确定性、不改检索合成。**
- **D11 模型默认继承；Task 无按次 model 参数**（`tool/task.ts`：`model = next.model ??
  调用方 model`）。要"实验里用指定模型" → 用**第二个 agent 变体**（写死 `model`），主 agent
  靠 `subagent_type` 选，等价于"派发时指定"。**默认交付的 agent 不写 `model`**（继承）。
- **D12 subagent 工具面用白名单**：`permission` 评估为 `findLast`（最后匹配胜出）+ 配置
  插入序（`permission/index.ts`）→ 写法是 **`"*": deny` 在前、具体 `allow` 在后**；只放
  **只读**记忆工具（`memory_search` / `memory_get` / `memory_index_status`）+ `skill`。
  写工具一律不放（写需用户在主机确认，不属该子代理职责）。

**仍不解决的**（如实标注）：subagent 只做**上下文隔离**与**成本上限**（agent `steps`，
部署建议、不由包强制）；**不修早停**，也不构成对 skill 行为的验证——那仍需 in-package
harness + in-domain 多跳集（D1 层 2 的剩余部分，另立票）。**D8 不变**：宿主 enforcer
仍是部署决定、非包能力；`steps` 只是写进 agent 定义的建议值。

## 追加（2026-09-21）：D10 补充——插件工具内驱动子会话（形态 B）

结论（**PASS**，限重入 / 控流）：在 opencode **插件工具**的 `execute` 调用栈里回调
`client.session.create({ parentID })` + `client.session.prompt({ agent })` **不重入、不死锁**；
子会话结果经**工具返回值**回到主会话；`session.children(parentID)` 可核到该 child。
即"**委派 + 代码控流 + 返回**"在插件层可行。**据此采纳形态 B**（实现见 **#61**）。

- 证据：`experiments/opencode-plugin-subagent/`（worktree `wk-60-plugin`，opencode **1.18.31**，
  `@opencode-ai/plugin` 1.18.31，模型 `deepseek/deepseek-flash`）；P0–P4 原始观测见 `results.md`。
- 与 **D10（形态 A：随包 agent `.md` + Task）** 的关系：**采纳形态 B（插件工具内驱动子会话）**
  为交付形态（`memory_agent/plugin/memory-research.js`，工具 `memory_research`）；
  **形态 A 保留为无插件环境下的 fallback**（同一子代理定义 `.md` 两用）。
  - 接口：**只返回文本**（消费方是主 LLM；无固定格式 / 成分需求；结构化输出还会撞 provider
    的 `tool_choice` 限制，见边界 1）。`id` 依据沿用提示词里的**纯文本约定**，不上升为 schema。
  - 控流：**hop 预算由插件控**（插件循环调子会话，用 `NEXT_QUERY:` 文本协议续跳）；
    单跳内部的策略仍是子代理提示词。
  - 执行语义：**与原生 subagent 的默认前台模式一致——阻塞**（`execute` 内 `await
    session.prompt`；父会话在 await 期间挂起，返回后把文本作为 tool result 才继续本轮）。
    **不支持后台模式**：插件 `ToolContext` 无 background 原语，Task 的可选 `background: true`
    不适用于本工具。长调用以 `metadata()` 报进度、受 hop 预算与**超时**约束、可 `abort`。
  - 相比 skill 的规劝，B 是**代码契约**（委派与预算由我们的代码定）；代价是**插件随包分发**，
    且插件**运行在宿主进程内、信任级高于外部 MCP daemon**（owner 2026-09-21 采纳）。
- 边界（如实标注）：
  1. `format: json_schema` 结构化输出在当前模型**不可用**——provider 返回 400
     `Thinking mode does not support this tool_choice`（`info.error`，`structured_output=null`）；
     需换支持该 tool_choice 的模型，**与重入无关**（同一调用正常返回并带回错误）。
  2. 本 spike 只用一次性 `session.prompt` 返回，**未验证**流式 / 中途事件。
  3. 仍只解决**隔离与控流**，不修早停、不构成对 skill 行为的验证（同 D10 与 #60 后置）。
  4. **隔离未被独立验证**：探针只回传子会话最终文本，主会话结构性看不到召回块——**设计使然**，
     未对父会话消息列表做断言（H2 记为"未验证"）；#61 的宿主 E2E 须补此断言。
  5. P2 的记忆检索结论受工具 `-32001` 超时污染，**不作为检索质量证据**引用。
- **D8 不变**：宿主 enforcer / 插件随包分发仍是部署决定，非包能力。
