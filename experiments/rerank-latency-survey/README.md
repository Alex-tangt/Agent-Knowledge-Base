# 调研：rerank 延迟——外部技巧库 + 候选模型（#21）

> 日期 2026-09-15 · 方法：**一手核对**（NirDiamant/RAG_Techniques 的 README 与 notebook 原文）+ 官方文档/模型卡；**未本地实测**（实测见下方「计划」）。
> 用途：#21 检索优化的方向输入；配套子票「序列长度上限」与「模型横评」。

## 问题

本地 CPU 上 rerank 主导端到端延迟（占 ~83%）。要回答：**能不能在不掉检索质量的前提下，降低 rerank 耗时或换掉它？**

## 假设

多数耗时来自三个可调杠杆（送排长度上限 / 候选池 / 模型体量），而非检索本身；"换更好的技巧"多半无效，**换更小的 cross-encoder 或设长度上限**才有效。外部技巧库（NirDiamant）可能只有范式、没有数字。

## 设置（本仓库实测事实，非外推）

| 事实 | 数字 | 来源 |
|---|---|---|
| rerank 占 e2e | **~83%**（total mean 18.6s；rerank mean **15.5s** / p95 **29.1s**） | `experiments/e2e-latency/results.md`（legal，20 题） |
| rerank vs 池子 | pool 20 → **4.6s**；10 → 2.4s；8 → 2.0s；40 → 9.2s（近线性） | `experiments/rerank-latency/results.md`（真实法条 ≤800 字） |
| batch | bs=32 → 4.6s vs bs=1 → **8.1s** | 同上 |
| 序列长度 | 模型默认 `max_seq_length=8192`；memory 侧 `MEMORY_RERANK_MAX_CHARS=512` → **226s/查询 → 15s/查询（≈15x；实测 26 条候选 226s）**；legal 侧无上限 | `memory_agent/eval/retrieval_baseline.md`、`AGENTS.md` |
| 质量基线（守门） | hybrid-rerank **nDCG@10=0.9658 / MRR=0.9778 / recall@1=0.8593 / 0 miss**；纯 fusion recall@1 仅 0.25 | `memory_agent/eval/retrieval_baseline.md` |

## 数据

### A. 外部技巧库：NirDiamant/RAG_Techniques

**性质：菜谱，非结论库。** 42+ notebook，每个 = 简介 + 代码 + 一条示例输出；**几乎无 benchmark / 无基线 / 无计时**。README 的 HyPE「+42pp」未在任何 notebook 复现（**unverified**）。

**可取（技巧）**：
- Reranking notebook 的便宜配方：cross-encoder 路径 `k=10 → rerank_top_k=5`（其 LLM-rerank 路径为 `k=15/30`）→ 佐证「**缩小重排池**」是最直接杠杆。其 `cross-encoder/ms-marco-MiniLM-L-6-v2` **仅英文**，中文不可用。
- Multi-faceted Filtering：相似度阈值 + 元数据 + 关键词 + MMR 去重，**零 LLM 调用**砍近重复/离题候选 → rerank 前的免费减池（阈值需按 BGE-M3 尺度重校）。
- Hierarchical Indices：先摘要后分块，结构性给池子设上界。

**不适用 / 不存在**：
- **无 RRF、无 RAG-Fusion**（`rag_fusion.ipynb` → 404 已核）。其 "Fusion Retrieval" 是加权 min-max 融合（非 RRF），无质量对比。
- Contextual Compression / LLM rerank 对延迟**反向**（每篇一次 LLM 调用），且无质量回报 → 不为延迟采用。
- RSE 唯一有实测（KITE 4.72→6.73），但**依赖 reranker**，自述成本/延迟与 top-k 相当 → 非提速方案。

**可借的评测口径**：UMBRELA（0–3 段落级）、GroUSE（judge 自校验 + positive_acceptance/negative_rejection）、**逐准则 pass/fail 而非平均**、CSV「每段落一行」导出。

### B. rerank 候选（数字均为作者自报，未本地复现）

| 候选 | 规模 / 语言 | 上限 | 许可 | 现成 ONNX | 备注 |
|---|---|---|---|---|---|
| `BAAI/bge-reranker-v2-m3`（现状） | 567.8M / 多语 | 8192 | apache-2.0 | ✗ | 重型 |
| `BAAI/bge-reranker-base` | 278M / zh+en | 512 | **MIT** | ✅ | 质量下限基准 |
| `Alibaba-NLP/gte-multilingual-reranker-base` | 306M / 70+ 语 | 8192 | apache-2.0 | ✗ | encoder-only；需 `trust_remote_code` |
| `jinaai/jina-reranker-v2-base-multilingual` | 278M / 多语(zh 好) | 1024 | **CC-BY-NC-4.0（禁商用）** | ✅ int8 | 体积/速度/中文最佳，**许可红线** |
| `mixedbread-ai/mxbai-rerank-base-v1` | 184M / **仅英文** | — | apache-2.0 | ✅(量化) | 最快；中文不可用，仅速度地板 |
| 现模型 ONNX/OpenVINO int8 | — | — | — | 需自导 | **CPU 上可能更慢**（待测） |
| mxbai-rerank-base-v2 / Qwen3-Reranker-0.6B | 0.5B/0.6B **decoder** | — | apache-2.0 | ✗ | CPU 大概率慢，不排期 |

