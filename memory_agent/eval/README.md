# memory_agent/eval — 运行时证据与确定性评测基座

放 memory_agent 的**运行时证据**（sandbox、dogfood、验收）与**检索评测基座**（issue #24 / #21）。
纯单测在 `tests/unit/`；这里的东西需要真实模型 / 真实索引。

## 记忆检索确定性评测基座（#24）

**对象 = 记忆检索**（不是法律 RAG）。评测链路 = **目标链路**（向量 + 关键词 + rerank），
走 `MemoryIndex.search` → `MemoryRetriever` → `ragcore` 策略 + `RerankerService`。
**不调 LLM、可重复**（同一次运行两次得到相同 `run_hash`）。

| 文件 | 作用 |
|---|---|
| `retrieval_eval_set.json` | 固化评测集：`query → 相关条目 id`（条目级二值） |
| `build_eval_set.py` | 从当前索引语料确定性抽样 + LLM 出题的**候选**生成器（provenance） |
| `retrieval_eval.py` | harness：一条命令产出 recall@k / nDCG@10 / MRR |
| `metrics.py` | 纯函数指标（单测 `tests/unit/test_eval_metrics.py`） |
| `retrieval_baseline.md` | 基线数字 + 命令 + 结论（证据） |
| `retrieval_baseline.json` / `retrieval_ablation_*.json` | 各模式逐题明细 |

### 评测集

- 51 条：45 条有答案（来源条目 = 初始 ground truth）+ 6 条**无答案**（语料外）。
- 抽样覆盖可写 KB 与三个只读仓库（`agent-knowledge-base` / `agent-infra` / `kg-triplet-sft`）。
- **标注**：LLM 依据来源条目的标题+节选生成 query（`temperature=0.7`），经 **agent 复核**——
  对 baseline 里来源非 top-1 的 8 条逐条核对内容，补入真正能回答的**共相关条目**
  （跨仓库同文如 `issue-tracker.md`/`triage-labels.md`、同主题如 ADR-0013 ↔ 可写 KB 条目），
  记录在每条 query 的 `label_review`；q042 判定为真 miss 不补。
- **人工抽检**：2026-09-15 用户确认上述 8 条补标注（q042 保持真 miss）→ 精标完成。
- 固定到索引 **gen-2**（134 条，2026-09-14 构建）。语料其后新增了若干 ADR；重建为 gen-3 会让
  语料变多、分数可能微移，需在本目录留新一版基线。

### 无答案 query 的口径

**只做描述性观察**（报告 top-1 分数分布），**不做阈值校准**——生成层决定去强制拒答、
由 LLM 在对话里说明「知识库中无相关内容」，见 `docs/adr/0017`。各模式 score 空间不同
（vector=余弦、hybrid 关键词批次 >1、rerank=交叉编码器 logit），**只在同模式内可比**。

### 跑

```powershell
# 目标链路（基线；需 BGE-M3 + reranker，CPU ~15 分钟）
venv\Scripts\python.exe memory_agent/eval/retrieval_eval.py --mode hybrid-rerank `
  --out memory_agent/eval/retrieval_baseline.json

# 消融（快，仅 BGE-M3）
venv\Scripts\python.exe memory_agent/eval/retrieval_eval.py --mode vector
venv\Scripts\python.exe memory_agent/eval/retrieval_eval.py --mode hybrid

# 长跑可断点续跑：--trace <jsonl>，重拉即从已完成的 query 续
# 冒烟：--limit N
```

`--out` 里的 `meta.run_hash` 是逐题结果的 sha256 截断；**两次运行同 hash = 确定性成立**。
