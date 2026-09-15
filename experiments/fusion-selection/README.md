# 默认检索融合对照与选型（#30）

> Part of **#21**。对象 = **记忆检索**（#24 评测集）。**retriever-only（rerank 关）**为主，
> 另跑一次 rerank 链路量增量。索引固定 **gen-2**（134 条）。决策落 `docs/adr/0022`（就地 amend）。

## 问题

生产默认 `MEMORY_RERANK=0`，默认链路 = `DefaultRetrievalStrategy` 的**关键词优先**融合：
关键词命中被编码成 `1.0 + matched/len(kw)`（**>1**）→ 无条件排在余弦之前。基线
（`memory_agent/eval/retrieval_baseline.md`）显示这把 recall@1 从纯向量的 **0.6407** 拖到 **0.2500**。

## 假设

1. 关键词优先把「CJK 二元组子串命中」的噪声顶到所有向量命中之上 → 是主因（`pool_results.md` 已指认）。
2. 改成**有界加法增强**（`余弦 + β·命中强度`）或归一化加权/RRF 可把默认链路拉回 ≥ 纯向量。
3. 融合顺序对 **rerank 链路**应几乎无影响（reranker 独立重打同一候选并集）→ rerank 的增量与融合无关。

## 设置

- 评测集：`memory_agent/eval/retrieval_eval_set.json`（51 题 = 45 有答案 + 6 无答案；条目级二值）。
- 召回：一次抓**原始两路**（向量 top-40 + 关键词全量命中），各融合**离线**套用 →
  所有变体面对逐字相同的候选集合，只差融合规则。关键词抽取 `max_keywords=12`、批次 `cap=6`。
- 一致性：离线 `vector` / `kw_first` 在 pool=20 复现 #24 ablation（`vector:OK，kw_first:OK`；
  `kw_first` 的 MRR 取全排名 0.4936——历史 ablation 只存 top-10，q042 的 gold 在 rank 12 被截断）。
- 环境：Windows / CPU；BGE-M3；rerank 段用 `BAAI/bge-reranker-v2-m3`、送排截断 512 字。

## 数据

### A. 融合对照（retriever-only，pool=14，45 有答案）

| 变体 | recall@1 | recall@3 | recall@5 | recall@10 | nDCG@10 | MRR | misses |
|---|---|---|---|---|---|---|---|
| **boost_b0.05（选定）** | **0.7074** | 0.8685 | 0.9185 | 0.9741 | **0.8817** | **0.8731** | 0 |
| weighted_a0.9 (=β≈0.11) | 0.6630 | 0.8685 | 0.9185 | 0.9741 | 0.8585 | 0.8427 | 0 |
| vector（纯向量） | 0.6407 | 0.8685 | 0.9185 | 0.9741 | 0.8524 | 0.8136 | 0 |
| vec_first（关键词只作池尾） | 0.6407 | 0.8685 | 0.9185 | 0.9741 | 0.8524 | 0.8136 | 0 |
| rrf_k1_cap20（RRF，k=1） | 0.5741 | 0.8333 | 0.8611 | 0.9685 | 0.8088 | 0.7853 | 0 |
| rrf_k60_all（RRF，k=60） | 0.5000 | 0.6630 | 0.7870 | 0.9185 | 0.7210 | 0.6848 | 0 |
| kw_first（旧默认） | 0.2500 | 0.5000 | 0.5556 | 0.9333 | 0.5739 | 0.4936 | 0 |

**β 扫描（pool=14，retriever-only）**：平台 **β∈[0.05, 0.08] → recall@1=0.7074**；
β=0.04→0.6796、β=0.09→0.6852、β≥0.10→0.6630。取平台（也是 nDCG/MRR 最优）的 **0.05**。
完整 31 变体 × pool 8/10/12/14/16/20 见 `fusion_results.md`。

**读数**：
- `rrf` 单独列一档：关键词列表是**低精度**表（CJK 二元组使一题命中 ~55 条，多为噪声），
  与高精度向量表等权融合会把它抬过头 → RRF 反而低于纯向量，不适合本语料的两路信号。
- `vec_first` 与纯向量逐位相同：当向量池（14）≥ 返回数（10）时，「关键词作池尾」等于不生效。
- 选定方案是**在余弦上叠加有界词面分**，只在余弦间距内重排，不翻转明显更高的余弦。

### B. 与 rerank 的交互（pool=14，45 有答案）

| 链路 | recall@1 | recall@10 | nDCG@10 | MRR |
|---|---|---|---|---|
| 新融合（retriever-only） | 0.7074 | 0.9741 | 0.8817 | 0.8731 |
| **新融合 + rerank** | **0.8815** | 0.9796 | **0.9709** | **0.9889** |
| 增量（rerank） | **+0.1741** | +0.0055 | **+0.0892** | **+0.1158** |
| 旧融合 + rerank（ADR-0022，对照） | 0.8815 | — | 0.9709 | 0.9889 |

- rerank 延迟（生产 `retrieval_eval`，51 题）：mean **13.741s**、p50 13.383、p95 22.41、max 23.169；
  1305 pairs 实测 **0.618 s/pair**（`rerank_pool_curve.json`）。
