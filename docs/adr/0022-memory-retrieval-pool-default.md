# 0022 记忆检索默认：候选池 20 → 14（#30 追加：默认融合 = 加法关键词增强）

Status: accepted（池 14 的 provisional 已由 #30 解除；#30 追加 D4–D6）

## 背景

rerank 主导记忆检索延迟（`experiments/rerank-latency-survey/`），且近线性于候选池（同场交错实测 `s/pair ≈0.60–0.64`）。`MEMORY_RETRIEVAL_POOL` 原默认 **20**。本次在 `#21` 下做了池大小调查（离线 + 端到端验证 + 同场延迟 bench）。

关键实测（`memory_agent/eval/retrieval_eval.py`，51 题 = 45 有答案；rerank-only 延迟为 5 题 × 3 轮交错）：

| pool | nDCG@10 | recall@1 | MRR | rerank 延迟/题 |
|---|---|---|---|---|
| 20（原默认） | 0.965758 | 0.859259 | 0.977778 | 13.34 s |
| 16 | 0.965758（逐位打平） | 0.859259 | 0.977778 | 10.91 s |
| **14** | **0.970870 (+0.53%)** | **0.881481 (+2.59%)** | **0.988889 (+1.14%)** | **9.00 s (−32.5%)** |
| 12 | 0.963642 (−0.22%) | 0.881481 | 0.988889 | — |

**非单调**（12 挂 / 14 超 / 16 平）。

## 决策

- **D1 默认 `RETRIEVAL_POOL = 14`**（覆盖 20）。
- **D2 环境可回退**：仍走 `MEMORY_RETRIEVAL_POOL`，设 20 即回旧行为。
- **D3 依据 = 记忆语料实测**，不跨语料外推（legal 的 `ADAPTIVE_POOL` 不动）。

## 理由

- **14 在质量与延迟上同时优于 16 与 20**：nDCG@10 +0.53%、recall@1 +2.59%、MRR +1.14%，rerank 延迟 13.34→9.00 s（−32.5%）。
- **机制（reranker 不单调）**：cross-encoder 对候选独立打分，**额外候选既可能是召回保险、也可能是干扰源**——池越大，越多机会让非 gold 候选压过真 gold（`q042` 即此）；池越小，则可能把 rank 13–20 的 gold 挤出（`q010/q016/q020`）。故存在**最优中间值**，而非"越大越好"。
- 12 已越界（gold 被挤出 > 干扰减少的收益）→ 取 14。

Considered options:

- **20（原）**：更保守，但更慢且质量并不更好。
- **16**：逐位打平、零余量、比 14 慢 → 被 14 支配。
- **14（采用）**：质量↑ + 延迟↓。
- **12**：nDCG@10 低于基线（守门未过）。

## Consequences

- 记忆检索（`memory_search`）默认候选池变小；返回条数（k）语义不变。
- 依赖"大候选池"的下游行为需复核（当前无）。
- **样本脆弱性**：14 的 +0.53% nDCG / +2.59% recall@1 由少数题驱动（`q042` 升、`q010` 降）；语料增长后须重跑并重验（`#24` harness）。
- 与 **#29（换更小 reranker）** 正交，可叠加。
- 验收指标口径见 **ADR-0021**（主 recall@1/MRR）。

Relates: #21、#24、#28、ADR-0020、ADR-0021、`experiments/rerank-latency-survey/{pool_results,pool_validation,pool_latency_bench}.md`。

## #30 追加（2026-09-15）：默认融合改为加法关键词增强

Status: accepted。

### 背景

本 ADR 的池实验建立在**旧（手写）融合**——关键词命中分数 `1.0+matched/len(kw)`（>1）
**无条件排在余弦之前**——之上。`#30` 实测该融合把**默认链路（`MEMORY_RERANK=0`）**的
recall@1 从纯向量的 0.6407 拖到 **0.2500**（2.5× 劣化）。故「池默认 14」在换融合后需重测。

证据：`experiments/fusion-selection/`（对照表 + 池曲线 + rerank 交互），评测集 = #24（51 题）。

### 决策

