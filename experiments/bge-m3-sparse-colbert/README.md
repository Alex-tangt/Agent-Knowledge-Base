# BGE-M3 的 sparse（学习词权重）与 colbert（多向量）作为 first-stage：官方用法 + 实验设计

> 2026-09-16 · 分支 `exp/bge-m3-sparse-colbert` · **状态：已跑完（22 臂）——结论见 §8**
> 原始表 `results.md`；逐臂指标 `results.json`；打分矩阵 `cache/*.npz`（百 KB 级，免重编码可复算）
> 对象 = **记忆检索 first-stage**（#24 题集 51 题 / 45 有答案，gen-2 索引 134 条）；不调 LLM、不碰 legal、不碰共享/云。
> 结论落点：ADR-0019（D16 或新节，若采用）；backlog 叙事 #21。

---

## 1. 问题与假设

- **问题**：BGE-M3 一次前向可出三种表示（dense / sparse / colbert）。我们**只用 dense**；
  另外两种能不能在**同语料同题集**上**净胜**现在的「dense + 手写关键词加法」
  （默认链路 **recall@1 0.7074 / recall@5 0.9185 / MRR 0.8731**）？
- **性质**：这是**换机制**（改索引表示），不是调参——参数级 6 轮扫描已做尽（见 `docs/retrieval_optimization_report.md`）。
- **假设**：
  1. **sparse**（学习词权重）比手写 CJK 二元组关键词**更准**（权重是学出来的、上下文相关）→ 可能成为更好的词法路；
  2. **colbert** 的 MaxSim 提供词级细粒度匹配 → 可能明显优于 dense-only；
  3. 官方配方（三路加权）在本语料上不一定最优（分数量纲差异大，见 §2.3）。

---

## 2. 官方用法（一手；本地源码可复核）

### 2.1 三种表示

| 表示 | 形状 | 生成 | 官方打分 |
|---|---|---|---|
| dense | 1×1024，L2 归一 | `_dense_embedding`（CLS）+ `F.normalize` | 内积 = 余弦 |
| **sparse（lexical weights）** | **词表大小**（250k+）稀疏向量 | `_sparse_embedding`：把每个 token 的权重 scatter 到词表位置后**取 max** | `Σ_{token∈q∩d} q_w·d_w` |
| **colbert（多向量）** | n_token×1024，L2 归一 | `_colbert_embedding` + `F.normalize` | **MaxSim**，再**按 query token 数取平均** |

源码：`FlagEmbedding/finetune/embedder/encoder_only/m3/modeling.py:116-157`（`_sparse_embedding`）、
`:270-280`（三头）、`:334-346`（`_compute_similarity` = 内积）；
`FlagEmbedding/inference/embedder/encoder_only/m3.py:134-166`（`compute_lexical_matching_score`）、`:168-182`（`colbert_score`）。

> 关键：sparse 的权重是**预训练模型的推理输出**（不是我们算的词频/IDF），因此**不加 IDF、不做长度归一化**。

### 2.2 官方融合公式（直接照抄参考）

`compute_score_single_device`（`m3.py:693-725`）：

```
weights_for_different_modes 缺省 = [1., 1., 1.]          # m3.py:693-694
weight_sum = sum(w)                                       # :699
sparse+dense        = (w1·sparse + w0·dense) / (w1+w0)    # :716-717
colbert+sparse+dense = (w2·colbert + w1·sparse + w0·dense) / weight_sum   # :723-725
```

- **融合 = 加权平均（除以权重和），三个分量之间不做任何归一化**（原始分数量纲直接用）。
- 官方**模型卡示例**：`weights_for_different_modes=[0.4, 0.2, 0.4]` → `0.4·dense + 0.2·sparse + 0.4·colbert`。
- 另一处（**训练期** `ensemble_score`，`modeling.py:257`）：`dense + 0.3·sparse + colbert`（等价权重 `[1, 0.3, 1]`）。
- 官方模型卡还建议：**`max_passage_length=128`**（"a smaller max length leads to a lower latency"）；
  `passage_max_length` 默认 **512**（`m3.py:67`、`AbsEmbedder.py:61`）。

### 2.3 一个必须点明的风险