- **旧融合 + rerank 与新融合 + rerank 逐位相同**（0.881481 / 0.970870 / 0.988889）：两路融合的
  候选**并集**相同，只是排序不同，而交叉编码器独立重打分 → 融合顺序在 rerank 开时**无关紧要**。
  故：**修融合主要是修「默认（rerank 关）」体验**；rerank 增量与融合方式解耦。

### C. 池曲线（新融合）

| pool | retriever-only recall@1 / recall@10 | +rerank recall@1 | +rerank recall@10 | +rerank nDCG@10 | +rerank MRR | misses |
|---|---|---|---|---|---|---|
| 8 | 0.7074 / 0.9519 | 0.8593 | 0.9370 | 0.9368 | 0.9667 | 1 |
| 10 | 0.7074 / 0.9741 | 0.8815 | 0.9593 | 0.9588 | 0.9889 | 0 |
| 12 | 0.7074 / 0.9741 | 0.8815 | 0.9667 | 0.9636 | 0.9889 | 0 |
| **14** | **0.7074 / 0.9741** | **0.8815** | **0.9796** | **0.9709** | **0.9889** | 0 |
| 16 | 0.7074 / 0.9741 | 0.8593 | 0.9852 | 0.9658 | 0.9778 | 0 |
| 20 | 0.7074 / 0.9741 | 0.8593 | 0.9852 | 0.9658 | 0.9778 | 0 |

- retriever-only 上 pool≥10 即封顶；rerank 链上 **14 仍是最优**（主指标 recall@1/MRR），
  16/20 的 recall@1 反而回落、12 的 nDCG@10 略低、8 丢 1 个 miss。
- 方法：候选集对 pool **单调包含** + reranker 对 (query,doc) 独立打分 → 只对**最大池并集真重排一次**
  （1305 pairs / 806s），小 pool 用缓存离析。pool14 结果与生产 `retrieval_eval` 逐位一致
  （0.881481/0.970870/0.988889）→ 离析可信。

## 结论

1. **选定默认融合 = 加法关键词增强**：`score = 余弦 + 0.05 × (命中关键词数/关键词数)`，
   recall@1 **0.7074 ≥ 0.6407（纯向量）**，nDCG@10 0.8817 > 0.8524，MRR 0.8731 > 0.8136；
   比现状 0.2500 高 2.8×。落在 β 平台 [0.05, 0.08] 内，且为 nDCG/MRR 最优点。
2. **RRF/归一化加权在此语料不适用**：关键词路低精度，等权/高分权融合反被噪声带偏；
   有界加法（关键词只加分、且幅度 ≲ 余弦间距）是正解。
3. **rerank 增量大且与融合解耦**：+0.1741 recall@1 / +0.0892 nDCG@10；旧/新融合在 rerank 下逐位相同。
   → 「是否默认开 rerank」是**延迟/内存 vs 质量**的权衡（13.7s/题、~2.2GB），
   不是质量必要项（默认（rerank 关）已被修到可用），供 `#29` 决策。
4. **池默认 14 确认**（ADR-0022 的 provisional 解除）：新融合链上 14 仍最优，不改。

## 复现

```powershell
chcp 65001 > $null; $env:PYTHONIOENCODING="utf-8"
$env:MEMORY_INDEX_DIR="D:\python_work\work2026-4\Agent-Knowledge-Base\memory_agent\vector_db"

# A/C(retriever) 融合对照 + 池曲线（仅 BGE-M3，~1.5 分钟）
venv\Scripts\python.exe experiments/fusion-selection/bench_fusion.py `
  --pool-curve 8 10 12 14 16 20 --out experiments/fusion-selection/fusion_results.json

# 生产链路复跑（新融合默认；hybrid 无 rerank）
venv\Scripts\python.exe memory_agent/eval/retrieval_eval.py --mode hybrid
# 新融合 + rerank（~12 分钟）
venv\Scripts\python.exe memory_agent/eval/retrieval_eval.py --mode hybrid-rerank `
  --out experiments/fusion-selection/rerank_hybrid_pool14.json

# C(rerank) 池曲线（一次重排 + 缓存离析，~15 分钟）
venv\Scripts\python.exe experiments/fusion-selection/bench_rerank_pool.py `
  --out experiments/fusion-selection/rerank_pool_curve.json
```

## 噪声带与局限

- 45 有答案；单题对均值可动 ~1/45≈0.022（单 gold 题）。boost vs vector 的 +0.0667 ≈ 3 题，
  在噪声带外；**新融合+rerank vs 旧融合+rerank 差 0（逐位相同）**；rerank 增量 +0.1741 远超噪声。
- query 由来源条目生成、偏易（词面重叠）→ 只做**相对**比较，不作绝对质量。
- 跨仓库同文（`issue-tracker.md` 三份）按 entry_id 去重（旧实现按文本去重会折叠，已修）。
- 索引固定 gen-2；语料增长后需重建并重跑。

## 产物

| 文件 | 内容 |
|---|---|
| `bench_fusion.py` | retriever-only 融合对照 + 池曲线（离线套用，含 baseline 复现校验） |
| `bench_rerank_pool.py` | rerank 链池曲线（一次重排 + 缓存离析） |
| `fusion_results.json` / `.md` | 31 变体 × pool 8–20 全量指标 |
| `rerank_hybrid_pool14.json` | 生产 `retrieval_eval --mode hybrid-rerank`（pool14）逐题明细 |
| `rerank_pool_curve.json` / `.md` | rerank 池曲线 + pairs/延迟 |