**关键坑**：sbert 官方——CPU 上 fp16/bf16 ≈ **0.17x**（更慢）；ONNX/OpenVINO 在 CPU 可能**不如** PyTorch，**必须实测**；CrossEncoder 建议**不开** Flash-Attention。
**上限 API**：`CrossEncoder(model, max_length=N)`；v5.4+ 属性 `model.max_seq_length`（旧 `max_length` 弃用）；默认 fallback = tokenizer `model_max_length`。
**云 rerank**（Cohere / SiliconFlow）：网络 + 计费（Cohere >500 token 文档会切块各计一次）；**私有平面数据不能出网**（ADR-0015/0018）→ 仅云平面可用。

## 结论

1. **延迟三个杠杆 = ①送排长度上限 ②候选池 ③模型体量**。
2. **⚠️ 误判记录（2026-09-15，严重）**：本页初稿把 ① 当主杠杆——从 memory 的 **≈15x**（226s→15s，长条目 6000 字）**外推到 legal**。**错了**：length census 证明 memory 候选对 **max 381 token**、legal 块 **≤~820 token**，上限 8192/1024/512 在两套语料上**全是 no-op**（`maxlen_results.md` / ADR-0020）。**杠杆是语料相关的；先做分钟级 census 再排大评测。** 真正的主杠杆是 **②候选池**（20→10 近线性减半）。
3. **外部技巧库不能替代实测**：无 RRF/无数字；可搬的是「减池/过滤/上限」范式与评测口径。
4. **换模型有候选，但有许可（jina NC）与 ONNX-in-CPU 不确定两个坑**；decoder 类不为延迟排期。
5. **主杠杆 = 候选池，已离线量化（零重排）**：hybrid 池 ~26→**10 无损**（上限 0.8593 不变）、**12 安全默认**；预估省 **≈1.8–2.3 s/查询**。见 `pool_results.md`（尚缺裁池后的一次 rerank 实测）。
6. **守门红线**：任何改动 nDCG@10 明显低于 **0.9658** 即不上线——延迟不能靠掉质量换。

## 未决 / 计划（→ 子票）

- **票 A**：`RerankerService` 支持可配 `max_seq_length` + A/B `{8192,1024,512}`，记录延迟 + nDCG 守门 + legal 锚点判分分布。
  → **已完成（2026-09-16，`maxlen_results.md` + ADR-0020）**：三档对 memory 是严格 no-op（pair ≤381 token）；
  legal（块 ≤~820 token）1024/512 也不改排名、**无延迟收益**，256 才 ~1.4x。默认取 512 作兜底，延迟主杠杆改走候选池裁剪。
- **票 B**：模型横评（质量-延迟帕累托），含峰值 RSS、许可证、`trust_remote_code` 面；顺序 `bge-base → gte-multilingual → jina(int8, 若接受 NC) → mxbai(地板)`。
- ✅ **池 vs 召回曲线（已做，离线零重排）**：见 `pool_results.md` —— hybrid 池可从 ~26 裁到 **10（无损上限）/ 12（安全默认）**，预估省 ≈1.8–2.3 s/查询。**尚缺一次**：裁池后的 `hybrid-rerank` 实测（确认 nDCG@10 ≥ 0.9658）。
- 仍需本地测：截断对分数标定（`RELEVANCE_THRESHOLD=0.85`）的影响；ONNX 在本机 CPU 的真实加速比。

## 来源

- https://github.com/NirDiamant/RAG_Techniques （notebook 原文；`rag_fusion.ipynb` 404 已核）
- HF 模型卡：`BAAI/bge-reranker-v2-m3`、`BAAI/bge-reranker-base`、`Alibaba-NLP/gte-multilingual-reranker-base`、`jinaai/jina-reranker-v2-base-multilingual`、`mixedbread-ai/mxbai-rerank-base-v1`/`v2`、`Qwen/Qwen3-Reranker-0.6B`
- sbert 效率文档：https://sbert.net/docs/cross_encoder/usage/efficiency.html ；ONNX Runtime 量化文档
- 本仓：`experiments/e2e-latency/results.md`、`experiments/rerank-latency/results.md`、`memory_agent/eval/retrieval_baseline.md`
