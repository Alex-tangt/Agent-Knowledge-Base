# standard_rag_set_71 — 标准 RAG 集选型与一次性基线（#71 阶段 4）

> 数据与口径：`standard_rag_set_71.census.{md,json}`（census）、`standard_rag_set_71_results.json`（逐题明细）；
> 执行器 `../../../eval_71_standard_set.py`；总证据 `../../../eval_71_results.md` §4。
> 本目录用来满足「实验留痕」：**问题 → 假设 → 设置 → 数据 → 结论**。

## 问题

线②（agentic search）需要一条**外部可比**的单跳检索基线：既要有**单跳检索质量**这个面，
又要在**分钟~小时级**内做得起（本机 CPU、与另一 teammate 同机并行）。
候选众多（BEIR 系 / 中文 MTEB 子集 / CRUD-RAG 一类），必须在**跑之前**用事实把范围收敛。

## 假设

- **H1**：BEIR 系里存在**规模最小且 qrels 干净**的单跳集，能在 ≤2h 内整集建索引。
- **H2**：按 `47min/609 篇 ≈ 4.6s/篇` 的 MultiHop-RAG 锚点外推，BEIR 最小的 scifact（5,183）/
  nfcorpus（3,633）都"超预算"——**这个外推不一定成立**（文档长度差一个数量级），
  故必须实测吞吐再判（「杠杆是语料相关的」）。
- **H3**：中文检索集在本轮**不可用**（规模 10 万级 + 许可卡缺失）→ 语言维度将如实记为未覆盖。

## 设置

1. **分钟级 census**（不跑模型）：HF `api/datasets/<id>`（许可 / 标签）+ `datasets-server/info`（规模）
   + `/parquet`（字段与 id 对齐核对）；12 个候选，只列事实（规模 / 语言 / 许可 / 是否需重嵌）。
2. **实测吞吐**（先于选型定案）：在选定的 SciFact 上先跑 gold-complete **283 篇**档，
   记录**含模型加载与落盘**的整档耗时 → 外推整集。
3. **选型硬约束**：整集建索引 **≤2h**；超预算则用**文档化子集**，并写清"子集 ⇒ 只作内部 paired 对照"。
4. **一次性基线**：选定集上跑生产默认链（native hybrid：BGE-M3 dense + BM25 sparse + DBSF，rerank 关），
   300 test query；再加一条 **dense-only** 对照臂做 **paired Δ + bootstrap CI**（不显著也报）。

## 数据

- **census**：见 `standard_rag_set_71.census.md`。关键事实——BEIR 系全 `cc-by-sa-4.0`，
  最小 corpus 是 nfcorpus 3,633 / scifact 5,183；中文候选（T2Retrieval / DuRetrieval /
  CmedqaRetrieval / MMarcoRetrieval）**全部 10 万级**且多数**卡缺 license**；CRUD-RAG 取不到 HF 卡。
- **id 对齐核对**：`BeIR/scifact` corpus `_id` ∩ `BeIR/scifact-qrels` test `corpus-id` = **283/283**；
  qrels query-id ∩ queries `_id` = **300/300**（**三件套自洽**，见 census 的"实测来源"）。
  test qrels = 339 对 / 300 query / **283 金标篇** / 二值 / p50 1 篇/题。
- **文档长度**：p50 **1,330** 字（MultiHop-RAG 新闻正文 p50 7,836 字）→ H2 的外推确实不成立。
- **实测吞吐**：283 篇档 **315s**（含模型加载 17s）→ 1.11 s/doc；1,500 篇档 **1,420.8s** → 0.947 s/doc；
  全量 5,183 篇外推 ≈ **82 min**（<2h）。
- **基线结果**（1,500 篇 gold-complete 档 / 300 query）：`recall@1 0.711 / @5 0.843 / @10 0.900 /
  @50 0.968 / @100 0.978`；`nDCG@10 0.819`；`MRR 0.804`。
- **paired Δ（hybrid − dense-only）**：`recall@5 +0.0308` [+0.0008, +0.0608]、`nDCG@10 +0.0621`
  [+0.0390, +0.0865]、`MRR +0.0784` [+0.0511, +0.1076]（n=300）。
- **确定性**：复用同一索引重跑，hybrid 逐题 `run_hash` 逐位相同（`8879a20887df4c98`）。

## 结论

- **H1 成立**：采用 **SciFact**（`BeIR/scifact` + `BeIR/scifact-qrels`，cc-by-sa-4.0）——
  最小、单跳、qrels 干净二值；并额外提供 `--docs 0` 全量档（外推 82min，本轮未跑）。
- **H2 被实测推翻（锚点不可外推）**：文档长度差 ~6×，"4.6s/篇" 不适用；
  实测 0.95–1.11 s/doc ⇒ **≤2h 硬约束满足**，但仍按 ≤2k 锚点跑 1,500 篇档
  （全量档仅作为可点单档，避免长时间占 CPU）。
- **H3 成立**：中文/语言维度**未覆盖**，如实标注（这是 D5/D6 边界的一部分）。
- **词法通道在单跳集上有弱可测增益**（BM25 使 hybrid 优于 dense-only，三项 CI 不含 0；`recall@5` 弱显著）。

**边界（不得越读）**：
1. 1,500 篇子集里 18.9% 是金标（全量应 5.5%）→ **数字乐观于官方 BEIR**，**只作内部 paired 对照**，
   **不与论文/排行榜同轴**（ADR-0026 D5/D6）。
2. 外部语料**只作机制证据**、**不声称本库增益**；域（生物医学）/ 语言（英）/ 任务（单跳）不可外推。
3. 我们链路与官方榜不同（BGE-M3 + BM25 + DBSF、条目级整篇送嵌）→ 论文数字只作量级锚点。
