# Phase A″（ticket #74）— 真 agent 循环 × MultiHop-RAG，N=200

- **测量契约（权威）**：同目录 `PREREGISTRATION.md`
  - `prereg_commit = fff00c2`（预注册首次冻结）
  - `prereg_amend  = 55472c4`（R1 修订：`exclude_retired` 口径 / id 映射陷阱 / `fallback` 分母）
- 本文件是**实验记录**（问题 → 假设 → 设置 → 数据 → 判定规则），按 AGENTS.md「实验留痕」
  **开跑前**先提交；冒烟实测数字在冒烟后追加提交（见 §7）。
- 分支 `feat/74-a2-multihop`，worktree `wk-74-a2`；主树相对路径一律写
  `experiments/agentic-rag-census`（**不把宿主绝对路径写进入库文件**）。

## 1. 问题（唯一问题）

交付的**真 agent 运行时**（`memory_agent/agent_loop` + `MemoryNavToolRegistry` 的 8 个工具）
在 MultiHop-RAG 上，**是否优于同证据额度的一次性检索（C20）**？
以及 **#63 里 26%（5/19）的 `fallback` 协议失配率在 N=200 下是多少**？

动机：#63 的残留归因是 **n=19 的直观判断**（`fallback` 5/19 = 26.3%，Wilson 95% CI ≈ [12%, 49%]；
19 条配对的 Δ CI 宽 0.158 → 只能检出 >0.16 的效应），区分不开"机制存在 / 不存在"。
把直观判断变成数字的唯一动作是**加 N**；本票就是那次加 N。

## 2. 假设

- **H1（主）**：`A1`（agent 循环，展示集 = `trace.final["evidence_ids"]`）的条目级证据召回
  高于 `C20`（同额度的一次性 top-20），配对 Δ 的 95% bootstrap CI 不跨 0。
- **H1₀（零假设）**：Δ = 0（CI 跨 0）→ 在额度匹配下，agent 循环相对一次性检索**无显著机制收益**。
- **H2（次）**：`fallback` 率 ≥ 10%（协议失配是实测缺陷）vs < 10%（#63 的 5/19 是小样本噪声）。
- **分层（预注册 §5）**：若整体 Δ 显著，必须能指出它只来自 `nav_used` 层（调用过任一导航工具的题）
  还是 `search_only` 层。
- 不判答案正确率（不调裁判 LLM）；`answer` 随 trace 落盘。外部语料**只作机制证据**，
  **不声称本 KB 增益**（ADR-0026 D5/D6、ADR-0030 D7.5）。

## 3. 设置（三臂 + 冻结的运行时）

| 臂 | 展示给模型的条目 | 角色 |
|---|---|---|
| **A1** | 真 agent 循环（真 LLM 决策），`trace.final["evidence_ids"]` | 被测 |
| **C20** | 一次性 `top-20`（本次在同 store 上重算的 pool=50 排名） | **主对照（额度匹配）** |
| **C5** | 一次性 `top-5`（产品默认 `k`） | 次对照（额度不匹配，如实标注） |
| C15 / C50 | 描述性曲线（C15 ≈ 纯 search 三轮上界） | 描述 |

**被测对象 = 交付运行时，一行都不改**（预注册 §2）：

- 索引链**逐字**照 `run_census.open_searcher("base:hybrid", 50)`：
  `open_store(db_path=<census>/store/base/qdrant, collection_name="multihop_rag", hybrid=False)`
  + `MemoryRetriever(strategy=DefaultRetrievalStrategy(enable_keyword=True), pool_size=50)`
  + `MemoryIndex(store=…, manifest_path=<census>/store/base/manifest.json, retriever_factory=…)`。
  **只建一份 index/store**（Qdrant local 同进程不能并发开 client）；控制臂与 agent 臂共用它。
- 工具目录 = `MemoryNavToolRegistry(index).list_tools()`（**8** 个：
  `memory_search` / `memory_get` + 6 导航），派发走 `AgentLoop` 的 `TOOL:` 协议。
- 循环 = `AgentLoop(llm, tools, budget=Budget(max_rounds=3, max_evidence=20), k=5)`（全默认）。
- **`exclude_retired=False`**（= 交付运行时出厂默认，R1(a)）；本语料条目没有 `status`，
  语义上是 no-op，但会经 `index.search` 的 `fetch_k` 产生机械差异 → 零 LLM 实测两种取值下
  同 query 的 top-5 id 是否逐位相同，写进报告（**报告明确记下 A1 实际用的值**，
  且**不把本票结果说成"延续 #63 同一设置下的复核"**）。
- LLM = `memory_agent.agent_loop.llm` 的 `openai-compat` 路径（`build_llm_client`，
  不塞 `extra_body`）：`qwen3.7-flash` / `temperature=0` / `seed=42`；
  key 只经进程环境 `MEMORY_AGENT_LLM_API_KEY`（取自宿主既有 `.env`，**不落盘 / 不日志 / 不回显**）。
