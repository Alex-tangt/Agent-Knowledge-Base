# agentic-rag-census — MultiHop-RAG one-shot 检索 census（#47 Phase A）

> 对应 **ADR-0026 D1–D5** 的**层 1（确定性检索 census，不调 LLM）**。决策票 **#47**；
> 层 2（LLM 在环）是 **#48**，**blocked by 本结论**。

## 问题

项目的检索链（BGE-M3 + 向量/关键词混合，`ragcore/strategies/default.py`）在**多跳证据**上
单次检索（one-shot）能收回多少？若单次已几乎全中，"迭代检索 / agent loop"作为*检索*问题
就没有头寸（headroom）；若有，再判是**排序问题**（金标进了候选池但没排进前排）还是
**覆盖问题**（金标根本进不了召回），并区分**查询表述**（分解 / 迭代可救）与
**索引 / 嵌入**（救不了）。

## 假设（ADR-0026 D4，**预登记**，不事后改判据）

- `recall@k ≈ 1.0` → 循环无检索头寸，agentic loop 作为检索问题关闭。
- 显著 `< 1.0` → 有头寸；再区分查询表述 vs 索引/嵌入，才排层 2。

## 设置

- **语料 = 外部 MultiHop-RAG**（HF `yixuantt/MultiHopRAG`；ODC-BY；609 篇英文新闻 /
  2556 query，金标 evidence 跨 2–4 篇）。**不建自造多跳集**（ADR-0026 D2）。
- **被测链路 = 生产记忆检索链**：`MemoryIndex.search` → `MemoryRetriever`
  → `DefaultRetrievalStrategy`（向量 + 关键词加法增强；**不叠加 store 原生 hybrid**，
  本地平面生产口径，ADR-0019 D4）。**rerank 关**（#47 明示先不开）。
- **隔离**：独立 store（`store/<variant>/qdrant`）+ 独立 manifest；数据、索引、trace
  全在实验目录（gitignored）。**绝不**指向生产 `memory_agent/vector_db` / 真实 KB / 真实 daemon
  （`report.md` 末尾有前后快照证明）。
- **单位 = 条目级**：一篇语料文章 = 一个条目（`multihop:<i:04d>` ← `corpus.json[i]`），
  金标 = evidence `url` 对齐到的文章。`recall@k = |top-k ∩ 金标文章| / |金标文章|`，
  金标按 **unique 文章** 计（168 条 query 的 `evidence_list` 有重复文章）。
- `pool_size=50`（**仅为让 recall@50 有意义**；生产默认 `MEMORY_RETRIEVAL_POOL=14`，
  同一排名里另报 **recall@14**）。k = 1 / 5 / 10 / 20 / 50。
- **元数据进送嵌正文**：source / author / published_at 写进条目正文（temporal / comparison
  题依赖它），对齐参考实现 `MetadataMode.LLM` 的 title/source/published_at 口径。

## 数据（`artifacts/inspection.json`）

- 语料 **609** 篇，url / title 均唯一；**6084** 条 evidence 的 url **100%** 落在语料内
  （无需剔除缺失金标），且**全部** `fact` 都是对应文章正文的子串。
- query **2556**：inference 816 / comparison 856 / temporal 583 / **null 301（11.8%）**。
- 每条 query 的**金标篇数（unique）**：2 → 1169，3 → 774，4 → 312（null → 301）。
- **送嵌截断**：正文 p50 **7836** 字（> `MAX_ENTRY_CHARS=6000`），477/609 篇超窗；
  **1935/6084（31.8%）** 条 evidence `fact` 落在 6000 字送嵌窗口**之外**。

## 复现

```powershell
# 仓库根；主树 venv 绝对路径（worktree 里没有 venv）
$py = "D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe"
cd experiments/agentic-rag-census
& $py inspect_dataset.py                         # 秒级：检验报告（无模型）
& $py build_index.py --variant base --force      # 灌 609 篇（BGE-M3 CPU，实测 ~47min）
& $py run_census.py --runs base:hybrid,base:vector   # 实测 ~48min（hybrid 1.08s/题）
```

数据集（gitignored）：`data/corpus.json` + `data/MultiHopRAG.json`，从 HF
`yixuantt/MultiHopRAG` 取（见 `multihop.py`）。

可选敏感性变体（**本票未跑**，见"局限"）：`build_index.py --variant full --force`
（整篇送嵌）→ `run_census.py --runs full:hybrid`。它把 `base` 的 6000 字截断去掉，
用于**因果**确认"截断表征"的贡献。

## 复用开源基线（纪律：先调研 → 复用基线）

- 代码/评测：`github.com/yixuantt/MultiHop-RAG`（COLM 2024），
  commit `c1c1287aa60a94acf9c4d20c891c9cd611a0f6e8`（2026-09-16，含
  `SentenceSplitter(chunk_overlap=25)` 修复）。参考检索脚本
  `simple_retrieval.py` / `hybrid_retriever.py` / `bm25_retrieval.py` / `rrf_fusion.py` /
  `rerank_result.py`；**评测口径** `retrieval_evaluate.py`。
- 它的指标（**chunk 级**，256-token 切块）：`Hits@K`（金标 `fact` 子串命中任一 top-K chunk）、
  `MAP@K`、`MRR@K`；**跳过 null_query**。
