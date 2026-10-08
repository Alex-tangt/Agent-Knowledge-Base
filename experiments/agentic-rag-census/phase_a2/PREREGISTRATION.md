# Phase A″ 预注册（ticket #74）— 真 agent 循环 × MultiHop-RAG，N=200

> **冻结声明**：本文件由 **Lead 在开跑前**起草并提交（测量契约 = 规划层决策）。
> 执行层（teammate）**不得修改本文件**；README / 脚本 / 结果写在同目录的**其它文件**里。
> 任何与实测不符之处，照实写进 `report.md` 的「与预注册的偏差」节，**回头改本文件 = 事后拟合**。

## 0. 这一票要判定什么（唯一问题）

> 交付的**真 agent 运行时**（`agent_loop` + 8 工具）在 MultiHop-RAG 上，**是否优于同证据额度的
> 一次性检索**？以及 —— **#63 里那个 26%（5/19）的 `fallback` 协议失配率，在 N=200 下是多少？**

写这一票的直接原因：`#63` 的残留归因（"26% 协议失配"、"残留 miss 全在 grep 面打 `AGENTS.md` 大条目"）
是 **n=19 的描述**，95% CI 分别约为 [12%, 49%] 与 CI 宽 0.158（只能检出 >0.16 的效应）——
**区分不开"机制存在"与"机制不存在"**。再读一遍同样 19 条不会增加任何信息量；
唯一能把直观判断变成数字的动作是**加 N**。本票就是那次加 N。

## 1. 冻结的资产（实测指纹，开跑前）

| 资产 | 路径 | sha256 | 大小 |
|---|---|---|---|
| 题集（200 条冻结 id，seed 20260917） | `phase_b/sample.json` | `DAED35F182524E19A77BF02E736BA55A07E0199BD5B57BA94911B0F6493CE438` | 6,742 B |
| 派生索引（609 条，重建于 2026-10-07） | `store/base/manifest.json` | `8ECCF3027CA4B55A59ED83ACF64B69CF0AADBE2C060C550D7386D8664C849066` | 578,360 B |
| 同上（向量库） | `store/base/qdrant/collection/multihop_rag/storage.sqlite` | `A40F216C3D021CEE6C3048E51C6100FA5450DEAFD6E907178BE662859C27E11C` | 10,072,064 B |
| #47 one-shot 不可变证据（pool=50 全量排名） | `artifacts/per_query_ids.json` | `9B17360BC476807BF06409B5F16E541218D8B862B8B01B525F497F33F2461089` | 2,100,221 B |

**测量基座不许清**（AGENTS.md）：`data/` 与 `store/` 一律不清；本票**只读**它们。

## 2. 被测对象 = 交付的运行时，**不改一行**

- 索引构造与 `run_census.open_searcher("base:hybrid", 50)` **逐字相同**：
  `open_store(db_path=store/base/qdrant, collection_name="multihop_rag", hybrid=False)` +
  `MemoryRetriever(strategy=DefaultRetrievalStrategy(enable_keyword=True), pool_size=50)` +
  `MemoryIndex(store=…, manifest_path=store/base/manifest.json, retriever_factory=…)`。
- 工具目录 = `MemoryNavToolRegistry(index, exclude_retired=False).list_tools()`（**8 个**：
  `memory_search` / `memory_get` + 6 导航），派发走 `AgentLoop` 的 `TOOL:` 协议。
- 循环 = `AgentLoop(llm, tools, budget=Budget(max_rounds=3, max_evidence=20), k=5)`（**全默认**）。
- LLM = `memory_agent.agent_loop.llm` 的 `openai-compat` 路径（`build_llm_client`，
  **不额外塞 `extra_body`**——运行时不动）：`qwen3.7-flash`、`temperature=0`、`seed=42`；
  key 只经**进程环境** `MEMORY_AGENT_LLM_API_KEY`（值取自宿主既有 `.env`，**不落盘 / 不日志 / 不回显**）。
- **纪律**：改循环 / 改工具 / 改提示词 / 加 retry 包装 = 换被测对象，本票作废。

## 3. 题集

- `phase_b/sample.json["all"]`（200 条，与 #48 Phase B **同一冻结子集** → 与 #48 / #47 的
  数字可对照、可配对）。
- 评测集用 `multihop.build_eval_set`（**纯函数**，不调 `ensure_prepared`，不重写 `data/articles/`）。
- **可答子集** = `kind != "no_answer"` 且 `relevant` 非空 → 主指标只在这个子集上算。
- `null_query` **只作描述性观察**，不校阈值（ADR-0017）。

## 4. 三臂（同一份排名，同一份 index）

| 臂 | 定义（模型上下文里**被展示**的条目） | 角色 |
|---|---|---|
| **A1** | 真 agent 循环，`max_rounds=3`、`max_evidence=20`、`k=5`；展示集 = `trace.final["evidence_ids"]` | 被测 |
| **C20** | 一次性 `top-20`（pool=50 排名） | **主对照（额度匹配）** |
| **C5** | 一次性 `top-5`（产品默认） | 次对照（额度不匹配，照实标注） |

**为什么主对照是 C20 而不是 C5**：A1 的展示额度上界就是 `max_evidence=20`（`Budget` 默认值）——
把 agent 跟"20 条 vs 5 条"比，量到的是**额度**不是 agent。#63 已用 `k=14` 做过同样的混淆检查
（结论：差异不是额度造成的）；本票把额度匹配**前置为主对照**，不做事后解释。
另报 C15 / C50 作曲线（C15 ≈ 纯 search 三轮的上界）。