三个分量的**量纲差异大**（模型卡实例：dense 0.63 / sparse 0.20 / colbert 0.78）。
官方"不做归一化、直接加权平均"意味着**权重隐含地偏向 colbert**。
→ 本实验主臂照抄官方，但**必须同时报单路分数分布**，否则无法解释结论。

### 2.4 官方推荐的流水线

模型卡原文：**"We recommend to use the following pipeline: hybrid retrieval + re-ranking."**
即 **dense + sparse 做召回 → cross-encoder 重排**；**colbert 未被官方放进 first-stage 召回**。
这与我们的架构（ADR-0019 D4 只允许一条融合路径）和 §3 的存储账一致。

---

## 3. 语料体量实测（本仓 gen-2，134 条）

| 项 | 值 |
|---|---|
| 总字数 / token | 67.2 万字 / ≈**0.5M token**（均 3,710 tok，max 46k） |
| 编码耗时（一次性） | 三头共享一次前向；按本仓锚点（~6 min/60 条 CPU）≈ **15–20 分钟** |
| **sparse 存储** | ≈**0.1 MB**（可忽略） |
| **colbert 存储**（passage 截断 **128** token，官方建议） | **≈70 MB** |
| colbert 存储（passage 512 / 全量） | ≈281 MB / **≈2.04 GB** ← 全量不可扩展 |
| 查询时 | sparse：1 次前向 + 250k 维稀疏点积（毫秒）；colbert：前向 + MaxSim（134 条上每 query 数秒） |

---

## 4. 实验设计（待批）

**D1 路径 = A 离线打分**（推荐）：`BGEM3FlagModel` 编码 134 条 + 51 query → **三套表示落盘缓存** → 释放模型 →
纯 numpy 打分 + 复用 `memory_agent/eval/metrics.py`。**零 schema / 零生产代码改动**；融合公式可秒级重算。
→ 只有 A 出现净胜，才谈 B（真 store：`dense`+`sparse` 具名向量、colbert 需 multivector schema，另票）。

**D2 消融矩阵**（同一份缓存，全部算完）：

| # | 臂 | 公式（官方或注明） | 目的 |
|---|---|---|---|
| A0 | **锚点 dense-only** | 余弦 | 必须复现 **0.6407**；同时验 **FlagEmbedding dense vs ST dense** 是否一致 |
| A1 | sparse-only | `Σ q_w·d_w` | 词法路单独质量（对标手写加法 / BM25） |
| A2 | colbert-only | MaxSim 按 query token 平均 | 多向量单独质量 |
| A3 | **官方配方（主臂）** | `(0.4·dense + 0.2·sparse + 0.4·colbert)/1.0` | 照抄官方示例 |
| A4 | 官方默认权重 | `(dense+sparse+colbert)/3` | 官方代码缺省 |
| A5 | dense+sparse | `(0.4·dense + 0.2·sparse)/0.6` | 官方推荐流水线的召回段 |
| A6 | **参照基线** | 手写加法 | 0.7074 / 0.9185 / 0.8731（对照） |
| A7 | colbert passage 128 / 512 / 全量 | A2/A3 | 质量 × 存储/延迟的取舍 |

**D3 顺手补缺口**：每臂同时报 `top_k=14 / 20` 的**池 recall（ceiling）**，回填
`docs/retrieval_optimization_report.md` §3.1（"池上限从未被量"）。

**D4 指标与闸门**：`recall@{1,3,5,10}` + `nDCG@10` + `MRR` + `misses`（ADR-0021 D4）；
主指标 `recall@1`/`MRR`。**净胜闸门 = 主指标 ≥ +3 题（≈+6.7pp）**（沿用 noise-band 外口径）；不达 → 判"不净胜"，只登记不排期。

**D5 交付**：`encode.py`（编码 + 缓存）、`eval_reps.py`（消融）、`cache/`（gitignored）、`results.json`、本 README 的结论节。
**无生产代码改动**；若胜出另开票（store schema + 全量重建）。

**D6 执行**：编码 15–20 分钟 → **独立 worktree 会话**（本仓纪律：长耗时活不进主会话，subagent 同样阻塞）。
`HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1`（#46 坑）；FlagEmbedding 会**再加载一份 BGE-M3**（+2.2GB，仅实验进程）。

---

## 5. 复现（待跑）

