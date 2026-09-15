# 池子大小 vs 召回（#21 有界优化 · pre-rerank pool）

> 日期 2026-09-15 · 方法：**复用已有 trace + 只跑检索阶段**（**不装 reranker**）；对象 = memory 检索（#24 评测集）。
> 结论：**hybrid 送排池可从 ~26 裁到 10（无损上限），12 为安全默认**；预估省 ≈1.8–2.3 s/查询。

## 问题

rerank 耗时**近线性于候选池**（`experiments/rerank-latency/results.md`：20→4.6s、10→2.4s、8→2.0s）。问：**pre-rerank 池能裁到多小而不掉召回？**

## 方法（廉价优先）

- **复用**：`retrieval_baseline.json`/`.md`、`retrieval_ablation_{vector,hybrid}.json`（**只存了 top-10**）。
- **新跑（便宜）**：`--mode vector` / `--mode hybrid`，`MEMORY_RETRIEVAL_POOL=32`，只在**检索阶段**（无 reranker 加载；模型加载后 vector 11.9s / hybrid 14.7s）。
- **未跑**重活：`--mode hybrid-rerank`（≈880s/趟）——本页是**上限论证**，不是端到端实测（见「未决」）。
- **一致性校验**：top-10 与已提交 ablation **0/45 不一致**；recall@1/3/5/10 与 `retrieval_baseline.md` **逐位复现**（vector 0.6407/0.8685/0.9185/0.9741；hybrid 0.2500/0.5000/0.5556/0.9333）→ 更深的排名可信。
- 索引：gen-2，134 条（与基线同源）。

## 数据

### retriever recall@k（pre-rerank）

| k | vector | hybrid（关键词优先） | hybrid-rerank（对照，仅 top-10） |
|---|---|---|---|
| 1 | 0.6407 | 0.2500 | 0.8593 |
| 2 | 0.8389 | 0.4667 | 0.9333 |
| 3 | 0.8685 | 0.5000 | 0.9444 |
| 5 | 0.9185 | 0.5556 | 0.9685 |
| 8 | 0.9519 | 0.8833 | 0.9852 |
| 10 | 0.9741 | 0.9333 | 0.9852 |
| 15 | 0.9944 | 0.9667 | not measured* |
| 20 | 1.0000 | 0.9870 | not measured* |
| 32 | 1.0000 | 0.9926 | not measured* |

\* 基线 JSON 只存 top-10；未跑 880s 的 rerank。

### gold 命中位置（首个相关条目的排名）

| 模式 | p50 | p90 | p95 | max | miss |
|---|---|---|---|---|---|
| vector | 1 | 3 | 4 | 9 | 0/45 |
| hybrid | 2 | 8 | 9 | **12** | 0/45 |
| hybrid-rerank | 1 | 1 | 1 | 2 | 0/45 |

hybrid 唯一超过 10 的是 `q042`（rank 12）——而 rerank 也失败（first_hit_rank=2，R@1=0）。

### 最小池（精确曲线）

| 模式 | ≥0.90 | ≥0.95 | ≥0.98 | ≥0.8593（= 当前 rerank R@1） |
|---|---|---|---|---|
| vector | 4 | 6 | 11 | 3 |
| hybrid | 9 | 12 | 18 | 8 |

### 硬上限（决定性口径）

reranker 永远打不过 retriever recall@pool。用与 rerank R@1 相同的口径（gold 不在 top-k 则计 0）：

| pool | hybrid 上限 | vector 上限 |
|---|---|---|
| 5 | 0.5111 | 0.8370 |
| 8 | 0.8148 | **0.8593** |
| **10** | **0.8593** | 0.8593 |
| 12 | 0.8593 | 0.8593 |
| 20 / 26 / 32 | 0.8593 | 0.8593 |

→ **hybrid 池裁到 10 对上限无损**；<10 开始掉（k=8 → 0.8148）。vector 可裁到 8。

### 预估延迟收益（假设：≈0.23 s/pair，由 `rerank-latency` 两点斜率得出）

当前 `RETRIEVAL_POOL=20`，hybrid 合并后实际 ~**26**。

| 目标池 | 相对 20 省 | 相对 26 省 | bench 一致 |
|---|---|---|---|
| hybrid **10** | ≈2.3 s | ≈3.7 s | 4.6→2.4 s（≈48%） |
| hybrid **12** | ≈1.8 s | ≈3.2 s | — |
| vector 8 | ≈2.8 s | — | 4.6→2.0 s（≈57%） |

## 结论

1. **⚠️ 池子是降延迟主杠杆，但「无损」主张已被实测推翻**：hybrid 池 **12 实测** nDCG@10 **0.963642 < 0.9658（守门未过）**，延迟 **−49%** → 是**真权衡**，不是免费午餐。见 `pool_validation.md`。（本页「10 无损 / 12 安全」是**上限论证**，只保证「**首个** gold 在 top-k」，未保证「**全部** gold 在 top-k」。）
2. 预估 **省 ≈1.8–2.3 s/查询**（rerank 段近腰斩）。
3. 若走纯 vector 一阶段：池 8 无损。
4. 若要 ≥0.95 retriever 召回硬底线：vector k=6、hybrid k=12。

## 未决（→ 需一次验证）

- ✅ **已实测 pool=12**（`pool_validation.md`）：nDCG@10 **0.963642（守门未过，−0.22%）**、MRR +1.14%、recall@1 +2.59%、recall@10 −1.88%、misses 0/45、**延迟 879.7s→447.9s（−49%）**。**本页「无损」口径缺陷**：只验「首个 gold 在 top-k」，未验「全部 gold 在 top-k」（多 gold 题被挤出）。
- 未测：pool 14/16/18（不能断言"没有既过守门又省延迟的中间档"）。
- 语料增长后需重跑（索引变化 → 曲线变化）。

## 局限（写在脸上）

- **query 偏易**（由来源条目生成、词面重叠）→ gold 排名分布偏乐观；只适合**相对**比较。
- **跨仓库重复**（`issue-tracker.md`/`triage-labels.md` 三份）抬高召回与多 gold 数（q010/q020 各 4 gold）。
- **样本小**：45 有答案 / 59 gold；11–20 的尾部基本只有 q042，单题可动聚合 ~0.02。
- **0.23 s/pair 来自 legal 语料**（≤800 字块）→ 绝对节省量在 memory 上可能不同；只假定**线性**。
- 无答案 query 不参与池大小决策。

## 产物

- 临时目录 `C:\Users\Tan\AppData\Local\Temp\opencode\pool-analysis\`：`vector_k32.json`、`hybrid_k32.json`、`pool_report.json`、`analyze*.py`（**未提交**）。
- 复现：`MEMORY_RETRIEVAL_POOL=32` + `--mode vector|hybrid`（无 reranker）。
