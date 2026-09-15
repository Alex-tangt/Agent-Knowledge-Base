# #29 rerank 模型横评：质量-延迟帕累托

> 日期 2026-09-15 · worktree `wk-29` / 分支 `feat/29-reranker-benchmark`
> 对象 = **重排调用**（不含 embedding / 向量检索 / 端到端），不调 LLM。
> 问题：CPU 上 rerank 主导 e2e 延迟（~83%）。**换更小的 cross-encoder 能否不掉质量地降耗？**

## 结论（TL;DR）

1. **唯一「质量守门 + 显著提速」的候选是 `jina-reranker-v2` int8 ONNX**：12 题子集上 recall@1/MRR/ 与对照 m3 **逐位持平**（0.9375 / 1.0000），nDCG@10 略高（0.9837 vs 0.9795）；重排延迟 **2366ms vs 11313ms（4.8x）**，`s/pair` 0.149（m3 0.713）。**代价：许可 CC-BY-NC-4.0（禁商用）**。
2. **许可干净（Apache/MIT）的候选全部过不了质量闸门**（ADR-0021 主指标 recall@1/MRR）：
   `bge-base`（MIT，3.4x）recall@1 **−16.7pp**、`gte-multilingual`（Apache，2.6x）**−8.3pp**、`mxbai`（Apache，1.7x，且**仅英文**）**−8.3pp**。
3. **商用 / 许可干净场景的推荐 = 不换模型，走「只调参」**：保留 m3，延迟主杠杆用**候选池裁剪**（ADR-0020/0022：池 20→14 已省 ~32%）+ 送排 `max_seq_length=512` 兜底。**没有一个 Apache/MIT 模型能在守门的同时提速。**
4. **非商用场景**（本项目 demo / 研究）：换 `jina-v2` int8 ONNX，是当前帕累托前沿；若接受 transformers 降级，torch fp32 版质量同样持平但慢 1.5x。
5. legal 锚点显示所有候选与 m3 的 top-8 重合只有 **0.60–0.75**（Kendall tau 0.49–0.74）→ **法律链路对换模型敏感**，但 legal 无金标，不能据此判优劣（见「局限」）。

## 方法

- **候选池离线缓存一次**（`memory-pools` / `legal-pools` 各起一次进程、各起一份 BGE-M3），之后按模型评测**只加载候选重排器**——既把「模型」变量与「检索/嵌入」隔离，又压低了峰值内存（同一时刻只有重排器 + 缓存，不再叠加 BGE-M3）。
- **质量**：`#24` 确定性评测集（记忆检索，条目级二值；`memory_agent/eval/metrics.py`），固定「同一 query 集 + 同一候选池 + 同一送排文本」，逐模型只换重排器。
- **延迟**：`latency` 子命令——**同场、交错、重复**（round → model → query；只计时 `predict` 调用），沿用 `experiments/rerank-latency-survey/pool_latency_bench.md` 的做法。
- **锚点验证**：缓存池重建必须与真实链路逐位一致——对同一 12 题，harness 的 m3 排名 vs 独立 `retrieval_eval.py --mode hybrid-rerank` 排名 **0 处不一致**；全 51 题真实链路结果见 `cache/anchor_retrieval_eval_m3.json`（`run_hash=dbfd570be1e65521`，recall@1 0.8815 / nDCG@10 0.9709 / MRR 0.9889）。
- **规模**：记忆 12 题（用户拍板缩量；全量 51 题锚点已给作尺度参照）、legal 12 题；索引 gen-2、`RETRIEVAL_POOL=14`、`RERANK_MAX_SEQ_LENGTH=512`。

## 数据

### 记忆检索质量（前 12 题，同一候选池 15.9 对/题）

| 模型 | 许可 | recall@1 | MRR | nDCG@10 | miss | Δrecall@1 vs m3 |
|---|---|---|---|---|---|---|
| `bge-v2-m3`（对照） | apache-2.0 | 0.9375 | 1.0000 | 0.9795 | 0 | — |
| `jina-v2-onnx-int8` | CC-BY-NC-4.0 | 0.9375 | 1.0000 | 0.9837 | 0 | 0.0 |
| `jina-v2-torch` | CC-BY-NC-4.0 | 0.9375 | 1.0000 | 0.9813 | 0 | 0.0 |
| `mxbai-v1` | apache-2.0 | 0.8542 | 0.9583 | 0.9530 | 0 | −8.3pp |
| `gte-multilingual` | apache-2.0 | 0.8542 | 0.9444 | 0.9300 | 0 | −8.3pp |
| `bge-base` | MIT | 0.7708 | 0.8750 | 0.8773 | 0 | −16.7pp |