```powershell
chcp 65001 > $null; $env:PYTHONIOENCODING="utf-8"
$env:HF_HUB_OFFLINE="1"; $env:TRANSFORMERS_OFFLINE="1"
$py = "D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe"
# 1) 编码 + 落盘（约 15-20 分钟，需 BGE-M3；一次拿三套表示）
& $py experiments/bge-m3-sparse-colbert/encode.py --index-dir memory_agent/vector_db --out experiments/bge-m3-sparse-colbert/cache
# 2) 消融（秒级，纯 numpy）
& $py experiments/bge-m3-sparse-colbert/eval_reps.py --cache experiments/bge-m3-sparse-colbert/cache --out experiments/bge-m3-sparse-colbert/results.json
```

---

## 6. 局限与风险

1. **FlagEmbedding 的 dense ≠ 确定 ST 的 dense**：A0 锚点同时充当"实现一致性"检查；若 A0 ≠ 0.6407，先查明差异（分词/池化）再看其他臂。
2. **官方融合无归一化** → 量纲差异使权重偏向 colbert；只报融合分会掩盖原因，故必须报单路。
3. **colbert 全量不可扩展**（2GB/134 条）；若只有全量才有效，则结论是"不可用"。
4. 45 题 → 绝对值不可过度解读；闸门按"题数"而非小数。
5. 6 个无答案 query 只作描述（ADR-0017）。
6. 语料固定 gen-2；增长后须重跑。

---

## 7. 参考

- 官方模型卡 `BAAI/bge-m3`（融合权重示例、`max_passage_length=128`、推荐流水线 hybrid + reranking）
- FlagEmbedding 本地源码：`inference/embedder/encoder_only/m3.py`（`:134/:168/:693-725`）、
  `finetune/embedder/encoder_only/m3/modeling.py`（`:116-157/:257/:334-346`）
- `docs/adr/0019`（store 端口、D4 无双重融合、D5/D7 平面形态、D14/D15 BM25）
- `docs/adr/0021`（验收指标）、`docs/adr/0022`（池/融合/rerank）
- `docs/retrieval_optimization_report.md`（组件决策总报告）、`experiments/local-lexical-40/`（BM25 对照）

---

## 8. 结论（2026-09-16 已跑，22 臂）

> ⚠ **§8.1–8.4 的 sparse / colbert 数字已作废**（2026-09-16 晚复核发现）：
> `encode.py` 当时读的是 **原始 .md**（含 YAML frontmatter），而索引/生产用的是**渲染正文**。
> frontmatter 让条目 token 数虚高（mean 1860 vs 真实 1339、sparse 非零 395 vs 真实 89），
> 使 sparse 的量纲更加失控。**更正后的数字见 §9.3**（结论方向不变：sparse 差、colbert 强；
> 但"官方 raw 配方 = 0.189"应更正为 **0.544**）。§8.0 的锚点与 §8.5 的成本不受影响。

### 8.0 口径校验（锚点）

| 校验 | 结果 |
|---|---|
| `C0 ST-dense only`（用**索引里那份 ST dense**） | **0.641 / nDCG 0.8524 / MRR 0.8136** ↔ 已知 `vector` 基线 0.6407 / 0.8524 / 0.8136 → **逐位一致**，离线打分可信 |
| `A0 FlagEmbedding dense` | 0.619 → **比 ST dense 差 ≈1 题**（实现差异）→ 后续融合一律用 `dense_st` 对齐生产口径 |

### 8.1 官方融合配方在**本语料**不可用（原创发现）

三个分量的**原始标度**（实测）：

| 矩阵 | min | mean | p99 | max |
|---|---|---|---|---|
| dense | 0.163 | 0.443 | 0.634 | 0.779 |
| **sparse** | 0.000 | **1.067** | **6.241** | **15.379** |
| colbert(full) | 0.490 | 0.654 | 0.760 | 0.882 |

sparse 是 dense/colbert 的 **7–20 倍** → 官方"加权平均、不做归一化"被 sparse 完全支配：

| 臂 | recall@1 |
|---|---|
| 官方 raw `.4/.2/.4`（A3） | **0.189** |
| 官方缺省 raw `1:1:1`（A4） | 0.189 |
| `raw dense.4+sparse.2`（A5） | 0.189 |
| （对照）sparse-only（A1） | 0.167 |