- 并发：`ThreadPoolExecutor`（默认 **4**，冒烟后按实测定，上限 8）。同一进程共享
  index/store/registry；`INDEX_LOCK` 串行化索引侧操作，LLM 调用并行。
- 宿主 `NO_PROXY` 含 IPv6 条目会让 httpx 建 client 失败 → 进程内 `sanitize_proxy_env()`
  （照 `nav_63_agentic.py`），只动进程环境，不落盘。

## 4. 数据

- 题集：`phase_b/sample.json["all"]`（**200 条冻结 id**，seed 20260917；176 可答 + 24 `null_query`），
  与 #48 Phase B 同一子集 → 与 #47/#48 可配对。
- 评测集：`multihop.build_eval_set(corpus, queries)`（**纯函数**；**绝不调用**
  `multihop.ensure_prepared()`，它会把 `data/articles/` 重写到 worktree）。
  语料/索引是 gitignored，**只在主树** → runner 用 `--census-dir` 指向主树
  （默认由 `git worktree list --porcelain` 第一项解析，抄 `run_census._main_worktree`）。
- `null_query` 只作描述性观察，不校阈值（ADR-0017）；主指标只在可答子集（176）上算。
- 主树 `data/`、`store/` **只读**：开跑前 / 收工后各取一次 `_dir_signature(store/base)`
  并证明未变。
- 冻结指纹（预注册 §1，sha256）以预注册为准；本次 Step 0 会实测 store 目录签名。

## 5. 判定规则（预注册 §5/§6/§7）

- **主指标** = `mean_evidence_recall`（每题 `|gold ∩ 被展示 id| / |gold|`），
  打分口径**直接复用** `memory_agent.eval.harness.scorer.evaluate`。
- **配对检验**：`stats.paired_diffs` + `bootstrap_ci`（95%，逐题配对）。
  主比较 `A1 − C20`；次比较 `A1 − C5`；曲线 C15/C50；分层 `nav_used` / `search_only`。
- **`fallback` 判定带（开跑前写下，10% 是判断不是推导）**：
  - CI 下界 **> 10%** → 协议失配是**实测缺陷**（开「协议加固」票，本票数字作 before）；
  - CI 上界 **< 10%** → #63 的 5/19 判为**小样本噪声**（**不开**加固票）；
  - 其余 → **未决**：照实记 CI，并给出"要多少 N 才能定"的算账。
  - 分母（R1(c)）：报 `fallback/completed`（**判定带用这个**）与 `fallback/200`（保守上界）。
- **完成题数 < 190 → 声明「证据不足」**，不得四舍五入；判定带只作参考。
- 描述性项齐（停止分布 / nav 使用率 / 逐工具调用 / 每题 search 次数 / 轮数分布 /
  耗时 p50-p95-max / `memory_grep` 单次耗时 / 错误题数）。
- **两条已知事实必须如实写进报告 §7**：
  1. `MemoryIndex.search` 给模型的 `snippet` 只有前 **240** 字（`memory/index.py:334`），
     gold fact 在文章里的位置 p50 ≈ 2316 字 → 首轮检索结果天生看不全，agent 得靠
     `memory_read` / `memory_grep` 补。报告给出「A1 靠导航工具把 gold 补进来的题数」。
  2. `memory_history` / `memory_links` 对本语料**结构性近空**（文章 gitignored、无 supersede 链）
     → 报调用次数与返回形状，**不算工具失败**。

## 6. 产物与只读纪律

| 路径 | 内容 | 入库 |
|---|---|---|
| `artifacts/control_rankings.json` | Step 0 重算的 200 题 pool=50 排名 + recall 曲线 + 与 #47 一致性 | ✅（仅 id） |
| `artifacts/trace/a1_traces.jsonl` | A1 逐题原始 trace（断点续跑源；含 query 文本 / answer） | ❌（.gitignore:108） |
| `artifacts/trace/smoke*.jsonl` | 冒烟原始输出 | ❌ |
| `report.json` | 聚合 + id 级记录（**不含数据集 query 文本**） | ✅ |
| `report.md` | 结论报告（含「与预注册的偏差」节） | ✅ |

- 主树 `data/`、`store/` 只读；worktree 内只写 `phase_a2/**`；
  `git add` 只显式指名自己的路径（禁 `git add -A` / `.` / `commit -a`）。
- `PREREGISTRATION.md` 属 Lead 写域，本票只读（R1 修订由 Lead 提交，本 worktree 同步其内容）。

## 7. 冒烟（3 题，零 LLM 之外的实测）

> 开跑前实测：工具目录是否真进 prompt / `TOOL:` 派发是否真执行 /
> 每题耗时 / `memory_grep` 单次耗时（609 条目全扫）。数字见 §8「实测记录」。

## 8. 实测记录（追加）

### 8.1 冒烟（3 题，真 LLM；2026-10-08）— 明细 `smoke/smoke_summary.json`

