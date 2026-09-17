# Agentic RAG loop 调研：skill 化的多跳 / 充分性 / 自主终止

- 日期：2026-09-16
- 方式：三路并行子代理，读**一手来源**（OSS 仓库源码 / 论文 / 官方文档），只回报、不写仓库
- 目的：判断"把 agent loop 做深"在本项目（循环跑在**宿主 agent**、只交付 **SKILL.md + MCP 工具**）里**能做实什么**

**原始子报告**（未编辑，逐份随附）：
- `01-deep-research-systems.md` —— deepresearch 系统循环架构对照（OSS 源码 + 官方文档）
- `02-agentic-rag-mechanisms.md` —— 多跳 / 充分性 / 终止的可实现机制（论文，含 arXiv 编号核对）
- `03-skill-and-tool-affordances.md` —— 循环在宿主时的能力边界（skill 规则 vs 工具信号 vs 引擎）

## 1. deep research / deepsearch 的检索循环（一手核对）

| 系统 | 循环形状 | 多跳机制 | 充分性判定 | 终止条件 |
|---|---|---|---|---|
| `langchain-ai/open_deep_research` | LangGraph 图：supervisor↔tools + 每任务 researcher↔tools ReAct + compress | lead 拆 `ConductResearch` 子任务 | 无验证器；lead 自判 | **多条件 OR**：`iterations > max` / 无 tool_calls / 调 `ResearchComplete` |
| `gpt-researcher` deep research | 递归 breadth×depth 树（每层并行子 researcher） | 每层产出 `learnings + followUpQuestions` → 下一层 query | 无（靠 depth 倒数） | `depth<=1`；某层零结果即停（#1579 防死循环） |
| `gpt-researcher` multi_agents | 8-agent 团队：Editor→Researcher→Reviewer→Revisor→Writer | editor outline 分节深研 | **有显式 Reviewer**（按 criteria 校验→Revisor 修订） | reviewer 通过 / 修订上限 |
| `huggingface/smolagents` ODR | 层级：manager CodeAgent + managed search agent | manager 委派 | 无，manager 自判 `final_answer` | `max_steps`（12/20） |
| `dzhng/deep-research` | 递归 breadth×depth | `learnings + followUpQuestions` | 无 | `depth=0` |
| `InternLM/MindSearch` | planner 写图 + searcher 并行扩图 | planner 拆**原子子问题**为图节点，逐步扩图 | **有显式**：信息足够 → 加 `response` 节点 | 添加 response 节点 |
| `stanford-oval/storm` | 模拟对话（persona 写手 ↔ 专家检索） | 写手据历史提问 → 专家拆 query | **有（隐式）**：写手说"谢谢"结束 | 对话轮上限 / 主动结束 |
| `miurla/morphic` | 单 agent step loop（`ToolLoopAgent`） | adaptive 模式给 `todoWrite` 自建任务清单 | 无 | `stopWhen: isStepCount(maxSteps)` |
| Anthropic Claude Research | orchestrator-worker + 并行 subagents | lead 拆解；subagent 识别 gap、改写 query | **有（隐式）**：lead 自判 sufficient | lead 判断 + effort 规则 |
| OpenAI Deep Research | 未公开；端到端 RL + backtracking | 模型自学 | 未公开 | 未公开；pass rate 随 max tool calls 单调升 |
| Google Gemini Deep Research | planner + task models，异步 | 模型决定并行/串行，逐步 reason → next move | **有**：模型判定 enough → synthesis | 模型自判；计划可人工修 |

**有实证/官方数字**：
- **token/工具调用量是质量主因**：Anthropic 称 BrowseComp 上 token 用量单独解释 **80%** 方差；OpenAI 的 pass-rate vs max-tool-calls 曲线单调上升。multi-agent ≈ **15×** chat tokens。
- **并行 subagent 降延迟**最多 ~90%（Anthropic）。
**只是工程惯例**（无消融）：`depth=2/breadth=4`、`max_conv_turn=3`、"`think_tool` 提升效果"、"LLM 自判充分性准"。
**未找到公开依据**：各系统对"过早停 / 过度检索"的**实测发生率**（无人给数字）。