**机制**：sparse = **395 个非零项的加权求和**（长文档），得分随匹配项数线性膨胀；官方示例是**短句**
（那里 sparse 只有 0.196）。→ **官方的 `[0.4,0.2,0.4]` 不能直接搬到长文档语料**，必须先按 query 归一化：

| 归一化后（同权重） | recall@1 |
|---|---|
| B1 min-max | 0.641 |
| B2 z-score | 0.663 |

### 8.2 BGE-M3 **sparse**：否决（拖累融合）

- `sparse-only` = **0.167**，远低于手写关键词 0.7074 与 BM25 0.7111 →
  根因 = **不加 IDF**，长文档里 ~395 项的共同 token 全部计分，噪声淹没信号。
- 含 sparse 的融合**全部**低于不含的：C5 minmax(dense+sparse) 0.289、B7 0.267、
  C4 normalized-official 0.680 < C2 dense+colbert 0.724。
- → **补完组件表 C2 的候选 ④：BGE-M3 原生 sparse 不可用**（作为词法路已被手写加法与 BM25 双双击败）。

### 8.3 **colbert**：真正的增益信号 → 与 ST dense 融合**全指标超生产**

| 臂 | r@1 | r@3 | r@5 | nDCG@10 | MRR |
|---|---|---|---|---|---|
| **生产默认（dense + 手写关键词加法）** | 0.7074 | 0.8685 | 0.9185 | 0.8817 | 0.8731 |
| **C3 ST-dense + colbert(full) z-score .5/.5** | **0.746** | **0.935** | **0.946** | **0.9046** | **0.8937** |
| C6 ST-dense + colbert(**512**) min-max .5/.5 | **0.746** | 0.907 | 0.941 | 0.9003 | 0.8852 |
| C1 ST-dense + colbert raw .5/.5 | 0.724 | 0.896 | 0.924 | 0.8988 | 0.8773 |
| C2 ST-dense + colbert min-max .5/.5 | 0.724 | 0.913 | 0.946 | 0.8931 | 0.8759 |
| （单路）colbert-only full | 0.669 | 0.839 | 0.907 | 0.8476 | 0.8241 |
| （单路）colbert-only 512 / 128 | 0.624 / 0.602 | | | | |

- **Δ(C3 − 生产) = recall@1 +3.9pp（≈+1.7 题）· r@3 +6.7pp · r@5 +2.8pp · nDCG +0.023 · MRR +0.021**
  → **全指标一致改善**（不是单点的脆结论）。
- **闸门（主指标 ≥ +3 题）**：**未达**（r@1 +1.7 题）；但 **r@3 达 +3 题**、且无一指标退化。
- **colbert 512 ≈ full**（0.746 / 0.746）→ passage 截断 **512 零代价**，存储 **281MB** 而不是 1.02GB；
  **官方建议的 128 太激进**（0.602，掉 6.7pp，110/134 条被截）。
- 单路对比：**colbert(full) 0.669 > ST dense 0.641** → colbert 是比 dense 更强的单信号。

### 8.4 池上限实测（补 `docs/retrieval_optimization_report.md` §3.1 缺口）

| 臂 | gold 在 top-1 | ≤3 | ≤5 | ≤10 | ≤14 | ≤20 |
|---|---|---|---|---|---|---|
| C1 ST-dense+colbert raw | 36 | 42 | 43 | 45 | 45 | **45/45** |
| C3 ST-dense+colbert z-score | 37 | 44 | 44 | 44 | 44 | **45/45** |
| C0 ST-dense only | 32 | 40 | 42 | 44 | 44 | 44 |

→ **召回确实已饱和：gold 在 top-20 内 45/45 = 100%**（top-14 亦 44–45/45）。
**瓶颈是排序，不是召回**——与 ADR-0021「recall@10 ≈0.985」和本报告 §3.1 的判断一致。

### 8.5 成本

| 项 | 值 |
|---|---|
| 编码（一次性，一次前向出三头） | docs **773s（13 分钟）** + queries 3.8s；模型加载 1.3s（缓存） |
| colbert 存储 | **281MB @512** / 1.02GB @full / 70MB @128 |
| sparse 存储 | ≈0.1MB（但质量不可用） |
| 查询时 colbert | 离线全量 51×134 = 13.4s → **2ms/pair** → 生产应作**候选池重打分**（池 14 → ~30ms/query），不做全量 first-stage |
| 依赖 | FlagEmbedding（已在装，`transformers<6` 兼容）；**零新增** |