- **D4 默认融合 = 加法关键词增强**：`score = 余弦 + keyword_weight × (命中关键词数 / 关键词数)`，
  `keyword_weight` 默认 **0.05**；关键词独占候选（不在向量池）作池尾补充（≤`keyword_cap`）。
  候选身份按 `metadata.entry_id` 去重（跨仓库同文是不同条目，不再按文本折叠）。
- **D5 池默认 14 确认**（不改）：新融合链上重跑池曲线，14 仍最优（主指标 recall@1/MRR，
  见下），`ADR-0022` 原 provisional 解除。
- **D6 rerank 与融合解耦**：rerank 开时，旧/新融合的候选**并集**相同、只是排序不同，
  交叉编码器独立重打分 → 结果逐位相同。故融合只影响 **rerank 关（默认）**的体验；
  「是否默认开 rerank」是纯延迟/内存权衡（供 #29），不是质量必要项。

### 数据（45 有答案，gen-2）

融合对照（retriever-only，pool=14）：

| 变体 | recall@1 | nDCG@10 | MRR |
|---|---|---|---|
| **加法增强 β=0.05（采用）** | **0.7074** | **0.8817** | **0.8731** |
| 归一化加权 α=0.9（≈β=0.11） | 0.6630 | 0.8585 | 0.8427 |
| 纯向量 / 向量优先 | 0.6407 | 0.8524 | 0.8136 |
| RRF k=1 / k=60 | 0.5741 / 0.5000 | 0.8088 / 0.7210 | 0.7853 / 0.6848 |
| 旧默认（关键词优先） | 0.2500 | 0.5739 | 0.4936 |

- β 平台 **[0.05, 0.08] → recall@1=0.7074**；β=0.04→0.6796、β=0.09→0.6852、β≥0.10→0.6630。
- RRF 不适用的机制：关键词路是**低精度**表（CJK 二元组一题命中 ~55 条噪声），
  与高精度向量表等权/高分权融合会把它抬过头。有界加法（只加分、幅度 ≲ 余弦间距）是正解。

rerank 交互（pool=14）：新融合 0.7074 → +rerank **0.8815**（recall@1 **+0.1741**，
nDCG@10 +0.0892、MRR +0.1158）；与「旧融合 + rerank」**逐位相同**（0.881481/0.970870/0.988889）。

池曲线（新融合 + rerank）：pool 8→0.8593(1 miss)、10→0.8815、12→0.8815、**14→0.8815
（nDCG@10 0.9709 / MRR 0.9889，最优）**、16→0.8593、20→0.8593（主指标口径）。

### 理由

- 指标对应消费者（ADR-0021）：`memory_search` 取第一条 → 主指标 recall@1/MRR；加法增强在两者上均超纯向量。
- 旧口径的结构性缺陷：关键词分数恒 >1 → 任意词面噪声都压过全部向量命中；有界加法去掉这个「无条件」。

Considered options：

- **A 加法关键词增强（采用）**——简单、有界、可解释；β 有平台、非尖峰。
- **B RRF（弃）**——本语料关键词路低精度，被噪声带偏（< 纯向量）。
- **C 归一化加权（弃）**——等价于某 β 的加法，但引入每 query 归一化噪声，无增益。
- **D 直接纯向量（弃）**——最简但放弃词面信号，recall@1 停在 0.6407。

### Consequences

- `ragcore/strategies/default.py::DefaultRetrievalStrategy` 默认融合改变；memory 与任何用
  default 策略的调用方同步生效（legal 用 `LegalRetrievalStrategy`，不受影响）。
- `retrieval_eval.py --mode hybrid` 的数字随之改变（0.2500 → 0.7074）；历史 ablation 仅作旧口径存档。
- 关键词通道不再「补召回」到头部，只做**池内重排**；`keyword_cap` 仅管池尾补充。
- 未决：β 是否随语料/模型变化（换 embedding 后可能需重扫）；reranker 是否默认开（#29）。

Relates（#30 追加）：#21、#24、#29、ADR-0013（daemon 内存）、ADR-0021、ADR-0023、
`experiments/fusion-selection/`。

## #29 追加（2026-09-16）：记忆 rerank **不设默认**，方案保留（jina int8，memory-only）

Status: **accepted**（owner 拍板：不设默认、推迟实现、保留方案）。

### 背景