> 全 51 题 m3 真实链路：recall@1 **0.8815** / nDCG@10 **0.9709** / MRR **0.9889**（见锚点文件）；子集值为 0.9375/0.9795/1.0，只作**相对**比较。

### 重排延迟（记忆，交错 8 题 × 3 轮，15.9 对/调用）

| 模型 | mean ms | p50 | p95 | sd | s/pair | 加速 vs m3 |
|---|---|---|---|---|---|---|
| `bge-v2-m3`（对照） | 11312.5 | 11116.1 | 12919.5 | 1163.5 | 0.713 | 1.0x |
| `mxbai-v1` | 6603.9 | 6508.6 | 7877.6 | 903.7 | 0.416 | 1.7x |
| `gte-multilingual` | 4364.9 | 4359.6 | 5230.5 | 484.9 | 0.275 | 2.6x |
| `jina-v2-torch` | 3558.6 | 3560.6 | 4248.2 | 412.8 | 0.224 | 3.2x |
| `bge-base` | 3286.6 | 3225.3 | 3946.3 | 371.2 | 0.207 | 3.4x |
| `jina-v2-onnx-int8` | 2366.1 | 2277.5 | 2847.3 | 302.1 | 0.149 | **4.8x** |

### 模型事实

| 模型 | dtype（强制） | config dtype | 参数量 | token ctx | `trust_remote_code` | 加载 s | 内存增量 GB | token max/mean（记忆） |
|---|---|---|---|---|---|---|---|---|
| `bge-v2-m3` | fp32 | fp32 | 567.8M | 8194 | 否 | 21.5 | 0.62 | 361 / 283 |
| `bge-base` | fp32 | fp32 | 278M | 514 | 否 | 4.1 | 0.27 | 361 / 283 |
| `gte-multilingual` | fp32 | **fp16** | 306M | 8192 | **是** | 4.0 | 1.53 | 361 / 283 |
| `jina-v2-torch` | fp32 | **bf16** | 278M | 1026 | **是** | 4.2 | 1.38 | 361 / 283 |
| `jina-v2-onnx-int8` | int8 ONNX | — | 278M | 1026 | **是** | 5.2 | 0.58 | 361 / 283 |
| `mxbai-v1` | fp32 | **fp16** | 184M | 512 | 否 | 3.8 | 0.84 | 454 / 322 |

- **6 个模型全部载入同进程的峰值 RSS = 7.34 GB**（含 torch 运行时）；单候选增量 0.27–1.53 GB，**不会撑爆 commit**（本机 29.7GB）。
- 记忆候选对 max **361 token**（mxbai 454）→ `max_seq_length=512` 对全部模型 **不 binding**（over-512 = 0）。

### legal 锚点（前 12 题；无金标，只看与 m3 的一致度）

| 模型 | 许可 | top-8 重合 | Kendall tau | refuse | mean ms |
|---|---|---|---|---|---|
| `bge-v2-m3` | apache-2.0 | 1.0000 | 1.0000 | 0 | 13192.8 |
| `bge-base` | MIT | 0.7500 | 0.5734 | 0 | 4137.9 |
| `jina-v2-torch` | CC-BY-NC-4.0 | 0.6979 | 0.7397 | 0 | 4403.2 |
| `jina-v2-onnx-int8` | CC-BY-NC-4.0 | 0.6771 | 0.7389 | 0 | 3002.2 |
| `gte-multilingual` | apache-2.0 | 0.6562 | 0.6278 | 0 | 5523.7 |
| `mxbai-v1` | apache-2.0 | 0.6042 | 0.4944 | 1 | 8264.9 |

> legal 延迟为**单趟**（池按题号 ~16–26 条差异大，m3 p95 达 21.7s），非交错口径；模型间相对快慢与记忆交错口径一致。

## 帕累托与推荐

```
质量(recall@1) ↑
0.94 |  ● m3(11.3s)   ● jina-torch(3.6s)  ● jina-int8(2.4s)   ← 前沿
0.85 |                         ● gte(4.4s)      ● mxbai(6.6s)
0.77 |                 ● bge-base(3.3s)
     +----------------------------------------------------→ 延迟
```

- **推荐 A（非商用 / 本项目 demo）**：换 **`jina-v2` int8 ONNX**。质量持平、4.8x、显存 0.58GB。风险：CC-BY-NC 禁商用；sbert onnx backend 需要 `optimum`（会牵动 transformers 版本，见下）。
- **推荐 B（商用 / 许可干净）**：**保留 m3，不换模型**；降延迟走**候选池裁剪**（ADR-0022 已把默认池降到 14，省 ~32%）与 `RERANK_MAX_SEQ_LENGTH=512` 兜底。理由：能提速的 Apache/MIT 模型全部掉 recall@1 8–17pp，违反守门（ADR-0021）。
- **推荐 C（许可干净 + 必须提速 + 容忍降级）**：`bge-base`（MIT，3.4x）作下限；须接受 recall@1 −16.7pp，**默认不建议**。