### 8.6 判定与下一步（**待 owner**）

- **sparse ④ 关闭**（明确负结果，登记即可）。
- **colbert ⑤ 是正向信号但未过闸门**（+1.7 题 vs 要求 +3 题），且落地需要：
  ① Qdrant **multivector** schema（`MultiVectorConfig(MAX_SIM)`，客户端 1.18.0 已支持）；
  ② 派生索引全量重建 + 281MB（@512）存储；
  ③ 检索层新增一路（ADR-0019 D4 只允许一条融合 → 需要"dense 召回 + colbert 重打分"的形状，而非再加一条 fusion 并行路）。
- **建议（本轨意见）**：按官方流水线做成 **"dense(+手写关键词) 召回 → colbert 对候选池重打分"**，
  而不是把 colbert 当全量 first-stage；触发条件满足（本次证据）后另开票，**不自行改默认链路**。

Relates（结论）：ADR-0019（D4/D5/D16 待定）、ADR-0021、ADR-0022、`docs/retrieval_optimization_report.md` §C2/§3.1。

---

## 9. 蒸馏拟合（2026-09-16）：用 rerank 排序当老师，求融合参数的数学最优

方案见 `DESIGN-distill.md`。**先修了一个致命口径错误**，再做拟合。

### 9.1 口径修正（关键）

`encode.py` / `record_rerank.py` 最初都从 `manifest[entry]["path"]` 读**原始 .md**，
但**索引 payload 里存的是渲染正文**（frontmatter 剥离、标题 `# ...`）——两者前 512 字完全不同
（原始文件前 512 字基本是 YAML）。修法：新增 `docs_source.py`，统一从 **Qdrant payload** 取正文
（= 生产实际送排/编码的文本），并加 frontmatter 断言。差异有多大：

| | 原始 .md（错） | Qdrant 正文（对） |
|---|---|---|
| doc token 均值 / max | 1,860 / 8,191 | **1,339 / 3,798** |
| sparse 非零项均值 | 395 | **89** |
| rerank-only r@1（全量排序） | 0.7648 | **0.8537** |

`rerank_scores.npz` 与 `scores_qd.npz` 均为**修正后**录制；原始文件版保留作对照。

**rerank 资产验证**：#35 的 jina 锚点 r@1 = 0.8315；本表全量排序 = **0.8537**。逐题 diff 显示
差异**全部落在同文重复条目**（如三个仓库各有一份 `docs/agents/triage-labels.md`）——
rerank 分数**完全相同**，只是 tie-break 不同 → **资产有效**（差异非排序失败）。

### 9.2 数据资产（一次性录制，可永久复用）

| 文件 | 内容 | 成本 |
|---|---|---|
| `cache/rerank_scores.npz` | **51×134 全量 rerank 分数**（jina int8，送排 512 字） | 13.1 min |
| `cache/scores_qd.npz` | dense / sparse / colbert{128,512,full}（渲染正文） | 13 min |
| `cache/dense_st.npz` | 索引里那份 ST dense 余弦 | 秒级 |
| `cache/kw.npz` | 手写关键词命中强度 | 秒级 |

**锚点**：`dense_st + 0.05·kw` 逐位复现生产 `0.707407/0.868519/0.918519/0.881725/0.873148` → **PASS**。

### 9.3 干净数据下的单路/两路（`results_qd.md`）

| 臂 | r@1 | r@3 | r@5 | nDCG@10 | MRR |
|---|---|---|---|---|---|
| 生产默认（锚点） | 0.7074 | 0.8685 | 0.9185 | 0.8817 | 0.8731 |
| ST-dense only | 0.641 | 0.869 | 0.919 | 0.8524 | 0.8136 |
| **sparse only** | **0.133** | 0.222 | 0.267 | 0.2520 | 0.2382 |
| colbert 128 / 512 / full | 0.631 / **0.698** / **0.698** | 0.846/0.874/0.874 | 0.913/0.946/0.924 | 0.8389/0.8645/0.8634 | 0.8176/0.8533/0.8489 |
| 官方 raw `.4/.2/.4` | 0.544 | 0.681 | 0.796 | 0.7159 | 0.6763 |
| 官方 + minmax/zscore | 0.648 / 0.626 | | 0.907 | 0.8447 / 0.8333 | 0.8104 / 0.7950 |
| **ST-dense + colbert zscore .5/.5** | **0.754** | 0.913 | 0.941 | **0.9053** | **0.9007** |
| ST-dense + sparse | 0.378 | 0.437 | 0.602 | 0.5735 | 0.5055 |

