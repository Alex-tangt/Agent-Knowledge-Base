# phase_b — 迭代检索三臂（#48 Phase B，ADR-0026 层 2 / LLM 在环）

> 对应 **ADR-0026 D1 层 2**：Phase A（#47）已证单次检索在 MultiHop-RAG 上**有头寸**
> （条目级 `recall@5=0.650`，排序头寸为主）。本目录跑**三臂对照**：B1 单发 / B2 单发+改写 /
> B3 迭代循环，把「改写」与「迭代」的贡献分开。**结论照 D5 标注：英文新闻语料上的机制证据，
> 不是本产品增益。**

## 问题

宿主 LLM「判断信息够不够 → 据已得证据改写 query → 再检索」相对单发 / 一次改写，能补多少
gold-evidence recall？增益落在**排序 miss**（金标进 @50 不进 @5）还是**覆盖 miss**（@50 也缺）？
LLM 自判的**过早停**有多严重？（研究实测 Adaptive-RAG 路由误判 31%，但口径不同。）

## 假设（预登记）

1. **B2（改写一次）**：主要改善**排序 miss**——剥离「according to X」式来源样板词、聚焦信息需求 →
   改善嵌入排序。
2. **B3（迭代）**：在**覆盖 miss / 多约束题**上补召回——不同 query 带进新 gold。
3. **B3 的 LLM 自判弱**：会出现**过早停**（① 触发但可达金标未收齐）；不记录就会被当成「看运气停」。

## 设置

### 三臂（#48 规格）

| 臂 | 做法 |
|---|---|
| **B1 单发** | 原 query 一次检索；**直接 join Phase A `artifacts/trace/base_hybrid.jsonl`**，不重跑检索 |
| **B2 单发+改写** | LLM 把 query 改写一次（RRR 式，仍只检索一次） |
| **B3 迭代循环** | 每轮 LLM 判充分性 + 产出下一跳 query（同一 JSON）；再检索；停止 = ①/②/③ OR |

### 受控参数（值落报告）

- 检索链与 Phase A 一致：`MemoryIndex`/`MemoryRetriever` + `DefaultRetrievalStrategy`（向量 + 关键词），
  **pool=50**（使 B1 的 recall@5 ≡ Phase A），每轮取 **top-5** 入累计证据，**rerank 关**。
  - **检索链在代码里显式固定、不依赖 `MEMORY_*` env**（验收澄清，2026-09-17）：
    `run_census.open_searcher` 以 **`open_store(..., hybrid=False)`** 打开（dense-only，
    因此 **不读 `MEMORY_LOCAL_HYBRID` / `MEMORY_SPARSE_BACKEND` / `MEMORY_STORE_FUSION`**），
    词法走 **`DefaultRetrievalStrategy(enable_keyword=True)`**（策略层，**不读 `MEMORY_*`**；rerank 关不含其中）。
    ⇒ **master 的「默认词法路换 BM25/DBSF」变更不影响本实验**，无需 env pin 即可复现。
- **`R_max=4`**（1 初始 + ≤3 追问）；**累计证据上限 20 篇**。
- 停止优先序：**① LLM 判定足够 / 已得答案 → ② 本轮无新 id → ③ 用完 `R_max`**；
  另有 `no_next_query`（判不足但给不出新的、非重复 query）归为**停滞族**。每条停都落盘触发条件。
- 每轮落盘：query 文本、top-50 `ids/scores`、新增 id、judge 输出、上下文长度；hop 曲线**离线算**。
- 上下文预算 `CONTEXT_MAX_CHARS=120000`：条目级证据 ≤6000 字/篇，实测 gold fact 末尾
  p50=2316 / p90=5003 / max=5990 → 不能按 3k 截断（会丢 ~40% fact），20 篇 × 6000 ≈ 120k。

### LLM

- 宿主模型 **`qwen3.7-flash`**（DashScope OpenAI 兼容，源项目 `D:/Study/SFT/kg-triplet-sft` 同款），
  `temperature=0`、**`enable_thinking=true`**、固定 `seed=42`。
- key 只从进程环境 `DASHSCOPE_API_KEY` 读（`DASHSCOPE_ENV_FILE` 可指向外部 `.env`）；
  **绝不落盘 / 不入提交 / 不回显**。
- **答案判定**：先确定性匹配（精确 / 包含 / 词元子集），失败才用**异模型** `qwen3.7-max` 裁判（防自评）。
- prompt 版本 `phase-b-v1-20260917`（`prompts.py`）；参考基线：RRR `2305.14283`、Self-Ask `2210.03350`、
  IRCoT `2212.10509`、ReAct `2210.03629`、Sufficient Context `2411.06037`、MultiHop-RAG `agentic_rag/`。

### 抽样（`sample.json`，冻结）

- 分层 **(题型 × unique 金标篇数)**，`null_query` 单独成层；固定 `seed=20260917`；
  比例分配（最大余数），**N=200**（可答 176 + null 24）。