本 ADR 的 D6 把「是否默认开 rerank」留给 #29。#29 横评 5 个候选 cross-encoder（证据
`experiments/rerank-model-survey/`）：

- **许可干净（MIT/Apache）候选全部过不了质量闸门**：`bge-base`(MIT) recall@1 −16.7pp、
  `gte-multilingual`(Apache) −8.3pp、`mxbai`(Apache，仅英文) −8.3pp。
- **唯一「质量守住 + 显著提速」= `jina-reranker-v2-base-multilingual` int8 ONNX**
  （CC-BY-NC-4.0）：12 题子集 recall@1/MRR 与 m3 逐位持平（0.9375 / 1.0000）、nDCG@10 略高
  （0.9837 vs 0.9795）；重排延迟 **2366ms vs 11313ms（4.8x）**。

**但「值不值」按消费者口径复核后结论反转**（本次补充分析，数据源 `experiments/fusion-selection/`
逐题 rankings + `experiments/rerank-model-survey/cache/anchor_retrieval_eval_m3.json`）。
`memory_search` 默认 **k=5**，agent 读的是返回的那几条 → 真实消费者口径是 **recall@5**，不是 recall@1：

| 口径 | rerank 关（β=0.05, pool 14） | rerank 开（m3, pool 14） | Δ |
|---|---|---|---|
| recall@1 | 0.7074 | 0.8815 | +17.4pp |
| MRR | 0.8731 | 0.9889 | +11.6pp |
| **recall@5（实际返回）** | **0.9185** | **0.9685** | **+5.0pp（≈2/45 题）** |
| recall@10 | 0.9741 | 0.9796 | +0.6pp |

- rerank 是**排序**不是**召回**：尾部已饱和（两边 ~97–98%），候选池本就几乎含 gold。头条 +17.4pp
  是「只看 rank-1」的产物；消费者口径只有 **+5.0pp**。
- **免费杠杆已占位**：`recall@10（不重排）=0.9741 > recall@5（重排）=0.9685`——「多返回几条、不加重排」
  在「gold 在读到的集合里」上就赢了（成本仅 LLM 上下文）。
- **+5pp 显著性未验**：ADR-0021 的噪声带未定，2/45 题可能落在噪声内。

成本：内存 +0.58GB（jina int8，可接受）；延迟 jina ~2.4s/题（每次 `memory_search` 都付）；
程序适配大头 = **`optimum[onnxruntime]` 会把 `transformers` 由 5.x 降到 4.57.x（整仓）**；
许可 CC-BY-NC-4.0（不可逆）。

### 决策

- **D7 不设默认**：`MEMORY_RERANK` 保持默认 **0**（不启用 rerank）。消费者口径增量仅 +5.0pp 且
  未验显著，不足以换 NC 许可 + 整仓依赖降级 + ~2.4s/题。
- **D8 方案保留（owner 认可）**：若将来启用，采用 **jina-reranker-v2-base-multilingual int8 ONNX**、
  **仅 memory 作用域**（legal 继续 m3，回归锚点不动）、**接受 CC-BY-NC-4.0**（定位 = 个人知识库、
  非商业用途）。
- **D9 推迟实现**：本决策**不落代码**；实现票 `#35` 标记 **deferred**（移出 `ready-for-agent`），
  触发条件满足时再启动。
- **D10 留缺口（seam）**：不关闭该选项——复用既有接缝 `MEMORY_RERANK` / `MEMORY_RERANK_MODEL` /
  `default_reranker_factory`（无需改结构即可挂新模型）；将来给 `RerankerService` 加 ONNX 参数即可。
- **D11 触发条件（何时重看）**：(a) 实证 agent 依赖 rank-1（消费者口径回到 recall@1）；
  (b) 语料放大到 first-stage 明显退化；(c) 出现许可干净且过闸门的候选；(d) ONNX 依赖代价消除。
- **D12 约束登记**：将来实现前须复验 `optimum`/`transformers` 版本影响与 jina 许可。

### 理由

- 按消费者口径（k=5）衡量，重排的可见收益被**免费杠杆**（返回更多条）与噪声带吃掉；而代价
  （NC 许可不可逆 + 整仓依赖降级）高，故**不启用**。
- owner 认可 jina int8 方案本身（延迟效益高、个人非商用），故**保留而非废弃**。