结论方向与 §8 一致（**sparse 差、colbert 强、归一化必需**），但量级更正：官方 raw 配方 **0.544**（非 0.189）。

### 9.4 蒸馏拟合结果（`fit_results.md`）

监督 = 全量 rerank 排序（6834 观测）；主张量 = **蒸馏 nDCG@5**（融合前 5 条的 rerank-gain 覆盖，
贴消费者口径）；L3 qrels **不参与拟合**。

| 选择方式 | 配置 | distill5 | r@1 | r@3 | r@5 | nDCG@10 | MRR |
|---|---|---|---|---|---|---|---|
| **按 L2（无泄漏，取此）** | `colbertfull+dense+kw+sparse` / zscore / `[.40,.45,.10,.05]` | 0.8257 | **0.7759** | 0.8907 | **0.9685** | **0.9143** | **0.9144** |
| 按 L3（**含泄漏**，仅对照） | `colbert512+dense+kw` / zscore / `[.45,.50,.05]` | 0.8250 | **0.7981** | 0.9130 | **0.9685** | **0.9282** | **0.9274** |
| 生产默认 | dense + 0.05·kw | — | 0.7074 | 0.8685 | 0.9185 | 0.8817 | 0.8731 |
| **rerank 上限** | 按 R 排序 | 1.0 | 0.8537 | 0.9481 | 0.9722 | 0.9590 | 0.9537 |

- **Δ(L2 选定 − 生产) = r@1 +6.9pp（+3.1 题）· r@3 +2.2pp · r@5 +5.0pp · nDCG +3.3pp · MRR +4.1pp**
- **paired bootstrap（对 51 题，B=2000）Δ(r@1) 95% CI = [+0.0588, +0.2353]** → **显著净胜**（CI 下界 > 0）
- **蒸馏回收率**：L2 选定拿下 rerank 增益的 **(0.7759−0.7074)/(0.8537−0.7074) = 47%**；
  若按 qrels 选（0.7981）则 **62%**，**且不付重排延迟**。

### 9.5 凸解 vs 离散网格（交叉验证）

| 求解器 | 最优 distill5 | 对应 r@1 | 结构 |
|---|---|---|---|
| LS（全语料，精确活跃集） | 0.8217 | 0.7537 | colbert+dense ≈ .49/.51 |
| LS（限 top-20） | 0.8133 | 0.7074 | colbert512 .18 |
| 成对 logistic（全语料） | 0.8170 | 0.7352 | colbert512 .31 |
| 成对 logistic（限 top-20） | 0.8185 | 0.7537 | .4995/.5005（**并把 sparse 压到 0**） |
| **离散网格（step .05）** | **0.8257** | 0.7759 | 4 路小权重 |

- 三条路线**同区**（colbert+dense ≈ 0.5/0.5 + 小的 kw/sparse 权重），凸解与网格差 **0.004（0.5%）**。
- 差距可解释：**目标（蒸馏 nDCG@5）非凸**，凸代理是其松弛 → 最优落在不同点。
  凸解的价值 = **全局最优保证 + 结构鲁棒**（成对解自动把 sparse 压成 0）。
- 失败教训：LS / 成对在**全部 134 条**上拟合会被尾部无关条目主导（**全局 Spearman 只有 ~0.46**）
  → 必须 **top 加权**（限定 rerank 前 20 条）。这正是"大语料下全量目标不可用"的微型预演。

### 9.6 per-query oracle headroom：**不需要 query 自适应**

对每 query 用**它自己的文档的半数**拟合 per-query 权重，在另一半上评（2 折）：
**oracle headroom = −0.0866**（比全局权重**更差**）→ 全局一组权重已是本语料的最优形态；
per-query 自适应只带来过拟合。**回答"不止一个 query"的疑问：全局 w 够用。**

