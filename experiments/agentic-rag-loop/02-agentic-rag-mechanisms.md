# 子报告 B：Agentic RAG 机制（多跳 / 充分性 / 终止）

> 由研究子代理产出的**原始报告**（2026-09-16，未编辑）；综合与结论见同目录 `README.md`。
> 所有编号均为本次实际抓取的 arXiv 页面；无法一手确认的已标「待核实」。

## 0. 来源核对（编号已核实）

| 论文 | arXiv / 链接 | 角色 |
|---|---|---|
| IRCoT | **2212.10509** · github.com/stonybrooknlp/ircot | 交错检索+CoT |
| Self-Ask | **2210.03350**（标题《Measuring and Narrowing the Compositionality Gap…》） | 分解子问题 |
| ReAct | **2210.03629** · react-lm.github.io | 推理+动作交错 |
| MindSearch | **2407.20183** · github.com/InternLM/MindSearch | 多 agent DAG 检索 |
| Search-R1 | **2503.09516** · github.com/PeterGriffinJin/Search-R1 | RL 学会搜索 |
| MultiHop-RAG | **2401.15391** · github.com/yixuantt/MultiHop-RAG | 多跳基准 |
| Self-RAG | **2310.11511** · selfrag.github.io | reflection tokens |
| CRAG | **2401.15884** · github.com/HuskyInSalt/CRAG | 检索评估器+纠错 |
| FLARE | **2305.06983** · github.com/jzbjyb/FLARE | 主动检索 |
| Adaptive-RAG | **2403.14403** · github.com/starsuzi/Adaptive-RAG | 复杂度路由 |
| Sufficient Context | **2411.06037**（Google） | 充分性 + 弃答 |
| Agentic RAG 综述 | **2501.09136**（v4，2026-04 更新） | 分类学 |
| RAG 综述 | **2312.10997** | Naive/Advanced/Modular |
| RAGAS | **2309.15217** · github.com/explodinggradients/ragas | 无参考评测 |
| GAIA | **2311.12983** | 通用助手基准 |
| BrowseComp | **2504.12516** · github.com/openai/simple-evals | 浏览基准 |
| DeepResearch Bench | **2506.11763** · github.com/Ayanami0730/deep_research_bench | 深研基准 |
| Mallen et al. 自适应检索 | 「When Not to Trust Language Models」(实体频率阈值；编号 2212.10511 **待核实**) | 二值是否检索 |

## 1. 机制清单表

### A. 子查询 / 跳的生成

| 机制 | 解决什么 | 怎么做（具体） | 证据强度 | 来源 |
|---|---|---|---|---|
| **交错检索 + CoT 引导查询（IRCoT）** | "下一步查什么"取决于"已推出什么" | 起始用问题检索 K 段 → 循环【用「问题+已收集段落+已生成 CoT」生成**下一句 CoT**】→【用**最后一句 CoT** 当查询再检索 K 段】；`answer is:` 或步数超限即止 | **强**（4 数据集受控；检索 +11.3~22.6 recall，QA +7.1~15.3 F1，GPT-3；Flan-T5-XXL 同向；事实错误 −40~50%） | 2212.10509 |
| **Self-Ask 子问题分解** | 复合问题、组合性缺口 | 提示模型显式追问「Follow up: …」并先答子问题再答原问题；结构化模板可直接把子问题接到搜索引擎 | **强**（compositionality gap 论证；Plug-in search 提升准确率） | 2210.03350 |
| **ReAct（Thought→Action→Observation）** | 让动作由推理轨迹驱动、可纠偏 | 交替生成 thought 与 action（search/lookup），observation 回填上下文；异常由推理步处理 | **强**（HotpotQA/Fever 抗幻觉；ALFWorld +34%、WebShop +10% 绝对成功率） | 2210.03629 |
| **动态图式规划（MindSearch）** | 一次检索覆盖不全、信息分散 | WebPlanner 把查询拆成原子子问题作**图节点**，依 WebSearcher 返回**逐步扩图**；子问题并行检索 | **中**（人评优于 ChatGPT-Web/Perplexity；3 分钟覆盖 >300 网页——工程证据，非消融） | 2407.20183 |
| **RL 学出来的查询生成（Search-R1）** | 提示工程下"不会最优地与检索器交互" | 在推理轨迹中让 LLM 生成 `<search>query</search>`，多轮检索；**retrieved token masking** 稳定训练 + 纯结果奖励（outcome reward） | **强**（同设置下 +41%(7B)/+20%(3B)），但**需 RL 训练策略模型**，非现成 agent 可移植 | 2503.09516 |
| **实体/关系追踪** | 跨文档的多跳链 | IRCoT 本质是"用上一跳得到的实体当下一跳查询"；图结构检索（Asai 2020 等）靠超链接/path 追踪 | **中**（IRCoT 强证据；图方法依赖实体链接与超链接图） | 2212.10509 / 综述 2501.09136 |