Considered options：
- **A 不设默认 + 保留方案（采用）**——零成本、零风险；保留未来路径。
- **B 默认开 + jina int8（上一轮倾向，撤销）**——消费者口径收益不足，成本高。
- **C 默认开 m3**——CPU ~11.3s/题，交互不可用；否。
- **D 全局换 jina**——会改 legal 排名（重合 0.677）；否。

### Consequences

- 代码不变：默认链路 = 加法融合（recall@1 0.7074 / nDCG@10 0.8817），`MEMORY_RERANK` 默认仍 0。
- 优化优先级提高：**先动 k（返回条数）与 first-stage（BGE-M3 原生 sparse/colbert，见 ADR-0019 方向）**，
  再考虑第二模型（`#21` backlog）。
- `#35` 转 deferred；触发条件见 D11。
- 若将来启用：memory daemon 多 ~0.58GB；`ensure_hf_offline` 缓存清单须加 jina 权重；legal 不受影响。

Relates（#29 追加）：#29（证据已合并 master）、#35（deferred）、#21、#24、#30、ADR-0020、
ADR-0021、`experiments/rerank-model-survey/`。

## #35 复验（2026-09-16）：jina int8 零新依赖落地 + **全量**质量复核

Status: accepted（D8 由 owner 确认为「**若开则用 jina int8**」；D7 默认关不变）。

### 背景

#29（D8）选定 jina int8 ONNX，依据是 **12 题子集**上「质量逐位持平 + 4.8x」，且当时记录
jina ONNX 必须经 `sentence-transformers` 的 onnx backend → `optimum[onnxruntime]`，
会牵动 `transformers`（D12 要求实现前复验）。#35 落地时两项都复验了。

### 复验结论

- **D12 复验（依赖）**：`pip install optimum[onnxruntime]` 实测会把 `transformers`
  5.5.4 → **4.57.6**、`huggingface_hub` 1.31 → **0.36**（整仓）。**改为不需要 optimum**：
  jina 的 ONNX 图（`onnx/model_int8.onnx`，输入 `input_ids`+`attention_mask` → `logits`）
  用已在装的 `onnxruntime` + `tokenizers` 直跑，**零新依赖、不动 transformers**
  （`memory_agent/memory/onnx_reranker.py`，`MEMORY_RERANK_BACKEND=onnx`）。
- **全量质量复核（51 题，同 gen-2 / 同候选池）**：**#29 的 12 题子集（q001–q012，偏易）
  掩盖了差异**——全量下 jina **不是**「逐位持平」：

  | 重排器 | recall@1 | recall@5 | nDCG@10 | MRR | 延迟/题 |
  |---|---|---|---|---|---|
  | m3（torch） | **0.8815** | 0.9685 | **0.9709** | **0.9889** | 11.68 s |
  | jina int8 ONNX | 0.8315（**−5.0pp**） | **0.9685（同）** | 0.9443 | 0.9426 | **2.67 s（4.4x）** |

  手写 ONNX 实现与 #29 的 sbert-ONNX 路径在前 12 题**排名 0 处不一致** → 差异源于模型本身。
- **消费者口径**（`memory_search` 默认 k=5）两者 **recall@5 相同（0.9685）**：−5.0pp 只体现
  在 rank-1/3 排序，不改变 agent 读到的 5 条。

### 决策

- **D8 确认（owner 2026-09-16）**：memory rerank **若开则用 `jina-reranker-v2-base-multilingual`
  int8 ONNX**（`MEMORY_RERANK_BACKEND=onnx`、`MEMORY_RERANK_MODEL` 指向该模型）。接受
  CC-BY-NC-4.0。**默认仍关**（D7 不变），m3 仍是 `MEMORY_RERANK_BACKEND=torch` 的缺省。
- **D10 落地**：接缝已从「预留」变「已实现」——`default_reranker_factory` 按后端分派，
  两后端接口同形；权重优先走 HF 缓存（离线可用），缺文件默认不联网。

证据：`experiments/rerank-jina-35/`（脚本 + 三档 JSON + 结论）。

Relates（#35 复验）：#35、#29、#21、ADR-0020/0021、`experiments/{rerank-jina-35,rerank-model-survey}/`。
