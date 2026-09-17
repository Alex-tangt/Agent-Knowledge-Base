# 0026 agent loop 做深（agentic RAG）：测量协议与范围

Status: **accepted**（2026-09-17 owner 拍板）。

Relates: **#21**（检索优化叙事）、**#24**（检索评测基座）、**#42**（agent loop 已闭环）、
**ADR-0025 D19**（读 = 整张基表 / 写 = 全局知识库）、**ADR-0017**（不校无答案阈值）、
**ADR-0022**（融合 / 池默认）、`experiments/agentic-rag-loop/`（调研结论）。

## 背景

主线个人模式功能票已闭环，下一步是**把 agent loop 做深**——多跳检索 / 证据充分性 / 自主终止
（agentic RAG）。前置调研（`experiments/agentic-rag-loop/README.md`）已定三件事：

1. 循环跑在**宿主 agent**（opencode），本包只交付 **SKILL.md + MCP 工具**；强制多跳 / 充分性 / 终止
   **不在可及范围**（`steps` / `permissions` 在宿主，MCP 无此原语）。
2. 我们能做实的只有两件：**(a) skill 把策略写清楚**（规劝，统计测）、**(b) 确定性工具提供判据 / 信号**
   （可强制出现、可单测）。
3. 证据最强、最便宜、零模型改动的可移植机制 = **IRCoT 式迭代检索 + 硬预算 + 弃答分支**。

**但"先量"这一步的原始设想（在现有 51 条集上跑 `hops=1/2/3`）被一次零成本 census 否决**：
对 `memory_agent/eval/retrieval_baseline.json` 逐题核对（只读已有排名，不重跑索引），
59 个标注相关条目 **@k=5 覆盖 91.5%**、**@k=10 覆盖 96.6%**；k=10 唯一缺口是 q035 的两份同名
`triage-labels.md`（**跨仓同文副本**），k=5 的缺口也几乎都是 `README.md` / `README.zh-CN.md` 这类副本。
且评测集每条由**单一 `source_entry`** 生成，**结构上表示不了多跳**（无链式标注）。

> 结论：在现有集上跑多跳是**结构性空实验**——一次性检索已近满覆盖，且它连多跳题都造不出。
> 这违反「廉价测量优先 / 已定数值不重跑」。

## 决策

- **D1 测量分两层，先便宜后贵**：**层 1 = 确定性检索 census**（不调 LLM）；
  **层 2 = 端到端 host-agent eval**（LLM 在环、可验证 outcome、holdout、统计）。**层 1 显示头寸才做层 2**。
- **D2 层 1 语料 = 外部 MultiHop-RAG**（COLM 2024，ODC-BY）：**2556 query / 609 篇语料**，
  gold evidence **跨 2–4 篇**，4 题型（inference / comparison / temporal / null），英文。
  理由：本语料内**不存在**多跳集，自造成本高且有出题偏差（owner 判断本地数据太少）；
  该基准自带 gold evidence + 检索评测脚本，与层 1 census 同型。
  隔离机制 = **独立 store / collection**（`memory_agent/eval/retrieval_eval.py` 已有
  `--store-dir / --store-url / --manifest` 接缝），**不是**具名视图（ADR-0025 D14 未实现）。
- **D3 层 1 指标 = gold-evidence recall@k（并分题型）**：确定性、不改检索合成；不调 LLM。
- **D4 预登记信号（先定什么结果算什么答案）**：
  - 证据 recall@k ≈ 1.0 → **一次性检索已覆盖**，agentic loop 作为*检索*问题在此关闭
    （最多保留"诚实弃答"条款）；
  - 证据 recall@k 显著 < 1.0 → **有头寸**；再判是**查询表述**（分解 / 迭代可救）还是**索引 / 嵌入**
    （救不了），才排层 2。
- **D5 方向性局限（诚实记录，防外推）**：外部语料**只能反证，不能正证**本 KB——
  它规模更大（609 > 172）、多跳更密集 ⇒ 若它都没有头寸，我们更不可能有；
  但若它有头寸，**不能**证明我们的 KB 有。域 / 语言 / 题型不可迁移（`temporal` 依赖时间戳 / 元数据）。
  它测**检索引擎**，**不测包边界**（宿主循环 = 层 2）。
- **D6 A（SKILL.md 显式化循环规则）暂缓**：等 census 划定机制缺口再写，避免"先写再测"。
  A 中"证据不足就明说没查到"半条与检索无关，可随 census 结果一并落。
- **D7 范围 = 只 skill + 读侧工具，不碰检索默认 / 合成**（沿 2026-09-16「主线不碰检索」）：
  - `memory_gather`（多 query fan-out 合并）**出局**：动检索合成、只省 hop、不能强制多跳；
  - `memory_verify` **暂不建**；若建，只做**确定性**判据：supersede 链完整性、结果是否残留退役项、
    **近重复 `current` 条目提示**（复用写侧 `MEMORY_DEDUP_THRESHOLD=0.88`）、命中描述量
    （`n_hits` / `distinct_sources` / `best_score`）。**不做** `sufficient: bool`、不做语义矛盾判定、
    不调 LLM（与 daemon 确定性、ADR-0017 一致）。
- **D8 宿主 enforcer（opencode `steps` / `permissions`）= 部署决定，显式非目标**：
  skill 无 hooks，`connect.py` **不写**宿主 `steps`（避免越权 + 跨工具不一致）；README 记一句部署侧可自设。
- **D9 数据集不入库**：外部下载 + gitignored；遵守 ODC-BY 归属。

## 理由

- **先便宜后贵**：census 能"排除"不能"证明"；先用分钟级确定性测量排除/定位，再决定是否排贵运行。
- **外部基准换掉自造偏差**：自造多跳集 expense 高且题面/难度带出题人偏差；已知基准有 gold evidence 与
  外部可比性——代价是语料方向性局限，故 D5 必须显式记录、不得当产品增益引用。
- **不动检索**：检索默认归检索轨（#21）；在缺乏端到端证据前改检索合成是不确定性换不确定性。

Considered options：

- **A 自造本语料多跳集（备选）**——in-domain、可确定性，但成本高、偏差大；暂不采用，层 1 有头寸后
  若需 in-domain 佐证可再补小集。
- **B 外部 MultiHop-RAG census（采用）**——最省、可复用、有 gold evidence；代价见 D5。
- **C 直接层 2 端到端（弃）**——未先证检索头寸，贵且理由不足。
- **D 在现有 51 条集上跑 hops=1/2/3（弃）**——census 已排除的结构性空实验。

## Consequences

- **新执行单元**：外部语料 importer + eval-set 转换器（MultiHop-RAG → 独立 store / `MemoryIndex`；
  `retrieval_eval.py` 的 `--rebuild` 现在走死 `load_corpus()`，需接缝），**不改检索合成**。
- **新增实验目录** `experiments/agentic-rag-census/`（脚本 + 数据 + 一页结论）；benchmark 运行放
  **独立 worktree 实验会话**（长耗时活不进主会话）。
- 结论回写 `memory_agent/eval/` 或 `experiments/`；若 D4 判"有头寸"，另开层 2 票（LLM-in-loop，
  需可验证 outcome + holdout）。
- 在本 ADR 接受前，agentic loop 的 skill / 工具改动**不开工**；A 待 census。
- 与 ADR-0025 一致（读侧可见域、写侧单目标），本 ADR 不修改 ADR-0025。