| 检查 | 实测 |
|---|---|
| 工具目录 | `MemoryNavToolRegistry(index)` = **8** 个，与预期集合一致；`describe_tools` 764 字符，**8 个名字全在 prompt 里**（`prompt_has_all_tools=True`） |
| `TOOL:` 派发 | `ScriptedLLM` 写死 `TOOL: memory_grep {...}` → 真执行：`grep` 命中 1 条并**进 `evidence_ids`**（`multihop:0000`），stop=answer |
| `memory_grep` 单次耗时（609 条目） | **全扫（0 命中）0.3644s**；`limit=50` 早退 0.0030s；`"2023"` 0.0069s |
| `memory_history` / `memory_links` 形状 | history：`count=0`（无 git 历史）、无 error；links：`chain` 长度 1、无 error → 结构性近空（§5 第 2 条），**不算工具失败** |
| 宿主 `NO_PROXY` 污染 | 进程内剥掉 IPv6 条目（`[::1]` 等）后才建出 httpx client（照 `nav_63_agentic.sanitize_proxy_env`） |

3 题逐题（同一 `Budget(3,20)` / `k=5`）：

| id | stop | rounds | 工具 | 耗时 | 展示集 gold 召回 |
|---|---|---|---|---|---|
| mhr1926 | `fallback` | 1 | search×1 | 10.6s | 0.5 |
| mhr0443 | `budget` | 3 | search×3 | 56.1s | 0.5 |
| mhr2012 | `budget` | 3 | search×1 + **read×3** | 61.0s | 0.5 |

- 冒烟即复现 #63 的 `fallback` 形态（1/3：模型输出不可解析 → 兜底）。
- 观察到一次**模型侧**参数名错误：`memory_read` 用 `id` 而非 `entry_id`（3 次里 2 次）→ 运行时如实返回 `{"error": "unknown_entry"}`（运行时行为正确；这是模型 ACI 误用，描述性记录）。
- 注：`prompt_has_tool_protocol` 只检查了 **user** 消息（`TOOL:` 协议写在 **system** 消息里），故该字段为 False 是口径假阴性；`prompt_has_all_tools=True` 已证明工具目录进了 prompt。

**并发决定**：**4**（每题串行 ≈42s，其中 LLM 占 ≈95% → 200 题 4 路并行 ≈35 min；上限 8 留余量，避免 DashScope 限流）。

### 8.2 Step 0（零 LLM）— 2026-10-08

明细 `artifacts/control_rankings.json`；结论 `report.md` §1。

- 本次重算 recall@k（176 可答）：@1 0.2495 / **@5 0.6482** / @10 0.7812 / @15 0.8537 / @20 0.9029 / **@50 0.9616**。
- **与 #47 逐位一致性：200/200 全同**（top-5 / top-20 也各 200/200），**首次分歧 = 无** → 闸门通过，
  **控制臂用本次重算值**（与 A1 同 store），**与 #48 可比性不降级**；#47 存档逐题 recall 在同 176 行上
  Δ = 0.000000（#47 该文件无 k=15 字段）。
- `exclude_retired` True/False 同 query top-5 **200/200 逐位相同**（R1(a)：本语料无退役条目，纯机械 no-op）；
  A1 实际用 **False**。
- store 目录签名 before = after = `626e174119757612` → **未变**（只读证明）；金标缺 0。

### 8.3 A1（N=200，2026-10-08）— 结论

明细 `report.md` / `report.json`；原始 trace 在 `artifacts/trace/a1_traces.jsonl`（gitignored，不入库）。

- **完成 200 / 最终错误 0**（首轮 3 题失败：2 × LLM 超时 + 1 × 运行时 TypeError，断点续跑重试后全部成功）；
  并发 4；总墙钟 **3747.8s**（2 趟）。
- **主比较 `A1 − C20` = −0.2098**，95% CI **[−0.2505, −0.1690]** → **显著为负**（A1 0.6932 vs C20 0.9029）。
  次比较 `A1 − C5` = **+0.0450** [+0.0270, +0.0653]（显著为正）；`A1 − C15` = −0.1605（显著为负）。
- **额度口径的机制事实**：循环每轮 `k=5` × 最多 3 轮 → A1 **实际展示上界 = 15**（实测 mean 6.4 / max 15），
  `max_evidence=20` 到不了 → C20 只是**名义**额度匹配，**C15 才是实际额度匹配**；两者 A1 都显著更低。
- 分层：`nav_used`（n=51）Δ −0.1454、`search_only`（n=125）Δ −0.2360，两层都显著为负。
- `fallback` = **26/200 = 13.0%**，95% bootstrap CI [8.5%, 18.0%]（Wilson [9.0%, 18.4%]）→
  按 §6 10% 判定带 = **未决**；若真实率维持 13%，Wilson 下界越过 10% 需要 **N≈390**。
- 运行时观察（**本票不修**，报 Lead）：`memory_get(entry_id=[...])` 触发
  `TypeError: unhashable type: 'list'` 并中止整题（`_dispatch` 只捕 `KeyError`）。

### 8.4 与预注册的偏差

见 `report.md` §7：无预注册偏差；另记一条**实现注记**（Step 0 首轮参照曲线分母误含 24 条 null_query，
已修正并重跑，入库为修正版）。