- **我们的口径**：同样的"金标证据命中"概念，单位改为**条目级（整篇文章）**，输出
  `recall@k`（#47 D3）。**不与论文数字同轴**——粒度 / chunking / 模型都不同，
  论文数字只作**外部量级锚点**。
- **论文锚点**（Table 5，chunk 级）：最优无 rerank `Hits@10=0.6718` / `Hits@4=0.5221`
  （bge-large-en-v1.5）；最优加 `bge-reranker-large` `Hits@10=0.7467` / `Hits@4=0.6625`
  （voyage-02）。论文结论：现有 RAG 方法在多跳证据检索上表现不佳（§4.3 点名 hybrid
  与 agent 是未探索方向）。我们条目级的 `recall@5=0.650` / `recall@10=0.783` 与之量级一致。

## 结论（`report.md` 为证据）

**D4 判定：有头寸（显著 `< 1.0`）。** 生产链（`base:hybrid`，rerank 关，pool=50）：

| k | 1 | 5 | 10 | 14（生产池） | 20 | 50 |
|---|---|---|---|---|---|---|
| recall@k | 0.2526 | **0.6504** | 0.7833 | 0.8470 | 0.8998 | **0.9645** |
| 完整覆盖（该题金标全中） | — | — | — | — | — | 0.9175 |

- **排序头寸占主导**：候选池 @50 已覆盖 96.45% 的金标，但 top-5 只有 65.0%
  ——**排序余量 @5 = 0.314**（1317/2255 题有金标"进了池子却不在前 5"）。
- **覆盖头寸小但不为零**：204 个 gold 槽在 **top-50 都进不来**（186/2255 题有缺失）；
  `recall@50` 距 1.0 差 0.0355。
- **随跳数单调变难**（unique 金标 2/3/4 篇）：`recall@5` = 0.747 / 0.588 / **0.443**；
  `recall@1` = 0.327 / 0.187 / **0.137**——典型的"多跳"信号。
- **分题型**：inference 最难（`recall@5` **0.545**）、comparison 0.698、temporal 0.728。
- 关键词通道只改**排序**、不改**覆盖**：`base:vector` 与 `base:hybrid` 的 `recall@50`
  完全相同（0.9645），但 `recall@5` 0.632 → 0.650、`recall@1` 0.246 → 0.253。
- **截断表征确有贡献**（离线交叉表）：fact 全在送嵌窗口内的 gold 槽（3976）
  `recall@50=0.979 / recall@5=0.708`；全在窗口外的（1903，31.2%）只有
  `recall@50=0.936 / recall@5=0.434`。粗略归属：**59%（121/204）的覆盖缺槽**与
  **大部分 @5 排序损失**都落在这批"fact 看不见"的槽上。
- `null_query`（301，描述性，ADR-0017 不校阈值）：top-1 分数 mean 0.492 / max 0.648
  （与可答题的分数分布重叠，**不能**据此定拒答阈值）。
- 确定性：全量 `base:hybrid` `run_hash=48503510bedd9655`，`base:vector=fd57550e387d13f8`；
  **两次独立**、干净 trace、`--limit 120` 的复跑逐位相同
  （`base:hybrid=c95cc737bea0fce5`、`base:vector=c8214716d7bfc01c`）。

**对 #48 的含义（供架构层决策，不是本票结论）**：单次检索在多跳证据上是**欠召回**的
（`k≤10` 时 22–35% 缺失），尤其 3–4 跳与 inference 型；其中**多数缺失金标已在候选池内
（排序问题）**，少数从未进入（覆盖问题，且约六成与送嵌截断相关）。迭代检索至少有
`1 − recall@k` 的可测上限。

## 交付物

- `multihop.py` — 数据集 → 独立语料（`data/articles/*.md`）/ 条目级评测集 / 检验报告 /
  送嵌窗口诊断。
- `build_index.py` — importer（`base` 生产默认截断 / `full` 整篇送嵌）。
- `run_census.py` — one-shot census + 分题型 / 分篇数 / 覆盖-vs-排序 / 窗口交叉表 / `run_hash`。
- `inspect_dataset.py` — 数据集检验（不加载模型）。
- `report.md` + `artifacts/report.json` — 生成的报告（证据）。
- `artifacts/per_query_ids.json` — 逐题明细（id 序号 / 排名 / 指标，**无数据集正文**）。

## 局限（ADR-0026 D5）

- **外部语料只能反证、不能正证本 KB**：它规模更大、多跳更密集——它无头寸则我们更不可能
  有；它有头寸也**不证明**我们有。域 / 语言 / 题型不可迁移；它测**引擎**，不测包边界。
- **单位是整篇文章**（论文口径是 256-token chunk）。条目级 recall 对长文更宽松
  （一篇文章进 top-k 即算命中），但与生产记忆条目（一条 = 一个文件）同形。
- **送嵌截断未做因果 A/B**：`full` 变体（整篇送嵌）本票**未跑**；"截断贡献"是离线
  交叉表（相关，非因果）。离线统计能**排除**也能提示，**不能证明**——要证明需跑 `full`。
- 未开 rerank；无 LLM 在环（那是 #48）。
- 实测成本远高于原估（build ~47min + census ~48min，hybrid ≈1.08s/题，瓶颈是关键词通道
  每题为 609 篇长文做子串扫描），后续层 2 估算需以此为据。
