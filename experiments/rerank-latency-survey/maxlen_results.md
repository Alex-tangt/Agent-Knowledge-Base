# #28 rerank 序列长度上限：A/B 结果与结论

> 日期 2026-09-16 · 对象 = `ragcore/services/reranker_service.py` 的 `CrossEncoder max_length`。
> 机器可验证据：`memory_census.json` / `memory_ab_512.json` / `legal_anchor.json`（本目录）。
> 上游输入：`README.md`（调研：票 A）、`experiments/e2e-latency/results.md`、`experiments/rerank-latency/results.md`。

## 问题

legal 侧 reranker 未设 `max_length` → 吃模型默认 `max_seq_length=8192`。本票给
`RerankerService` 加**可配 token 上限**，在 `#24` harness 上 A/B `{8192, 1024, 512}`，
选一个不掉质量的默认值（守门 nDCG@10 ≥ 0.9658）。

## 度量口径（动手前先定）

`#24` harness 原只记整轮 `elapsed_s`。本次：
1. 给 `memory_agent/eval/retrieval_eval.py` 加**逐题计时**（`elapsed_s` 入 trace 记录，
   汇总 mean/p50/p95/max，口径同 `experiments/rerank-latency/results.md`）；
2. 加**预热**（正式计时前先跑一次检索），把模型加载成本排除在逐题计时外；
3. legal 侧新增 `bench_rerank_maxlen.py legal-anchor`：在**真实候选池**上逐档测延迟 +
   判分漂移 + 排名漂移，不调 LLM、不起后端。

`run_hash` 只覆盖 `aggregate/per_query/no_answer`，计时字段不入 hash —— 确定性不受影响。

## 关键前提：token 上限只在 pair 超限时才截断

sbert 5.4.1 `CrossEncoder.predict` 先按输入长度排序、再按 batch 内最长对动态 padding
（`model.py:671`）。所以 `max_length` **不是**恒定 pad 到 8192，只在 query+doc 真的超过
该上限时截断。→ 短文本场景（memory 侧另有字符截断）里 token 上限可以是 no-op。

## 结果

### 1) memory 侧：{8192,1024,512} 是**严格 no-op**

`memory-census`（51 query、1119 个 query→doc 对，复刻 `RETRIEVAL_POOL=20` 候选 + `MEMORY_RERANK_MAX_CHARS=512` 字符截断）：

| 指标 | 值 |
|---|---|
| max pair tokens | **381** |
| 超过 8192 / 1024 / 512 的对数 | **0 / 0 / 0** |
| 超过 256 的对数 | 759 |

三档上限都不会截断任何一对 → 输入逐字节相同 → 排名与延迟**按构造相同**。

实测（`RERANK_MAX_SEQ_LENGTH=512` 跑满 51 题）：

| 档 | nDCG@10 | MRR | recall@1 | miss | run_hash | latency mean/p50/p95 (s) |
|---|---|---|---|---|---|---|
| 8192（基线） | 0.9658 | 0.9778 | 0.8593 | 0/45 | `d9d2311f2342a7b8` | 旧 harness 无逐题计时 |
| **512（实测）** | **0.9658** | **0.9778** | **0.8593** | **0/45** | **`d9d2311f2342a7b8`** | 17.71 / 18.15 / 21.12 |

`run_hash` 与基线**逐位一致** → 512 档排名/指标与 8192 完全相同。**守门通过。**

### 2) legal 侧：真实候选池 20 题（`legal-anchor`）

| cap | latency mean | p50 | p95 | min_dist mean | 判拒数(>0.85) | top-8 重合 | Kendall tau |
|---|---|---|---|---|---|---|---|
| 8192（基线） | 11.55 | 11.61 | 18.76 | 0.2316 | 3 | — | — |
| 1024 | 11.61 | 11.53 | 18.97 | 0.2316 | 3 | **1.000** | **1.000** |
| 512 | 12.33 | 12.72 | 19.06 | 0.2316 | 3 | **1.000** | **1.000** |
| 256 | **8.36** | 8.53 | 10.36 | 0.2535 | 3 | 0.994 | 0.993 |