## 5. 主指标与配对

- 主指标 = **条目级证据召回** `mean_evidence_recall`：
  每题 `|gold ∩ 被展示 id| / |gold|`，gold = `multihop.build_eval_set` 的 `relevant`（去重文章 id）。
  打分口径**直接复用** `memory_agent.eval.harness.scorer.evaluate`（与 #71 `phase_c` 同一条通路）。
- 配对检验：`memory_agent.eval.harness.stats.paired_diffs` + `bootstrap_ci`（95%），**逐题配对**。
- **主比较**：`A1 − C20`（显著与否都要报，含 CI 端点）。
- **次比较**：`A1 − C5`；描述性曲线：C15 / C50。
- **预注册的分层**（防"拿工具用没用过当解释"）：
  - `nav_used` 层 = A1 该题调用过任一导航工具的题；`search_only` 层 = 没调用过的题。
  - 两层分别报 A1 召回与其配对差——**若整体 Δ 显著，必须能指出它只来自哪一层**。
- **确定性口径**：LLM 不是逐字节确定的 → 本票是**一次观测**，不是可复现性声明；
  trace 仍盖 `run_hash`（`seal_trace`）供逐位重放同一份轨迹。

## 6. 预注册判定规则（`fallback` 协议失配）

- 口径：`fallback` 率 = **停止触发器为 `fallback` 的题数 / 全部 200 题**
  （与 #63 的 5/19 同分母口径）；另附「轮级」率（fallback 轮 / 总轮）作描述。
- 95% CI 用 `bootstrap_ci`。判定带（**开跑前写下**；阈值 10% 是判断，不是推导——
  含义："每 10 题就有 1 题把最后一轮浪费在协议失配上"）：

| CI 落点 | 判定 | 动作 |
|---|---|---|
| 下界 **> 10%** | 协议失配是**实测缺陷**且幅度已定 | 开「协议加固」票，用本票数字作 before |
| 上界 **< 10%** | #63 的 5/19 判为**小样本噪声** | **不开**加固票；#73 依此收窄 |
| 其余 | **未决** | 照实记 CI，并给出"要多少 N 才能定"的算账 |

- **禁止**：跑完再挑一个刚好跨过/不跨过现有点估计的阈值。要改阈值 = 重跑前改本文件并记录。

## 7. 描述性报告项（只报数，不设门槛）

停止触发器分布（含 `fallback` / `no_new_ids` / `budget` / `answer` / `insufficient`）、
`nav_tool_use_rate`、逐工具调用次数、每题 `memory_search` 次数、轮数分布、
每题耗时（p50/p95/max）、`memory_grep` 单次耗时（609 条目全扫，**先冒烟测**）、
各臂自身召回、null_query 行为（描述性）、错误题数（LLM/工具失败）。

**另有两条口径必须如实写进报告**（本票最重要的事实之一）：

1. `MemoryIndex.search` 给模型的 `snippet` 是**前 240 字**（`memory/index.py:334`），
   而 MultiHop-RAG 的 gold fact 在文章里的位置 p50 ≈ 2316 字 → **首轮检索结果本身看不全**。
   报告要给出「A1 靠 `memory_read`/`memory_grep` 把 gold 补进来的题数」。
2. 六工具里 `memory_history` / `memory_links` 对本语料**结构性近空**
   （文章是 gitignored、无 supersede 链）→ 报调用次数与错误形状，
   **不把它们无输出算作工具失败**（那是运行时的已知边界，不是本票发现的缺陷）。

## 8. 不做什么（边界）

- **不判答案正确率**（不调裁判 LLM）：主指标是证据召回；`answer` 随 trace 落盘，
  后续要判分**不必重跑**。报告里不得出现任何"答案准确率提升"的说法。
- **不声称本 KB 的增益**：外部语料**只作机制证据**（ADR-0026 D5/D6、ADR-0030 D7.5）。
- **不改运行时、不改检索默认、不改提示词**（见 §2）。
- **不重跑 #47/#48 的已定数值**：C20/C5 由本次在同一 store 上重算（§9），
  并与 #47 公布数字做**一致性核对**，不是"再测一遍取平均"。

## 9. Step 0（零 LLM，先做，是闸门）

在重建后的 store 上，对 200 道冻结题重算 pool=50 一次性排名，产出：

- 本次 `base:hybrid` 在**这 200 题**上的 `recall@5/@10/@15/@20/@50`；
- 与 `per_query_ids.json`（#47 不可变证据）在这 200 题上的**逐位排名一致性**
  （报告 `ranked` 列表首次分歧位置 + 逐位相同题数）。

**闸门**：若逐位不一致 → 说明重建的 store 与 #47 的语料/索引不同，
**必须**在报告里标明，且控制臂改用**本次重算**的排名（配对仍然成立，但与 #48 的可比性要降级声明）。
另报 `store` 目录签名在开跑前 / 收工后**未变**（只读证明）。

## 10. 冻结点

- 本文件的冻结 commit：**见同目录 `README.md` 记录的 `prereg_commit`**（执行层在开跑前填 SHA）。
- 开跑前先提交本票的 `README.md`（问题 → 假设 → 设置 → 数据 → 判定规则），
  **先写记录再跑**（AGENTS.md「实验留痕」）。
- 任务卡 / write scope / 验收锚点见共享任务板（ticket #74）；
  决策落点 = `docs/adr/0030`（D8 之后的实测补充由 Lead 在验收后写）。