来源：各仓库源码/README（`deep_researcher.py`、`deep_research.py`、`graph.py`、`engine.py`、`researcher.ts`）、[Anthropic multi-agent](https://www.anthropic.com/engineering/multi-agent-research-system)、[OpenAI Deep Research](https://openai.com/index/introducing-deep-research/)、[BrowseComp](https://openai.com/index/browsecomp/)、[Gemini DR](https://gemini.google/overview/deep-research/)。

## 2. 多跳 / 充分性 / 终止的可实现机制（论文）

| 机制 | 做法 | 证据强度 | 来源 |
|---|---|---|---|
| **IRCoT 交错检索** | 用"最后一句 CoT"当下一跳查询，循环；答案串出现或步数上限即停 | **强**：检索 +11~22 recall、QA +7~15 F1、事实错误 −40~50% | 2212.10509 |
| Self-Ask 子问题分解 | 显式拆子问题、先答再合 | 强 | 2210.03350 |
| ReAct | Thought→Action→Observation | 强（HotpotQA/ALFWorld/WebShop） | 2210.03629 |
| 硬预算（跳数/段数） | IRCoT：≤8 步、≤15 段 | 强（工程） | 2212.10509 |
| **充分性分类 + 弃答分支** | autorater 判 sufficient/insufficient → 不足**弃答**而非硬答 | 中–强：作答正确率 **+2~10%**；关键发现：**模型默认"不足也硬答"** | 2411.06037 |
| CRAG 检索评估器 | 微调 T5(0.77B) 给 (q,doc) 打分、双阈值三档 | 强：评估器 84.3% vs ChatGPT 64.7% | 2401.15884 |
| Self-RAG reflection tokens | `Retrieve/IsRel/IsSup/IsUse` 训练式 token | 强（有消融），但**需训练 LM，不可移植** | 2310.11511 |
| FLARE 置信度触发 | token 概率 <θ 才检索 | 中：检索占比 >50% 在 StrategyQA **反而掉分**（过度检索有害） | 2305.06983 |
| Adaptive-RAG 复杂度预路由 | A 不检索 / B 单步 / C 多步 | 强：A/B/C 时间 **0.35s / 3.08s / 27.18s**（~78×）；**C 被误判成 B 占 31%**（过早停实测） | 2403.14403 |
| 无新信息即停 | 比对新增 id 集合，≈0 就停 | **弱/仅工程**（无一手受控实验） | — |
| Search-R1 | RL 学会 `<search>` | 强（+41%/+20%）但**需 RL 训练** | 2503.09516 |

**三种有实测的失败模式**：过早停（Adaptive-RAG 31%）、过度检索（FLARE >50% 掉分；Lost-in-the-Middle）、**不足却不弃答**（2411.06037）。
**成本**：IRCoT 每步一次 LLM 调用；CRAG 每实例 +0.15s、TFLOPs 26.5→27.2；Adaptive-RAG 路由错则两头不讨好。

## 3. 能力边界（循环在宿主 agent、只发 skill + MCP 工具）

| 能力 | 靠什么 | 可否强制 | 可否确定性测 |
|---|---|---|---|
| 多步工作流 / 条件分支 / 停止条件措辞 | **skill 规则（纯提示词）** | 否（skill 是"指示"） | 否（模型行为是统计量） |
| 命中带 `owner` / `status` / `supersedes` | **工具信号（server 计算）** | **是**（必然出现） | **是** |
| 只读**确定性**判据（无证据 / 未裁决冲突 / 链完整性） | 新增只读工具 | 部分（调用仍靠规劝） | **是**（工具部分） |
| 多 query fan-out 合并（`memory_gather`） | 新增工具，**改检索合成** | 否 | 是 |
| hop 预算 / 终止宿主循环 | — | **否**（MCP 无此原语；宿主才有 `steps`/`permissions`） | 否 |
| 服务端 LLM 裁判 / `sufficient: bool` | — | 否 | 否（且破坏 daemon 不调 LLM 的确定性；阈值违反 ADR-0017） |

**官方口径**：skill 文本**从不承诺约束力**；"确定性可靠"来自**代码/工具**，不是提示词（Anthropic Skills / Writing tools for agents）；MCP 规范无"终止宿主循环"能力，stateful handle 只是普通字符串。

**官方评测范式**：eval agent 用 `while` 循环跑 LLM+tool，每题配**可验证 outcome**，收 tool-call/token/耗时，留出集防过拟合，**不规定唯一工具序列**。本仓库 `agent_loop_42.py`（沙箱真 MCP + Stub 嵌入 + **不调 LLM 判分**）正是这套。

## 4. 对本地统一向量索引语料的适用性

- ✅ **可移植**：IRCoT/Self-Ask/ReAct 循环、硬预算、答案出现/无新 id 即停、检索式充分性检查+弃答、要求子问题覆盖。
- ⚠️ **部分**：FLARE 要 token logprobs（走 API 常拿不到）；RAGAS 式评测需 ground truth（离线）。
- ❌ **不要移植**：CRAG 的 web 兜底、Self-RAG 训练式 token、Search-R1 的 RL、MindSearch 网页并行、GAIA/BrowseComp/DeepResearch Bench（均以 web 为前提）。

**本项目特有的现实**：本地语料条目级检索已 nDCG@10=**0.9658**、recall@1≈0.71；多跳增益**可能只出现在跨文档 / 多约束题**。不做测量就默认开 = N 倍延迟换不确定收益（违反「廉价测量优先 / 已定数值不重跑」）。

## 结论

1. **"deep search" 的检索循环里，真正可迁移的是三条**：① 终止写成**多条件 OR**（预算 ∪ 显式完成信号 ∪ 无新信息）；② **gap-driven 下一跳**（显式产出"已知/缺什么"再查）；③ 充分性要有**显式信号/弃答分支**（裸问"够不够"很弱）。
2. **在"循环在宿主"前提下，我们能做实的只有两件**：(a) 用 SKILL.md 把**策略写清楚**（规劝，靠场景评测**统计**量化）；(b) 用**确定性工具**提供**判据与信号**（可强制出现、可单测）。
3. **强制多跳/充分性/终止不在我们可及范围**——`steps`/`permissions` 在宿主（opencode），hooks 在 Claude Code；做了也是自欺。
4. **别做**：`sufficient: bool`、服务端 LLM 判分、服务端 hop 预算"假终止"、对"模型是否按 skill 做了循环"要求确定性 pass/fail。
5. **成本纪律**：token/工具调用是质量主因，多 agent ≈15× token；先用免费确定性杠杆（k、池、稀疏词法）+ 测量，再谈加轮次。

## 建议的决策菜单（待 owner 拍板）

- **A（浅·零引擎改动）**：SKILL.md 显式化循环规则——多跳触发条件、**停止条件**（预算 ∪ 无新 id ∪ 显式完成）、**证据不足就明说没查到**；用 #42 风格场景集做**统计**回归。代价小、可立即做。
- **B（中·加只读确定性工具）**：A + `memory_verify`（纯确定性：无证据 / 未裁决冲突 / supersede 链完整性）+ 命中加**描述性**信号（来源数、是否有未裁决冲突；**不做**充分性布尔）。把"判断"从模型直觉挪进可测代码。中等代价。
- **C（深·多 query 合并）**：B + `memory_gather`（服务端 fan-out 多 query + 去重合并）——**动检索合成**，与本轮"主线不碰检索 / 冻结区"冲突，**需 owner 明确放行**，且它只省 hop、**不能强制**多跳。
- **D（先量再建）**：先用 `retrieval_eval.py` 在 51 条集上量 `hops=1/2/3` 与子问题分解的增益（对齐 IRCoT 口径），**只有端到端提升才落 B/C**。

> 我的建议：**D → A → B**；C 留到 D 证明跨文档题确有增益、且 owner 放行检索冻结之后再谈。

## 来源
OSS：`langchain-ai/open_deep_research`、`assafelovic/gpt-researcher`（`deep_research.py` / `multi_agents`）、`huggingface/smolagents`（examples/open_deep_research）、`dzhng/deep-research`、`InternLM/MindSearch`、`stanford-oval/storm`、`miurla/morphic`。
论文：IRCoT 2212.10509 · Self-Ask 2210.03350 · ReAct 2210.03629 · MindSearch 2407.20183 · Self-RAG 2310.11511 · CRAG 2401.15884 · FLARE 2305.06983 · Adaptive-RAG 2403.14403 · Sufficient Context 2411.06037 · Agentic RAG 综述 2501.09136 · RAGAS 2309.15217 · GAIA 2311.12983 · BrowseComp 2504.12516 · DeepResearch Bench 2506.11763。
官方：Anthropic Agent Skills / multi-agent research system / writing tools for agents · MCP 规范 · opencode docs · Claude Code subagents/hooks。