- **1024 对 legal 是严格 no-op**：法条块最长 ~797 字 ≈ 820 token < 1024 → 三档分数/排名
  逐位相同。
- **512 也不掉质量**（分数/排名与 8192 逐位一致），**但延迟没有收益**（12.33 vs 11.55，
  在噪声内）——legal pair 本就 ≤ ~820 token，512 只削最长那几块的尾部，改动太小。
- **256 才有 ~1.4x 收益**，代价是轻微的排名漂移（top-8 重合 0.994 / tau 0.993）和分数
  标定移动（min_dist 0.2316 → 0.2535）；本样本判拒数不变（3）。

> 注：`max_length` 只截断**送进 reranker 的** query+doc，**不截断**回给 LLM 的检索正文
> （`rag_service._rerank` 仍返回完整 chunk）。所以这里的风险仅限于**重排排序**，不含
> 生成上下文缺失。

### 3) 旁证：只取最长法条块（临时探针，未入脚本）

pool=24 取最长块时：8192→19.84s、1024→19.38s、512→18.84s、256→9.57s。
即便全是长块，512 也只 ~5%，256 才翻倍——与上表同结论。

## 结论

1. **选定默认 `RERANK_MAX_SEQ_LENGTH=512`**（core 层，`ragcore/config/config.py`）：
   在两套语料上都**有实测零排名变化**，且对超过 512 token 的输入设了兜底上限；
   env 设 `none/off/0/空串` 即不设上限、一键回退旧行为。
2. **本票没有拿到预期的大延迟收益**：A/B 三档里真正 binding 的是 legal 的 ~820 token，
   1024/512 都不改变结果。**要降 legal rerank 延迟，靠 token 上限得下到 ~256**（~1.4x，
   有轻微排序漂移），或走候选池裁剪（`experiments/rerank-latency/results.md`：池 20→10
   近线性减半）。这与调研「三个杠杆 = 送排长度 / 候选池 / 模型体量，①②便宜且有天花板」
   一致——本案 ① 的天花板比预期低。
3. 决策落点见 `docs/adr/0020`（proposed）。

## 复现

```powershell
chcp 65001 > $null; $env:PYTHONIOENCODING="utf-8"; $env:HF_HUB_OFFLINE="1"
$MAIN = "D:\python_work\work2026-4\Agent-Knowledge-Base"

# (1) memory token 普查（需 #24 索引，只读）
$env:MEMORY_INDEX_DIR="$MAIN\memory_agent\vector_db"
$env:MEMORY_READONLY_REPOS_CONFIG="$MAIN\memory_agent\readonly_repos.json"
venv\Scripts\python.exe experiments/rerank-latency-survey/bench_rerank_maxlen.py `
  memory-census --out experiments/rerank-latency-survey/memory_census.json

# (2) memory harness 跑 512（~15 min）
$env:RERANK_MAX_SEQ_LENGTH="512"
venv\Scripts\python.exe memory_agent/eval/retrieval_eval.py --mode hybrid-rerank `
  --trace experiments/rerank-latency-survey/trace_mem_512.jsonl `
  --out experiments/rerank-latency-survey/memory_ab_512.json

# (3) legal 锚点（需 legal_web 向量库，不调 LLM，~20 min）
$env:VECTOR_DB_PATH="$MAIN\legal_web\vector_db"
venv\Scripts\python.exe experiments/rerank-latency-survey/bench_rerank_maxlen.py `
  legal-anchor --n 20 --out experiments/rerank-latency-survey/legal_anchor.json
```

## 局限

- 平台：Windows / CPU；legal 锚点单轮，±6% 内的差异不显著。
- legal 锚点用原始问题直接检索+重排（不跑 query rewrite LLM），只做**相对**比较。
- legal 无检索标注集，用「top-8 重合 / Kendall tau / 判拒数」而非 nDCG 判质量。
- memory 评测集偏易（query 由来源条目生成），适合相对比较（见 `retrieval_baseline.md` 局限）。
