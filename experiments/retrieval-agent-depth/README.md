# retrieval-agent-depth — 文档检索 agent「做深」调研 + 决策菜单

- 日期：2026-09-22
- 触发：owner 指出 **LLM 已进我们的代码（插件内跑子会话）**——ADR-0026 里"循环在宿主、
  只发 skill + MCP 工具、强制多跳/终止**不在可及范围**"这一约束**已解除**。可在 **in-package
  harness / runtime** 里以**代码**实现机制（不再只是提示词规劝）。
- 问题：在不脱离本项目背景（记忆能力包 / 文件+git 基表 / MCP / 生命周期 / 治理）的前提下，
  **如何把"文档检索 agent"做深、且是可上简历的深度**？
- 方式：外部一手来源（Anthropic 工程博客、Agentic RAG survey）+ 本仓库既有调研与实测。
  **不是实验**，是方向调研；结论供 owner 拍板后拆票。

## 0. 解锁了什么（before → after）

| | ADR-0026（进行时） | 现在 |
|---|---|---|
| 循环在哪 | 宿主 agent（opencode），我们只发 **skill + MCP 工具** | **我们的代码**（插件/runtime 持有 LLM 循环） |
| 能强制 | 只有**确定性工具信号**；hop/终止/充分性靠规劝 | **预算 / 终止 / 委派 / fan-out / 校验**都可落成**代码契约** |
| 能测 | skill 行为只能靠场景统计（D1 层 2 难做） | 可建**真 harness**：跑 LLM+工具循环、记录轨迹、可复现、可评分 |
| 结论 | 只解决**隔离与控流**（#60/#61） | 可攻**检索质量本身**（机制 + 校验 + 评估） |

> 一句话：从"**给 agent 递工具 + 写提示**"升级为"**自己造 agent**"。

## 1. 外部一手：什么让"检索 agent"真的变深

**Anthropic《How we built our multi-agent research system》(2025-06)**
- 检索的本质是**压缩**：subagent 用**独立 context** 并行探索再压缩关键 token 回传 → 减路径依赖、
  分离关注点。**深度来自"多花 token + 分开上下文"**：BrowseComp 上 **token 用量单独解释 80% 方差**，
  工具调用数 + 模型选择是另两个因子；多 agent ≈ **15×** chat token（延迟可用并行降 ~90%）。
- **工具设计（ACI）= 一等公民**：工具描述差会让 agent 走错路；用**测试 agent**反复试工具、重写描述，
  任务耗时降 **40%**。
- **评估**：**小样本立刻开评**（~20 条即可看出大效应）；**LLM-as-judge + rubric**（忠实/引用/完整/来源/
  效率）+ **人在环**抓自动评估漏掉的边角；**终态评估**（看最终状态，不规定唯一路径）。
- **长时上下文**：把**计划写进 memory** 防截断；接近上限时**开干净上下文的子 agent + 交接**；
  **子 agent 输出落文件系统**（artifact）避免"传话游戏"丢保真。

**Anthropic《Building effective agents》(2024-12)**
- 分清 **workflow（预定义代码路径）vs agent（模型自定路径）**；能用简单模式就别上复杂框架。
- 模式菜单：prompt chaining / routing / **parallelization** / **orchestrator-workers** / **evaluator-optimizer** /
  autonomous agent。**只在可测地更好时才加复杂度**。
- **ACI**：像写 HCI 一样投入工具设计（参数名 / 描述 / 示例 / 边界）；**poka-yoke** 让错误更难发生。

**Agentic RAG survey（arXiv:2501.09136，v4 2026-04-01）**
- 分类轴 = **agent 基数 / 控制结构 / 自治度 / 知识表示**；四类设计模式 = **reflection / planning /
  tool use / multi-agent collaboration**。
- 开放挑战点名：**评估、协调、记忆管理、效率、治理**——正好是本项目的既有资产。

**本仓库既有调研**（`experiments/agentic-rag-loop/`，2026-09-16，未重复）：
IRCoT / Self-Ask / ReAct / **充分性分类 + 弃答**（2411.06037，模型默认"不足也硬答"）/ CRAG /
FLARE（**过度检索有害**）/ **Adaptive-RAG 早停 31%** / Self-RAG（需训练，不可移植）。
→ 可迁移三条：**终止 = 多条件 OR**、**gap-driven 下一跳**、**显式充分性 + 弃答分支**。