### B. 充分性信号（哪些有实证，哪些只是提示词）

| 机制 | 做法 | 证据强度 | 来源 |
|---|---|---|---|
| **训练式 reflection tokens（Self-RAG）** | 4 组 token：`Retrieve{yes,no,continue}`、`IsRel{relevant,irrelevant}`、`IsSup{fully/partially/no}`、`IsUse{5..1}`；离线用 GPT-4 打标蒸馏 critic（每类 4k–20k），再训生成器 | **强**（有消融：去掉 `IsSup` 掉分；critic 与 GPT-4 一致率 >90%）。**但需要训练 LM**——现成 LLM 只能模仿提示词版 | 2310.11511 |
| **微调检索评估器（CRAG）** | T5-large(0.77B) 逐 (query,doc) 打分，双阈值分 3 档 Correct/Incorrect/Ambiguous；阈值按数据集经验设：PopQA (0.59,−0.99)、Pub/ARC (0.5,−0.91)、Bio (0.95,−0.91) | **强**（评估器 84.3% vs ChatGPT 58.0 / CoT 62.4 / few-shot 64.7）；消融显示 Ambiguous 档关键） | 2401.15884 |
| **充分性分类 + 引导弃答（Sufficient Context）** | 用 LLM autorater 判"上下文是否足够回答问题"（sufficient/insufficient），据此做 selective generation：不足则**弃答**而非硬答 | **中–强**（跨 Gemini 1.5 Pro/GPT-4o/Claude 3.5 / Mistral/Gemma 分层分析；弃答使"作答时的正确率" +2–10%）。关键发现：大模型在上下文不足时**宁可乱答也不弃答** | 2411.06037 |
| **token 级置信度（FLARE）** | 临时生成下一句，若**任一 token 概率 < θ** 才触发检索 | **中**（θ 从 0→1 扫：检索占比 40–80% 最优；2Wiki 上直接句子作查询优于掩码/生成问题） | 2305.06983 |
| **覆盖检查（RAGAS context… / 子问题覆盖）** | 无参考：抽 context 中"能回答该问的句子"占比（context relevance）；faithfulness = 被支持陈述数 / 总陈述数 | **中**（与人工一致率 0.95/0.78/0.70；context relevance 最难，长上下文 ChatGPT 常挑不准）。**是评测信号，不是在线低延迟信号** | 2309.15217 |
| **LLM 自判"能否据此作答"（纯提示词）** | 直接问 ChatGPT"这些文档够不够" | **仅提示词**（CRAG 实测其准确率(58–64%)显著低于微调 T5；Sufficient Context 也需专门 autorater prompt） | 2401.15884 / 2411.06037 |

### C. 终止判据