**「换模型 vs 只调参」的取舍**：换模型只有 jina（NC）同时过闸门+提速；在许可干净前提下，**只调参（裁池）严格优于换模型**。

## 关键坑 / 发现

- **jina-v2 torch 在 transformers 5.x 装不上**：其自定义 `modeling_xlm_roberta.py` 依赖 `einops`，且 `embedding.py` 从 `transformers.models.xlm_roberta` 里 import `create_position_ids_from_input_ids`——该符号在 transformers 5.x 已被移除。**要用 jina 走 int8 ONNX 路径**（不触发自定义 modeling，且恰好最快）。
- **ONNX 路线牵动版本**：sbert 的 onnx backend 需要 `optimum`；`pip install optimum[onnxruntime]` 把 `transformers` 5.5.4→4.57.6、`huggingface_hub`→0.36.2。**本次实验环境的副作用，实验后已还原**（见「环境」）。
- **强制 fp32 是公平比较的前提**：gte/jina/mxbai 的 config 声明 fp16/bf16，CPU 上 fp16/bf16 ≈0.17x（`rerank-latency-survey/README.md`）。注册表统一 `torch_dtype=float32`。
- **子集质量**：recall@1/MRR 在 12 题上与 m3 的差异足够区分三档（持平 / −8pp / −17pp），但 nDCG@10 分辨率有限（子集 0.98 量级）。

## 局限

- 质量只跑**前 12 题**（按用户要求缩量）；全 51 题只对 m3 有真实链路锚点。子集偏向 q001–q012（Python/MCP/agent-infra 族），**只能相对比较**。
- legal 锚点**无金标**：只能测「排名是否像 m3」，不能证明候选更好；且它显示 legal 排名对模型敏感（重合 0.60–0.75），换模型前必须在 legal 上另建带标注的锚点。
- legal 延迟为单趟，非交错重复。
- 单机 CPU、单次会话；p95 受机器漂移影响（交错设计已尽量抵消）。

## 复现

```powershell
# 0) 候选权重（HF 缓存 + jina 的 einops/optimum）
venv\Scripts\python.exe experiments/rerank-model-survey/bench_rerank_models.py fetch
# 1) 候选池缓存（各起一份 BGE-M3；指到主树索引/向量库）
$env:MEMORY_INDEX_DIR="<主树>\memory_agent\vector_db"
$env:VECTOR_DB_PATH="<主树>\legal_web\vector_db"
venv\Scripts\python.exe experiments/rerank-model-survey/bench_rerank_models.py memory-pools
venv\Scripts\python.exe experiments/rerank-model-survey/bench_rerank_models.py legal-pools
# 2) 质量 + 交错延迟（只加载候选重排器，不再起 BGE-M3）
venv\Scripts\python.exe experiments/rerank-model-survey/bench_rerank_models.py memory --limit 12 --rounds 1 --out experiments/rerank-model-survey/memory_results_limit12.json
venv\Scripts\python.exe experiments/rerank-model-survey/bench_rerank_models.py latency --corpus memory --n-queries 8 --rounds 3 --out experiments/rerank-model-survey/latency_memory.json
venv\Scripts\python.exe experiments/rerank-model-survey/bench_rerank_models.py legal --limit 12 --rounds 1 --out experiments/rerank-model-survey/legal_results_limit12.json
# 3) 汇总表
venv\Scripts\python.exe experiments/rerank-model-survey/analyze.py
```

独立锚点（真实链路，#24 harness）：

```powershell
$env:MEMORY_INDEX_DIR="<主树>\memory_agent\vector_db"
venv\Scripts\python.exe memory_agent/eval/retrieval_eval.py --mode hybrid-rerank `
  --out experiments/rerank-model-survey/cache/anchor_retrieval_eval_m3.json
# -> run_hash=dbfd570be1e65521，recall@1 0.8815 / nDCG@10 0.9709 / MRR 0.9889
```

## 环境

- **不新增/修改任何非 `experiments/` 文件**；换模型只走 `models.json` + `CrossEncoder` 构造参数（`MEMORY_RERANK_MODEL` 亦可用于生产链路）。
- 实验期临时安装 `einops`、`optimum[onnxruntime]`（jina 依赖）；后者把 `transformers` 降到 4.57.6。**实验后已还原 `transformers==5.5.4`**。

Relates: #29、#21、#24、#28、ADR-0020/0021/0022；生产默认（rerank 关 + 池 14）见 ADR-0022。