## 2. 本项目的独特杠杆（通用 RAG 作品集没有的）

1. **基表 = 文件 + git**（ADR-0025）：条目是**版本化纯文本**，有 **git 历史**、可 diff / blame。
   → 检索可以**像人一样导航**（list/grep/read/history/links），不止 top-k 向量。
2. **生命周期**（ADR-0009/0010）：**supersede 链 / archive / 状态**。
   → agent 必须**对"版本 / 冲突 / 时效"推理**（取新弃旧；无链接的冲突交人裁决）。**这是稀缺角度。**
3. **治理**（ADR-0018/0019）：`tenant` / `classification` / `residency` / `owner` / **审计**。
   → 检索 agent 天然有**权限与来源归属**维度。
4. **多消费者共享 daemon**（#41/#44）：同一基表 + 派生索引服务多个 agent。
5. **测量纪律**（ADR-0021/0026）：bootstrap CI、预登记信号、`run_hash` 确定性。
   → 任何"做深"都能给**统计显著**的结论，而不是 demo。

## 3. 深度候选（机制菜单）

> 每条 = 一个可独立验收的深度方向。**平台（H）是公共基座**，A–D 是机制。

### H — 平台：in-package harness + runtime（公共基座）
- **做什么**：把"插件控流"泛化成**真 runtime**：工具注册表 + **代码控的循环**（预算/终止/委派）+
  **轨迹落盘（record）与重放（replay）** + 一套**可验证 outcome** 的**场景评测 harness**（LLM 在环、留出集、
  统计）。即 ADR-0026 D1"层 2"的剩余部分。
- **证据/依据**：Anthropic 评估法（小样本先开评、LLM-judge+rubric、终态评估）；本仓库 `agent_loop_42.py`
  已是"沙箱真 MCP + 不调 LLM 判分"的雏形。
- **代价**：中（工程为主，不碰检索默认）。
- **简历点**：**"自建 agent runtime + 可复现评测 harness"** —— 这是把其他所有深度**变成可信数字**的前提。

### A — 检索工具化：agentic search over 文件+git（机制深度）
- **做什么**：给 agent **导航类工具**：`memory_list`（按 树/owner/tags/section 浏览）、`memory_grep`
  （词法精搜）、`memory_read`（按行范围读回条目）、`memory_outline`（标题结构）、`memory_history`
  （git log/diff/blame）、`memory_links`（supersede / 相关链）。agent **由宽到窄**地导航，而非只吃 top-k。
- **证据/依据**：Anthropic"start wide then narrow"；ACI 是深度来源；本体 files+git 天然支持。
- **代价**：中低（多数是只读工具 + 现有 loader/store 复用）。**不碰检索默认**（只增工具）。
- **可测**：多跳 / need-point 题上，**agentic search vs 一次性检索**的 recall / 答案正确率（用 H 的 harness）。
- **简历点**：**"为版本化文档库设计检索工具集（ACI）+ 量化 agentic search 增益"**。

### B — 可信回答：grounded verification（信任深度）
- **做什么**：**生成与校验分离**——每条 claim 必须挂 `id`；**确定性/半确定性校验器**检查
  claim ⊆ 被引条目、标出**无支撑**claim、**充分性/弃答**、**冲突/时效提示**。形如 evaluator-optimizer /
  Anthropic 的 CitationAgent。
- **证据/依据**：2411.06037（充分性 + 弃答 +2~10%，模型默认硬答）；Anthropic rubric 含"引用准确 / 忠实"。
- **代价**：中。
- **可测**：**引用精确/召回、无支撑 claim 率、拒答正确率**（部分可确定性校验）。
- **简历点**：**"claim 级引用 + 校验阶段，量化降低幻觉 / 提升可归因性"**。

### C — 版本化知识：冲突 / 时效推理（**独有角度**）
- **做什么**：agent 必须处理**同一事实的多个版本**：supersede 链完整性、**无链接的 `current` 冲突**
  （呈现 + 请人裁决）、**时效**（`mtime` / git 时间 vs claim）、**取新弃旧**。配**确定性检测器**
  （链完整性 / 近重复 current / 陈旧项）与**植入冲突/版本的评测集**。