| 判据 | 具体做法 | 实证结论 / 失败模式 | 强度 | 来源 |
|---|---|---|---|---|
| **预算：跳数 / 段落数** | IRCoT：最多 **8 步**、累计最多 **15 段**；每步 K∈{2,4,6,8} | 硬上限，最稳；代价是可能截断长链 | **强（工程）** | 2212.10509 |
| **答案出现** | IRCoT 生成含 `answer is:` 即停 | 简单有效，但依赖模型稳定输出该串 | **中** | 2212.10509 |
| **充分性阈值（检索概率）** | Self-RAG：`Retrieve=Yes` 归一概率 > δ 才检索，默认 **δ=0.2**（ALCE 用 0） | 可调"检索频率↔准确率"；δ 大→检索少→PopQA 掉分明显，PubHealth 不敏感 | **强** | 2310.11511 |
| **置信度阈值** | FLARE：低概率 token 触发；检索占比 <50% 时 StrategyQA 最佳，>50% **掉分**（噪声污染） | 明确"过度检索有害"的实测 | **中–强** | 2305.06983 |
| **复杂度预路由（Adaptive-RAG）** | T5-Large 分类 A（不检索）/B（单步）/C（多步），标签由"哪种策略答对"自动生成 + 数据集先验 | 时间/查询：**A 0.35s(8.6%)、B 3.08s(53.3%)、C 27.18s(38.1%)**；混淆矩阵：C→B 31%、B→C 23%、A→B 47%（**过早/过度都实测发生**） | **强** | 2403.14403 |
| **无新信息 / 收敛** | （这些论文基本没形式化）工程做法：比对本轮返回 id 集合与已见集合，无新增即停 | **弱/仅工程**：一手论文未给受控证据；属 agent scaffold 常规做法 | 弱 | — |
| **充分性 yes（LLM 判定）** | 见 B | 仅提示词版弱；有 autorater 时有效 | 中 | 2411.06037 |

**三种失败模式（有实测）：**
- **过早停**：Adaptive-RAG 分类器把 C 误判为 B（31%）→ 用单步答多跳题。
- **过度检索**：FLARE 在 StrategyQA 检索占比 >50% 反而掉分；RAGAS 引 Liu 2023 "Lost in the Middle"——上下文越长 LLM 越用不好中间信息。
- **不足却不弃答**：Sufficient Context 发现大模型倾向硬答（→ 需要显式弃答分支）。

### D. 成本–收益（有数字就给）

| 机制 | 增益 | 额外成本 |
|---|---|---|
| IRCoT | 检索 +11~22 recall、QA +7~15 F1、事实错误 −40~50% | 论文自陈：**每句 CoT 一次 LLM 调用**；额外检索 K 段/步 |
| CRAG | RAG 上 +4.4~14.9；Self-RAG 上 +6.9~36.9 | 每实例 **0.363s→0.512s**；TFLOPs/token **26.5→27.2**；评估器仅 0.77B（比 Self-RAG critic 7B 轻） |
| Self-RAG | 多任务超 ChatGPT/Llama2-chat；引用精度提升 | 需训练；推理需 beam（默认 width 2）+ 可能多检索 |
| Adaptive-RAG | 在"效率+准确"间取中；比全程多步省 | 见上：A/B/C 三档时间差 **~78×**（0.35s vs 27.18s），路由错则两头不讨好 |
| FLARE | 2Wiki EM 51.0（vs question-decomp 47.8、单次 39.4、无检索 28.2） | 多次生成+检索；仅 40–80% 句子触发 |
| Search-R1 | 同设置 +41%/+20% | 需 RL 训练 |
| Sufficient Context | "作答时正确率" +2–10% | 一次 autorater LLM 调用 |

### E. 小语料（本地统一向量索引、无外部 web）适用性

| 机制 | 是否成立 | 说明 |
|---|---|---|
| IRCoT 交错循环、Self-Ask 分解、ReAct 循环 | ✅ **成立，最可移植** | 只需 memory_search，纯提示词+预算；不依赖 web |
| 硬预算 / 答案出现即停 / 无新 id 即停 | ✅ | 与语料规模无关 |
| query rewriting / 实体追踪 | ✅ | 但本仓库检索已是条目级、nDCG@10=0.9658、top-k 召回近满 → 多跳增益主要来自**分解+改写**，而非提高召回量 |
| LLM 自判充分性 + 弃答 | ✅（但属提示词，弱） | 建议配"列出待验证 claim + 候选 id"结构化提示 |
| RAGAS 式无参考评测 | ✅（离线） | faithfulness / answer relevance 可用；"context precision/recall"需 ground truth（后续实现） |
| FLARE token 置信度 | ⚠️ **部分** | 需要 token logprobs；宿主 agent 走 API 时往往拿不到 → 只能退化为 LLM 自判 |
| **CRAG 的 Incorrect→web search 分支** | ❌ **依赖大规模 web** | 论文明确用 Google Search API 兜底；本地无 web 时应改为"返回不足/弃答" |
| Self-RAG 训练式 critic/tokens | ❌ 不可移植 | 需训练 LM；现成 agent 无从获得这些 token |
| MindSearch / Search-R1 | ❌ | 依赖搜索引擎/RL |
| GAIA / BrowseComp / DeepResearch Bench / MultiHop-RAG | ❌（作为**评测**不适用） | 均以 web 浏览/新闻语料为核心；GAIA 466 题(92% 人 vs 15% GPT-4+插件)、BrowseComp 1266 题、DeepResearch Bench 100 题/22 领域。可借鉴其**指标思想**（如 DeepResearch 的 effective citation count / citation accuracy） |