- 每层再切 **dev / holdout = 70 / 30**；holdout 不参与任何 prompt / 规则调整（本实验 prompt 先验固定、未调）。

### 度量

- gold-evidence recall@k（条目级，k=1/3/5/10/20/50；按题型 / 篇数 / **Phase A 失效类型**切片）；
- 端到端**答案正确率**（另报参考 `qa_evaluate.py` 的宽松口径作可比锚点）;
- B3 **过早停率 / 过度检索率**（强：`r*` 存在仍多跑；弱：某轮新增金标=0）；
- **B2 改写约束保留**（规则：非来源实体 + 数字的保留率；来源名单独统计）——防止「丢约束当增益」。

## 数据

- 复用 Phase A 的独立 store / 语料（gitignored）：`../data/corpus.json` + `MultiHopRAG.json`（ODC-BY），
  `../store/base/`（609 篇条目级索引）。B1 从 `../artifacts/trace/base_hybrid.jsonl` join。
- **不重建索引、不碰生产索引 / 真实 KB / daemon**（本实验只写 `phase_b/artifacts/`）。

## 复现

```powershell
$py = "D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe"   # 主树 venv
$env:DASHSCOPE_ENV_FILE = "D:\Study\SFT\kg-triplet-sft\.env"                      # key 不入库
cd experiments/agentic-rag-census/phase_b
& $py sample.py                 # 冻结抽样（秒级，无模型）
& $py run_phase_b.py --arms b1,b2,b3 --concurrency 8   # 三臂（可断点续跑）
& $py analyze.py                # 离线报告
```

产物：`report.md`（人读）/ `artifacts/report.json`（聚合）/ `artifacts/per_query.json`（逐题 id/指标）；
原始逐题日志在 `artifacts/trace/*.jsonl`（含数据集 query 文本 → **gitignored**）。

## 结论（`report.md` / `artifacts/report.json` 为证据）

先导 N=200（可答 176 / null 24），三臂各 200 条、**0 错误**（~1085 次 LLM 调用）。

| 臂 | evidence_recall@5 | 答案正确率 |
|---|---|---|
| B1 单发 | 0.6482 | 0.7159 |
| B2 单发+改写 | 0.6245 | 0.7216 |
| **B3 迭代循环** | **0.7391** | **0.8182** |

配对 bootstrap（95% CI）：

- **改写（B2）无可靠增益**：B2−B1 `recall@5 Δ=-0.024`（CI [-0.051, +0.003]）、`full@5 Δ=-0.034`
  （CI 含 0）、答案 `Δ=+0.006`（CI 含 0）。点估计在召回上为负——**裸改写不能替代迭代**。
  约束保留：内容约束 0.944 / 来源名 0.734（改写基本保守，未造成大规模丢约束）。
- **迭代（B3）有可靠增益**：B3−B2 `recall@5 Δ=+0.115`（CI [+0.081, +0.149]）、
  答案 `Δ=+0.097`（CI [+0.046, +0.153]）；B3−B1 答案 `Δ=+0.102`（CI [+0.051, +0.159]）。**显著**。
- **增益主要落在「排序 miss」组**（金标进 @50 不进 @5，n=94）：`recall@5` 0.446→0.585、
  答案 0.713→0.787；「覆盖 miss」组（n=17）答案 0.647→0.941（组小，描述性）。
  「full@5」组 B3 维持 1.000 / 答案 0.831——**不靠牺牲已做对的题换增益**。
- **两跳拿走大部分收益**：hop 曲线 `recall@5` r=1 0.648 → r=2 **0.715** → r=3 0.730 → r=4 0.739；
  与参考实现 `max_hops=2` 一致（r≥3 边际很小）。
- **LLM 自判弱（关键失败模式）**：B3 停触发 = LLM 判足够 133 / 无新 id 29 / 预算 14；
  **早停率 35.8%**（占 LLM 主动停 47.4%）、过检（强）26/90（均多跑 1.73 轮）。
- `null_query`（24，描述性，ADR-0017 不校阈值）：弃答率 B1 0.917 / B2 0.958 / B3 0.917。

**样本量建议**：主效应（B3−B1 答案 +10.2pp）在 N=200 已显著 → **终版 N=200 足够**；
若要把可检出阈值压到 5pp（当前 MDE≈7.8pp）才需扩到 ~500。详见 `report.md`。

**ADR-0026 D5（如实标注）**：以上是**外部英文新闻语料上的机制证据**，**不是本产品增益**。
外部语料只能反证、不能正证本 KB（规模 / 多跳密度 / 语言 / 题型不可迁移）；它测**引擎**，
不测包边界。早停率是本实验的**操作化上界**，与 Adaptive-RAG「路由误判 31%」不是同一口径。

### 复跑注意（运维）

长任务被并行会话的宽口径 `python.exe` 清理误伤过两次。规避：用
`venv\Scripts\phaseb.exe`（复制自基础解释器、仍是 venv 解释器，进程名唯一）启动；
harness 逐题落盘、可断点续跑。