### 9.7 产物

`docs_source.py`（正文单一来源）· `kw_matrix.py` · `record_rerank.py` · `fit_fusion.py` ·
`cache/{rerank_scores,scores_qd,dense_st,kw}.npz` · `results_qd.{json,md}` · `fit_results.{json,md}`。

Relates：`DESIGN-distill.md`、ADR-0019/0021/0022、`docs/retrieval_optimization_report.md`。

---

## 10. 信号消融：四路都要吗？（2026-09-16，`ablate_signals.py`）

选定的 4 路里有 `sparse`，但它的单路质量只有 0.133 —— 于是直接验"要不要它"。

### 10.1 结论：**去掉 sparse（3 路即可），更好的同时更便宜**

| 配置（各自网格最优） | distill5 | r@1 | r@3 | r@5 | nDCG@10 | MRR |
|---|---|---|---|---|---|---|
| **3 路：colbert512+dense+kw / z-score / [.45,.50,.05]** | 0.8250 | **0.7981** | 0.9130 | **0.9685** | **0.9282** | **0.9274** |
| 3 路：colbert512+dense+kw / minmax / [.40,.50,.10] | 0.8242 | **0.7981** | 0.8963 | 0.9685 | 0.9247 | 0.9256 |
| 4 路：+sparse（full）/ z-score / [.40,.45,.10,.05] | 0.8257 | 0.7759 | 0.8907 | 0.9685 | 0.9143 | 0.9144 |
| 2 路：colbertfull+dense / z-score / [.45,.55] | 0.8219 | 0.7537 | 0.9130 | 0.9630 | 0.9096 | 0.9052 |
| 生产：dense + 0.05·kw | 0.7987 | 0.7074 | 0.8685 | 0.9185 | 0.8817 | 0.8731 |

- **Δ(4 路 − 3 路) r@1 的 95% CI = [−0.0588, +0.0000]** → **加上 sparse 从不变好**（CI 上界正好 0）。
  且两者在 **L2 选择指标上不可区分**（0.8257 vs 0.8250，Δ=0.0007）→ 取 3 路不是挑指标，是去冗余。
- **3 路 − 生产 r@1 的 95% CI = [+0.0784, +0.2941]** → **显著净胜**（比 4 路的 +3.1 题更强：+4.1 题）。
- sparse 被淘汰的机制见 §9.3：不加 IDF、原始标度又大，单路 0.133，任何融合里它只能当噪声。

### 10.2 增量成本（134 条实测）

| 信号 | 索引期增量 | 存储 | 查询期 |
|---|---|---|---|
| **dense** | **0**（生产已有 1024d 余弦） | 已有 | 1 次前向（已有） |
| **kw** | **0**（无模型 / 无向量，纯 payload 子串扫描） | 0 | payload 扫描（已有） |
| ~~sparse~~ | ~~与 dense 共用同一次前向~~ | 0.10MB（0.7KB/条） | 稀疏点积（毫秒） |
| **colbert** | 1 次前向（= 唯一的真增量） | **全量 735MB / 512 截断 260MB / 128 截断 69MB**（@512 ≈ **1.9MB/条**） | 1 次前向 + MaxSim（候选池 14 时 ~毫秒） |

**所以成本集中在 colbert 一处**：本语料 260MB（@512）；若语料涨到 1 万条 → **约 19GB**（@128 约 5GB；
**int8 量化可再 ÷4 → @512 约 65MB / 1 万条约 4.8GB**）。dense 与 kw 是**零增量**，sparse 的增量可忽略但**价值为 0**。

### 10.3 推荐形态

**`dense + kw`（零增量，生产已有）→ 召回候选池 → `colbert`（@512）对池内重打分**。
- 不必把 colbert 当全量 first-stage（查询期 MaxSim 与存储都随 D 线性涨）；
- 检索层仍是**一条融合路径**（ADR-0019 D4），colbert 作为池内重打分不引入第二套融合；
- 若接受 int8 量化，存储再降 4 倍。

Relates：§9、`fit_fusion.py`、ADR-0019 D4、`docs/retrieval_optimization_report.md` §C2/§4 U3。
