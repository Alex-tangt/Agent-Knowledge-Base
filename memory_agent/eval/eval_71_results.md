# eval_71_results — 线② 评测基座（#71）：导航探针 + MultiHop-RAG 适配 + 标准 RAG 集

> 目标：让「**agentic search 显著优于一次性检索**」这句话**可验证**（`#63` 的 L0 前置）。
> 本文件 = 命令 + 数字 + **边界** + 选型依据。外部语料**只作机制证据、不声称本库增益**
> （ADR-0026 D5/D6；ADR-0030 D7.5）。

## 0. 交付物

| 路径 | 作用 |
|---|---|
| `memory_agent/eval/standard_sets/nav_probes_71.json` | **28 条导航机制探针**（grep/read/outline/history） |
| `memory_agent/eval/standard_sets/nav_probes_71.spec.md` | 判命中口径 / 作用域 / 边界 |
| `memory_agent/eval/eval_71_nav.py` | 探针校验器（`--verify`）+ 一次性检索 paired 基线（`--baseline`） |
| `memory_agent/eval/eval_71_nav_baseline.json` | 基线逐题明细（排名/命中/CI/隔离快照） |
| `experiments/nav-probes-71/README.md` | 实验记录（问题→假设→设置→数据→结论） |
| `experiments/agentic-rag-census/phase_c/multihop_trace.py` | MultiHop-RAG → H harness 的 **trace 契约适配器** |
| `experiments/agentic-rag-census/phase_c/report.md` + `artifacts/report.json` | 适配后的 recall@k + CI + 与 #47 的一致性核对 |
| `memory_agent/eval/standard_sets/standard_rag_set_71.*` | 标准 RAG 集选型 census + 适配脚本（见 §4） |

## 1. 复跑命令

```powershell
$py   = "D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe"
$root = "D:\python_work\work2026-4\wk-71-eval"
$env:PYTHONPATH = $root

# §2 探针自证（无模型，秒级）
& $py memory_agent/eval/eval_71_nav.py --verify

# §2 paired 基线（需 BGE-M3；索引跑在 %TEMP% 副本上，生产零写入）
& $py memory_agent/eval/eval_71_nav.py --baseline `
    --out memory_agent/eval/eval_71_nav_baseline.json

# §3 MultiHop-RAG → trace 契约（无模型；复用 #47 不可变证据，不重跑检索）
& $py experiments/agentic-rag-census/phase_c/multihop_trace.py

