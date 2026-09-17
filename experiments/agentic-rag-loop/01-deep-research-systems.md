# 子报告 A：deep research / deepsearch 的检索循环架构

> 由研究子代理产出的**原始报告**（2026-09-16，未编辑）；综合与结论见同目录 `README.md`。
> 方法：直接读各仓库 README + 核心 loop 源码，以及一手官方文档。

## 1. 机制对照表

| 系统 | 循环形状 | 多跳机制 | 充分性判定 | 终止条件 | 预算/参数 |
|---|---|---|---|---|---|
| **open_deep_research** (LangChain) | LangGraph 图/状态机：`supervisor ↔ supervisor_tools` 外层 + 每任务 `researcher ↔ researcher_tools` 内层 ReAct + `compress_research`；lead + N 并行 researcher | lead 把 research brief 拆成 `ConductResearch` 子任务；researcher 用 `think_tool`+search 迭代。无显式 gap 分析，靠 LLM | **无显式验证器**；lead 自行决定调 `ResearchComplete` | `research_iterations > max_researcher_iterations` / 无 `tool_calls` / 调 `ResearchComplete`；内层 `tool_call_iterations >= max_react_tool_calls` / 无 tool call；token 超限异常也直接终止 | `max_concurrent_research_units=5`、`max_researcher_iterations=6`、`max_react_tool_calls=10`、`max_content_length=50000`（[code](https://github.com/langchain-ai/open_deep_research/blob/main/src/open_deep_research/deep_researcher.py), [config](https://github.com/langchain-ai/open_deep_research/blob/main/src/open_deep_research/configuration.py)） |
| **gpt-researcher** (Deep Research skill) | 递归树：单 agent 递归 + 每层并行子 researcher（`process_query` 内起新 `GPTResearcher`） | 每层每题产出 `learnings` + `followUpQuestions` + `researchGoal`，拼成下一层 query；`new_breadth=max(2,breadth//2)`、`new_depth=depth-1` | **无显式**；靠 depth 倒数 | `depth<=1`；或某层零成功结果（#1579 防死循环） | `breadth=4`、`depth=2`、`concurrency=2`、`MAX_CONTEXT_WORDS=25000`（[code](https://github.com/assafelovic/gpt-researcher/blob/main/gpt_researcher/skills/deep_research.py)） |
| **gpt-researcher multi_agents** | 8-agent 团队（Chief Editor 编排）：Editor→并行(Researcher→Reviewer→Revisor)→Writer→Publisher | Editor 生成 outline，每节 Researcher 独立深研 | **有显式 Reviewer**：按 criteria 校验草稿，Revisor 按反馈修订直到"满意" | reviewer 通过 / `max_plan_revisions` 上限 | `max_sections`、`max_plan_revisions`（[README](https://github.com/assafelovic/gpt-researcher/blob/main/multi_agents/README.md)） |
| **smolagents open deep research** | 层级：manager `CodeAgent` + managed `ToolCallingAgent`(search_agent) | manager 委派给 search agent；search agent 内 search→visit→page up/down | 无显式，manager 自判 `final_answer` | `max_steps`（manager 12 / search 20）；`planning_interval=4` | `max_steps`、`text_limit=100000`；GAIA 为靶子（[run.py](https://github.com/huggingface/smolagents/blob/main/examples/open_deep_research/run.py)） |
| **dzhng/deep-research** | 递归函数（breadth×depth 树） | `learnings` + `followUpQuestions` 拼 next query；`newBreadth=ceil(breadth/2)`、`newDepth=depth-1` | **无** | 递归到 `depth=0` | `depth` 默认 2 (1-5)、`breadth` 默认 4 (3-10)、`ConcurrencyLimit=2`、每 SERP `limit=5`、60s timeout（[code](https://github.com/dzhng/deep-research/blob/main/src/deep-research.ts)） |
| **MindSearch** (InternLM) | planner(LLM 写 Python 操作 `WebSearchGraph`) + searcher 节点**并行/异步**执行 | planner 拆单问题子节点建图 + `add_edge`，可在已有节点上继续提问 | **有**：planner 自判信息满足 → `add_response_node`（"current information satisfies the question's requirements"） | 添加 `response` 节点 | 线程池/事件循环 32；searcher `topk=2`（示例）；无显式迭代上限，靠 LLM（[graph.py](https://github.com/InternLM/MindSearch/blob/main/mindsearch/agent/graph.py), [prompt](https://github.com/InternLM/MindSearch/blob/main/mindsearch/agent/mindsearch_prompt.py)） |
| **STORM** (Stanford) | 两阶段（预写 research + 写作）；预写 = 多 persona 并行模拟"维基写手↔专家"对话 | 写手据对话历史提问 → 专家拆 query→检索→回答；perspective-guided | **有（隐式）**：写手自判"没问题了" → 输出 `"Thank you so much for your help!"` 结束 | `max_conv_turn=3` 或写手主动结束 | `max_perspective=3`、`max_search_queries_per_turn=3`、`search_top_k=3`、`retrieve_top_k=3`、`max_thread_num=10`（[engine.py](https://github.com/stanford-oval/storm/blob/main/knowledge_storm/storm_wiki/engine.py), [curation.py](https://github.com/stanford-oval/storm/blob/main/knowledge_storm/storm_wiki/modules/knowledge_curation.py)） |
| **morphic** | 单 agent step loop（`ToolLoopAgent`） | adaptive 模式给 todo 工具（`todoWrite`）让模型自建任务清单，据 todo 迭代 search/fetch | 无显式，模型自判 | `stopWhen: isStepCount(maxSteps)` | quick=20 steps（search+fetch）；adaptive=50 steps（search+fetch+todoWrite）（[researcher.ts](https://github.com/miurla/morphic/blob/main/lib/agents/researcher.ts)） |
| **nickscamara/open-deep-research** | README **未描述循环**；仅 Firecrawl search+extract + reasoning model + 结构化输出 | 未找到公开依据 | 未找到公开依据 | 未找到公开依据 | —（[README](https://github.com/nickscamara/open-deep-research/blob/main/README.md)） |
| **Anthropic Claude Research** | orchestrator-worker：lead（extended thinking 规划 + Memory 存 plan）→ 并行 subagents | lead 拆解委派；subagent 内 interleaved thinking 评估结果、识别 gap、改写 query | **有（隐式）**：lead 自判 "sufficient information" 后退出 | lead 判断；有 effort 规则 | 简单 1 agent/3-10 calls；比较 2-4 subagents/10-15 calls；复杂 >10 subagents；multi-agent ~15× chat tokens（[blog](https://www.anthropic.com/engineering/multi-agent-research-system)） |
| **OpenAI Deep Research** | 未公开具体图；端到端 RL 训练，multi-step trajectory + backtracking | 模型自学 | 未公开 | 未公开；但实测 pass rate 随 max tool calls 单调上升 | test-time compute scaling；HLE 26.6%、GAIA 67.36% (pass@1)、BrowseComp 51.5%（[launch](https://openai.com/index/introducing-deep-research/), [BrowseComp](https://openai.com/index/browsecomp/)） |
| **Google Gemini Deep Research** | planner + task models，异步共享状态；planning→searching→reasoning→synthesis | 模型决定子任务并行/串行；每步 reason over gathered info 决定 next move | **有**："Once the model determines enough information has been gathered" → synthesis，含 multiple passes of self-critique | 模型自判；计划可人工修正（human-in-loop） | 1M token context + RAG；异步 task manager（[官方页](https://gemini.google/overview/deep-research/)） |

## 2. 逐系统要点

**open_deep_research（LangChain）** — 唯一把"循环上限 + LLM 主动完成信号"写成显式代码的：`exceeded_allowed_iterations = research_iterations > configurable.max_researcher_iterations`，与 `ResearchComplete` tool call、`no_tool_calls` 三者任一即退出。它**没有**证据充分性验证器——`compress_research` 只是把 findings 压成摘要。来源：`deep_researcher.py`。

**gpt-researcher** — 深度/广度是**递归参数**：`new_breadth = max(2, breadth // 2)`、`new_depth = depth - 1`；子问题来自 `followUpQuestions`（gap-driven，由 LLM 生成）。有一条显式防御：某层零成功结果就停（issue #1579），否则会从空 learnings 无限生成 follow-up。来源：`deep_research.py`。

**MindSearch** — 唯一"planner 显式拆图 + 显式充分性信号"的组合：planner 必须（prompt 强制）最后一步只加 `response` 节点，以此表达"信息已足够"。子问题必须是单问题（"A,B,C 有什么区别 → 分别查询"）。来源：`graph.py` + `mindsearch_prompt.py`。

**STORM** — 用**模拟对话**驱动深度：写手（带 persona）提问、专家检索回答，写手在校验历史后决定继续或说"谢谢"结束。这是"自然语言停止信号"而非阈值。来源：`knowledge_curation.py` 的 `AskQuestion` signature + `ConvSimulator.forward`。

**Anthropic** — 关键实证：multi-agent（Opus lead + Sonnet subagent）比单 agent Opus 在内部 eval 上高 **90.2%**；BrowseComp 上 **token 用量单独解释 80% 方差**（tool calls + 模型选择解释其余 15%）。明确写"多 agent 主要就是因为能花更多 token"。也承认代价：multi-agent ~**15×** chat tokens。来源：Anthropic engineering blog。

**OpenAI Deep Research** — 无公开 loop 细节，但给了最强的"token/工具调用即质量"证据："Pass Rate vs Max Tool Calls"曲线单调上升，"The more the model browses and thinks about what its browsing, the better it does"。BrowseComp 上 51.5% vs o1 9.9%。来源：launch page + BrowseComp page。

**Gemini Deep Research** — 明确说**充分性由模型判断**："Once the model determines enough information has been gathered, it synthesizes the findings"。且规划阶段对人类可见可改（human-in-loop），长任务靠异步 task manager 断点续跑。来源：官方页。

## 3. 有实证支撑的 vs 只是工程惯例

**有实证/官方数字支撑：**
- **token 用量是质量的主因**：Anthropic（BrowseComp 80% 方差）、OpenAI（pass rate 随 tool calls 上升）。→ 多跳/多 agent 的收益本质是"花更多 token"。
- **multi-agent 对 breadth-first 查询收益最大**：Anthropic 单点 +90.2%，但明确说"多数 coding 任务不适合"。
- **并行化大幅降延迟**：Anthropic 称并行 subagent + 并行 tool call 使复杂查询耗时降最多 90%。
- **benchmark 分数**：ODR RACE 0.4309（默认）→ 0.4943（GPT-5）；OpenAI GAIA 67.36、HLE 26.6、BrowseComp 51.5。
- **上下文/长度是硬约束**：gpt-researcher 截 `25000` words；Gemini 用 1M context + RAG；ODR 用 50000 字符截网页。
- **rerank/压缩是必要环节**：ODR 专门有 `compress_research`（否则 findings 溢出 token 限制）。

**只是工程惯例（无实证背书，属于合理但未验证的默认）：**
- "depth=2、breadth=4"（dzhng）/ "breadth=4、depth=2"（gpt-researcher）——只是默认值，无数值消融。
- STORM 的 `max_conv_turn=3`、MindSearch 的无迭代上限——拍脑袋/交给 LLM。
- "reflection/think_tool" 能提升效果——ODR 提供 `think_tool`，但仓库未给 A/B 数据。
- 充分性"由 LLM 自判"——Anthropic/Gemini/MindSearch 都用，但没有公开的"自判准确率"或"过早停/过度检索"量化。**没有找到**任何系统公开讨论"过早停"或"过度检索"的实测率。

**未找到公开依据：** nickscamara/open-deep-research 的循环机制（README 只有技术栈）；OpenAI Deep Research 的终止判据细节（可能只在 system card 内，未在公开页给出）。

## 4. 对本项目可能最有用的 5 条

1. **把终止写成"多条件 OR"而非单阈值**（ODR）：`迭代上限 OR LLM 显式完成信号 OR 无工具调用`。单靠迭代数会既慢又贵；单靠 LLM 自判会跑飞。ODR 的 `ResearchComplete` + `max_*_iterations` 组合最可直接移植到 `memory_search` 的 agent loop。

2. **gap-driven 的"下一跳查询"生成**（gpt-researcher / dzhng）：每轮从检索结果里显式抽取 `learnings + followUpQuestions`，再拼成下一轮 query，而不是让模型在自由 ReAct 里自己记。这是可测的、可缓存的中间产物。

3. **把"充分性"做成显式信号，而不是隐式**（MindSearch 的 `response` 节点 / STORM 的结束语）：你们已经有 `exclude_retired` 这类确定性过滤，可以再加一个"证据覆盖了哪些子问题"的显式清单，避免纯 LLM 自判。

4. **上下文/结果必须有压缩与截断预算**（ODR `compress_research`、gpt-researcher 25k words）：多跳必然累积；不做压缩会先撞 token 限制再谈质量。你们 `memory_search` 的 `snippet` + `memory_get` 已经是天然的"引用而非复制"形状，可借鉴 Anthropic 的 "subagent output to filesystem"。

5. **便宜的杠杆优先于加 agent/token**（Anthropic 的 token 结论 + 本项目纪律）：既然 80% 方差来自 token，而你们已有 rerank/融合/池大小等**确定性杠杆**且 rerank 默认关——先把免费杠杆（`k`、候选池、稀疏词法）榨干，再加"多轮 agent"。多 agent ≈15× token 的成本必须用 breadth-first 的真实需求来 justify。

**Benchmark 参考**：`DeepResearch Bench`（RACE 报告质量 + FACT 引用可信度，100 个 PhD 级任务）、`BrowseComp`（难找短答案，1,266 题）、`GAIA`（三级难度，SOTA 67.36）。来源：[DeepResearch Bench](https://github.com/Ayanami0730/deep_research_bench)、[BrowseComp](https://openai.com/index/browsecomp/)、[GAIA paper](https://openreview.net/forum?id=fibxvahvs3)。