- **证据/依据**：本项目 ADR-0025 D19「冲突裁决」已是规则；经典 RAG 语料**没有版本轴**——这是差异点。
- **代价**：中（规则 + 评测集设计是主要工作）。
- **可测**：冲突检出率、取新弃旧正确率、不可裁决时的**弃答/交人**率。
- **简历点**：**"面向版本化知识库的冲突/时效感知检索，含确定性检测 + 人在环裁决"**。

### D — 深度研究：orchestrator + 并行 subagent（规模深度）
- **做什么**：planner 拆子问题 → **并行 subagent**（独立 context）→ 压缩回传 → artifact 落盘防传话丢失。
- **证据/依据**：Anthropic 多 agent（+90.2% 内部评测、15× token）；orchestrator-workers 模式。
- **代价**：**高**（token / 延迟；Anthropic 明言需高价值任务才划算）。
- **前提**：**in-domain 多跳评测集**（ADR-0026 D5：外部语料只能反证、不能正证本 KB）。
- **简历点**：**"多 agent 深度研究系统（规划 / 并行 / 压缩 / 产物）"**。
- **风险**：本语料小、多跳密度未知；无 in-domain 集时**无法证明增益**，易沦为 demo。

## 4. 建议路径（供裁决，非结论）

**推荐 thesis（一句）**：把记忆能力包从"检索工具 + 提示规劝"升级为 **一条我们自己控循环的
检索 agent runtime**，深度落在 **三根轴：agentic search（机制）→ grounded verification（信任）→
versioned/conflict（独有角度）**，用 **H 的 harness** 给出显著数字。

**序**：**H（薄）→ A → B → C**；**D 暂缓**（需先有 in-domain 多跳集，且成本最高）。
**冻结区不动**（共享/云 #38/#39/#34/#55）；**不碰检索默认 / 合成**（沿 ADR-0026 D6），
新能力走"**加只读工具 + 代码循环**"，与 ADR-0025/0026 一致。

**验收锚点（每轴都能独立验收）**：
- H：harness 可**重放**一次 agent 轨迹（`run_hash` 确定性）+ 场景集有**统计结论**。
- A：某类题（多跳 / 精确引用）上 agentic search **显著优于**一次性检索（bootstrap CI）。
- B：引用精确/召回达标、无支撑 claim 率下降、拒答正确。
- C：冲突/版本集上检出率 / 取新弃旧正确率达标，不可裁决时**不自动择一**。

## 5. 需 owner 拍的（真选项）

| 选项 | 深度轴 | 代价 | 主要风险 | 简历叙事 |
|---|---|---|---|---|
| **E+（推荐）** | H + A + B + C 分阶段 | 中 | 面铺太宽，需按票收窄 | 自建检索 agent runtime + 三轴深度，数字显著 |
| **A 单轴** | agentic search | 中低 | 只增工具，深度偏"工程" | 版本化文档库的检索工具集 + ACI |
| **B 单轴** | grounded verification | 中 | 校验器需设计，易与 legal 口径混 | claim 级引用 + 校验，降幻觉 |
| **C 单轴** | version/conflict | 中 | 需植冲突评测集（设计成本） | 冲突/时效感知检索（**最差异化**） |
| **D 单轴** | multi-agent | 高 | 无 in-domain 集则无法证明 | 多 agent 深度研究系统 |

**待定项**：① 是否先立 **H** 为独立票（公共基座）；② in-domain 多跳/冲突评测集**谁来建、何时建**
（D 与"证明增益"的前置）；③ thesis 选 E+ 还是单轴。

## 来源

- Anthropic《How we built our multi-agent research system》(2025-06-13)、
  《Building effective agents》(2024-12-19)。
- Agentic RAG survey `arXiv:2501.09136`（v4 2026-04-01）。
- 本仓库：`experiments/agentic-rag-loop/`（deep-research 对照 + 机制论文 + 能力边界）、
  `experiments/agentic-rag-census/`（#47 头寸 / #48 三臂：迭代 +10.2pp 答案、裸改写无增益、早停 35.8%）、
  `docs/adr/0025`（基表/生命周期）、`docs/adr/0026`（测量协议）、`docs/adr/0027`（文档解析/收录）。