## 2. 对本项目（本地 Markdown 记忆库 / 工具=memory_search+get / 宿主 agent 驱动循环）最可实现的 5 条

1. **IRCoT 式「跳循环」+ 硬预算**（最高优先）
   - 每次 `memory_search` 后，让 agent 写一行"已知 / 缺什么"，用**缺失的实体或关系**作下一次 `memory_search` 查询；`max_hops=3~5`、总返回条目数封顶（对齐 IRCoT 的 8 步/15 段），出现答案或 id 集合无新增即停。
   - 理由：IRCoT 是这几篇里**最强、最便宜、零模型改动**的证据（+11~22 recall / +7~15 F1）；我们工具已返回 id/snippet，天然支持"用上一跳产物当下一跳查询"。

2. **结构化充分性检查（含弃答分支），而非裸问"够不够"**
   - 一小段提示：列出待答主张 + 候选条目 id → 输出 `sufficient / insufficient`，并逐条给 `supported_by(id)`。不足则**要么再跳一跳，要么明说"记忆库中没有"**。
   - 理由：Sufficient Context 证明大模型默认"不足也硬答"，显式弃答使作答正确率 +2–10%；Self-RAG `IsSup` 消融证明支撑检查有用。**诚实标注：这是提示词版，弱于微调 T5（CRAG 84.3% vs ChatGPT 64.7%）**，需在自家评测上校准。

3. **子问题分解 = 多条独立 `memory_search`，按 id 合并**
   - 复合/多约束问句先做 Self-Ask 式分解（≤3~4 个原子子问题），并行各搜一次，按条目 id 去重合并后再判充分。
   - 理由：Self-Ask 与 MindSearch 的证据；也直接命中 AGENTS 里已记录的"多约束复合句丢约束"失败点。代价是 N 次调用（IRCoT 已明说每步一次 LLM 调用是固有成本）。

4. **终止用「预算 + 无新 id」双保险，并把检索占比当可调旋钮**
   - 记录每跳返回的新增 id 比例；当连续一跳新增≈0 就停（对应 FLARE"过度检索引入噪声"：StrategyQA 检索占比 >50% 反降）。先把阈值设保守，再在自家 51 条评测集上扫 hop 数。
   - 理由：Adaptive-RAG 实测时间/查询 A/B/C 相差 ~78×，证明"该停不停"是最花钱的错误；我们纪律也要求"先 census 再重评测"。

5. **把上述机制做成离线可测 harness（先量增益，再决定上不上线）**
   - 复用 `memory_agent/eval/retrieval_eval.py` + RAGAS 式 faithfulness/answer-relevance，对 `hops=1/2/3` 出曲线，只有端到端指标提升才默认开启。
   - 理由：本地语料 top-k 召回已近满（nDCG@10=0.9658），多跳的收益**可能只出现在跨文档题**；不先量就默认开，等于承受 N 倍延迟换不确定收益——违反"廉价测量优先 / 已定数值不重跑"。

**明确不要移植**：CRAG 的 web-search 兜底、Self-RAG 的训练式 reflection tokens、Search-R1 的 RL、MindSearch 的网页并行、GAIA/BrowseComp/DeepResearch Bench 作为评测（均以 web 为前提）。

**不确定/待核实**：Mallen et al. 2023 的 arXiv 编号；Self-RAG 官方代码默认阈值（论文写 δ=0.2，代码细节未一手核对）；"无新信息即停"缺少一手受控实验（标为工程启发式）。