# §4 标准 RAG 集（census 无模型；索引消融需 BGE-M3）
& $py memory_agent/eval/eval_71_standard_set.py --census
& $py memory_agent/eval/eval_71_standard_set.py --build --set scifact --limit 1500
& $py memory_agent/eval/eval_71_standard_set.py --eval   --set scifact
```

工作树里的 venv 是主树的（`venv/` gitignored）——**一律用主树绝对路径**。

> 运维备注：跑 `--baseline` 时**不要**把 stderr 合进管道（别写 `2>&1`）——模型加载 / tqdm 的 stderr
> 会被 pwsh 当错误记录、**包装进程退出码变 1**（脚本自身退出码是 0，实测 `LASTEXITCODE=0`）。
> 建议 `2>$null` 或用 `--out` 落盘后再读 JSON。

## 2. 导航探针 + 一次性检索 paired 基线（#63 的对照）

### 2.1 探针自证（`--verify`，无模型）

**28/28 通过**（四条面各 10/7/5/6）。verify 用 Python 复刻参考动作（字面 grep / 行窗读取 /
标题树解析（跳过 fenced code block）/ `git log --diff-filter=A` 与 `git log -S`），并断言：

- 期望位置**存在**（grep 的唯一性、行窗含 span、标题层级、提交 sha）；
- `baseline_entry_id` 与**真实语料条目 id 自洽**（KB 条目取 frontmatter `id`，只读条目取 `repo:<label>/<rel>`）。

探针带 `unique` / 行窗 / commit 断言 → **文档一改就报 stale**，不是"永真集"。

### 2.2 一次性检索基线（`--baseline`，生产链路）

- 链路：`MemoryIndex.search`（BGE-M3 dense + **BM25** sparse + store 原生 **DBSF**；`MEMORY_RERANK=0`；
  `MEMORY_RETRIEVAL_POOL=14`）→ 条目级命中（与 #24/#47 同粒度）。
- 语料：生产 `gen-4` 的**临时副本**（`%TEMP%/eval71-nav/index`），首次运行惰性增量追平当前语料
  （260 → **271** 条目，新增 19 条 embedding 用 204s）；**生产索引目录签名前后一致**
  （`8c330b95c09bd586`）——零写入自证。
- 排除本票自己的产物（`standard_sets/**`、`eval_71*`、`nav-probes-71/**`、`phase_c/**`）：
  否则等于"拿我自己的题面当选料"。

**19 条可寻址探针（corpus 17 + kb 2）**：

| k | 1 | 5 | 10 | 14（生产池） |
|---|---|---|---|---|
| `recall@k` | **0.526** | **0.684** | **0.737** | **0.737** |
| 95% CI（bootstrap，n=19） | [0.316, 0.737] | [0.474, 0.895] | [0.526, 0.895] | [0.526, 0.895] |

- **MRR 0.614**，CI [0.412, 0.798]。
- **5/19 在 k=14 也完全进不了榜**：`g03`（`EXCLUDE_DIR_NAMES` → AGENTS.md）、`g04`
  （`MEMORY_DEDUP_THRESHOLD` → AGENTS.md）、`g07`（`579 passed` → AGENTS.md）、`r04`
  （AGENTS.md「退役条目 = 0」段）、`o03`（CONTEXT.md `## Glossary`）。
  **三条 miss 的目标都是 AGENTS.md** —— 一条 400+ 行的巨型条目：一次性检索把它当**一个**向量，
  局部 token 被整条稀释（这正是 #63 `read` 行窗 / `grep` 的测量面）。
- 分面 `hit@5`：grep 0.500 / read 0.833 / outline 0.800；`o03` 的 top-1 是
  `repo:agent-infra/docs/agents/domain.md`（**跨仓库语义近邻**——标注 label 消歧义在这里才是必要的）。
- **结构性不可达 9 条**（history 6 + 代码文件 3）：一次性检索没有"提交"维度、索引只装 `.md`，
  按定义为 0——单独一层，不混进 paired CI。

**paired 差值（导航参考动作 − 一次性 top-k）**：

| k | Δ | 95% CI | 显著 |
|---|---|---|---|
| 5 | **+0.316** | [+0.105, +0.526] | 是 |
| 14 | **+0.263** | [+0.105, +0.474] | 是 |

> **口径警告（必读）**：导航臂 = 1.0 是**参考动作被 Python 复刻执行**的结果，**不是 LLM agent 的成功率**。
> 所以这个 Δ 度量的是「**面**（能问到什么）」的差，**不是**「#63 的工具体验」的差——后者要 #63 自己的验收。
> n=19 → CI 宽（±0.21）；本集是机制探针，**不做分布推断**。

确定性：`run_hash = 365b1880db1a6019`（逐题排名指纹；同一临时索引复跑应得同值）。

## 3. MultiHop-RAG → H harness（trace 契约）适配

**不重跑检索**（纪律「已定数值不重跑」）：数据源是 #47 Phase A 的**不可变证据**
`experiments/agentic-rag-census/artifacts/per_query_ids.json`（逐题金标 + 全量排名）。
`phase_c/multihop_trace.py` 把它表达成 `memory_agent/trace.py` 的 `Trace` JSONL：
一轮 `memory_search` 工具调用（`added_ids` = 全量 pool=50 排名）、`stop=budget`（单轮用尽预算）、
`final.evidence_ids` = 展示给模型的 top-k（两个 arm：k=5 / k=14）。

**一致性核对（证明消费的是同一份证据）**：

| run | r@1 | r@5 | r@10 | r@14 | r@20 | r@50 | 与 #47 README 一致 |
|---|---|---|---|---|---|---|---|
| base:hybrid | 0.2526 | **0.6504** | 0.7833 | 0.8470 | 0.8998 | **0.9645** | ✅ 全部 |
| base:vector | 0.2459 | 0.6324 | 0.7720 | 0.8364 | 0.8922 | 0.9645 | ✅（容差 1e-3，#47 数字四舍五入） |

harness 通路自证（`scorer.evaluate`）：`mean_evidence_recall` = 0.6504（k5）/ 0.8470（k14）,
`gold_unreached_rate` = 0.633 / 0.355；`run_hash`（2556 条 trace 内容哈希）k5 = `4d7fe00b4a766be8`。

**边界**：`answer_correct` **不适用**（one-shot 不产答案，`gold_answer=None` → scorer 回 `None`）；
trace 的 `query` 是**占位符**（数据集 query 文本未落盘，ODC-BY 归属见报告 meta）；
全量 trace 20MB 级不入库（落 `%TEMP%/eval71-phase-c/trace/`），入库的只有
`artifacts/sample_traces.jsonl`（前 25 条，证明契约形状）。**外部语料只作机制证据**（D5/D6）。

## 4. 标准 RAG 集选型 + 适配（分钟级 census → 小规模集 → 一次性基线）

（本节的 census 表、选型依据、规模/许可/是否重嵌、以及一次性基线数字，见 `standard_sets/` 与本节后续补记。）

## 5. 边界与未决问题

1. **本文件的数字都不是"本库检索质量增益"**：#2 是机制探针（自造、n=19），#3 是外部语料复用，
   #4 是外部标准集。本 KB 的 in-domain 正证归 **#65（E）**（ADR-0030 D5/D6/D7.5）。
2. **导航臂 1.0 的上限**：参考动作由 Python 复刻，不测 LLM 在环；#63 必须自己跑真 agent 才能声称"显著优于"。
3. **探针是机器本地 + 冻结行号**：3 条 `scope=kb` 依赖本机 `AGENT_KB_DIR`；行窗/标题行号随文档漂移，
   `--verify` 会如实报 stale（这是设计）。
4. **临时索引会随语料漂移**：`gen-4` 副本 + 惰性追平 → 260→271 条；`run_hash` 只在同一份临时索引内可比。
5. 未决（要不要做，属规划层）：把 §2 基线升为 CI 门（#69 已有 CI 骨架）；把 `read` 的行窗口径
   与 #63 的**真工具**对齐后重跑一次 paired（那时 Δ 才含 agent 行为）。
