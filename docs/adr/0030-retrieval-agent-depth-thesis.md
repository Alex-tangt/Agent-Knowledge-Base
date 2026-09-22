# 0030 检索 agent 做深：in-package harness/runtime + 三轴深度（H→A→B→C）

Status: **proposed**（2026-09-22 owner 选定路线 E+ 与首票 H，待接受/改）。

Relates: **ADR-0026**（agentic RAG 测量协议；本 ADR 修订其"循环在宿主、机制不可及"的约束前提）、
**ADR-0025**（基表 = 文件 + git；生命周期；唯一可写全局 KB）、**ADR-0019 / 0018**（存储端口 / 治理）、
**ADR-0027**（文档解析与收录）、**#60 / #61**（形态 A/B：隔离 + 控流）；调研见
`experiments/retrieval-agent-depth/`。

## 背景

**触发**：LLM 已进**我们的代码**（插件内驱动子会话，`memory_agent/plugin/memory-research.js`）。
ADR-0026 的约束前提——"循环跑在宿主 agent、本包只交付 skill + MCP 工具、**强制多跳/充分性/终止不在
可及范围**"——**已解除**。循环归我们，机制（预算 / 终止 / 委派 / fan-out / 校验）**可落成代码契约**，
**in-package harness / runtime**（ADR-0026 D1 的"层 2"剩余部分）**可做**。

**现状**：检索 = 条目级混合（BGE-M3 + BM25 + DBSF）；agent 面 = **skill 规劝**（迭代 ≤2 跳）+
**子代理隔离**（形态 A / B）。实测（#48 Phase B，外部语料）迭代 +10.2pp 答案 / +9.1pp 召回、
裸改写无增益、**LLM 早停 35.8%**——机制证据，**非产品增益**（ADR-0026 D5）。

**问题**：在不脱离本项目背景（记忆能力包 / 文件+git 基表 / MCP / 生命周期 / 治理）下，如何把
"**文档检索 agent 做深**"、且是**可上简历的深度**？

## 决策

- **D1 thesis**：把记忆能力包从"**检索工具 + 提示规劝**"升级为"**我们自己控循环的检索 agent
  runtime**"；深度落**三根轴**：**A agentic search（机制）/ B grounded verification（信任）/
  C version·conflict（独有角度）**，全部以 **H 的 harness** 产出**统计显著**的数字。
- **D2 平台先行（H = 公共基座）**：先建 **in-package harness + runtime**——工具注册 + **代码控循环**
  （预算 / 终止 / 委派）+ **轨迹 record/replay**（`run_hash` 确定性）+ **可验证 outcome 的场景评测
  harness**（LLM 在环、留出集、bootstrap CI，沿 ADR-0021/0026 口径）。**A/B/C 均 blocked by H**。
- **D3 边界（不变量）**：**冻结区不动**（共享 / 云 #38/#39/#34/#55）；**不碰检索默认 / 合成**
  （沿 ADR-0026 D6）——新能力走"**加只读工具 + 代码循环**"；**保 ADR-0025**（基表 = 文件 + git、
  写侧单目标、治理口径）。**不新建服务端 LLM、不动 daemon 确定性。**
- **D4 序**：**H（薄）→ A → B → C**；**D（multi-agent 深度研究）暂缓**——成本最高（≈15× token），
  且 **blocked by 一张 in-domain 多跳/冲突评测集**（见 D5）。
- **D5 in-domain 评测集是"正证"前置**：ADR-0026 D5 已定**外部语料只能反证、不能正证本 KB**。
  要用**本 KB** 证明任何增益（含 C / D），必须先建**本语料的多跳 / 冲突评测集**（分层 gold evidence）。
  该集是独立票（E），**C / D 均 blocked by 它**。
- **D6 外部语料只作机制证据**：H/A/B/C 的外部语料结论**不得当产品增益引用**（沿 D5）。

## 理由

- **复用开源基线优先**：深度机制与评估法沿用一手来源（Anthropic 多 agent 研究系统 / Building
  effective agents、Agentic RAG survey `2501.09136`），不重造；机制层复用本仓 `agentic-rag-loop`
  的三条可迁移结论（**终止 = 多条件 OR**、**gap-driven 下一跳**、**显式充分性 + 弃答**）。
- **廉价测量优先**：H 先给"**可复现的重放 + 统计**"，任何机制上线前先在其上量，避免"demo 式做深"。
- **差异点在 C**：通用 RAG 语料**没有版本轴**；本项目有 **supersede / archive / git 历史**，
  "冲突 / 时效感知检索"是**通用作品集给不出**的角度——最值得作为简历叙事支点。

Considered options：

- **E+ 分阶段 H→A→B→C（采用）**——深度完整、每轴独立可验收；代价是面宽，须按票收窄。
- **A / B / C 单轴（备选）**——见效快，但深度偏单一：A 偏工程、B 偏校验、C 最差异化但前置重。
- **D 单轴 multi-agent（暂缓）**——规模叙事亮，但成本最高且无 in-domain 集则无法证明。

## Consequences

- 新票：**H**（harness/runtime）、**A**（agentic search 工具集）、**B**（grounded verification）、
  **C**（version/conflict 检索）、**E**（in-domain 多跳/冲突评测集）；**D 记为 backlog**。
- **依赖边**：A/B/C **blocked by H**；C/D 另 **blocked by E**。
- H 落地后，**A/B/C 的验收锚点**分别为：agentic search 显著优于一次性检索（bootstrap CI）；
  引用精确/召回 + 无支撑 claim 率 + 拒答正确；冲突检出率 / 取新弃旧正确率 / 不可裁决**不自动择一**。
- 本 ADR **修订 ADR-0026 的前提**（循环不再只在宿主）——**不改其 D5/D6 的谨慎口径**（外部语料只反证）。
- 调研页 `experiments/retrieval-agent-depth/`（本 ADR 的依据，含外部一手来源清单）。
