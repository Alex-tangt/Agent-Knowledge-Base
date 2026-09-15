# 记忆检索基线（#24）— recall@k / nDCG@10 / MRR

> 对象 = **记忆检索**（不是法律 RAG）。目标链路 = 向量 + 关键词 + rerank。
> 确定性 harness，不调 LLM。逐题明细见 `retrieval_baseline.json` / `retrieval_ablation_*.json`。

## 结论

1. **目标链路（hybrid + rerank）最好**：nDCG@10=0.9658、MRR=0.9778、recall@1=0.8593、recall@10=0.9852，无 miss。
2. **关键词批次排在向量之前（ragcore legal 式融合）对记忆检索有害**：纯 hybrid 的 recall@1 从
   vector 的 0.6407 掉到 0.25、nDCG@10 从 0.8524 掉到 0.5739。**rerank 能把候选池救回来**，
   但融合本身是 #21 的明确优化点（把关键词命中无条件顶到向量之上会挤掉真正相关的向量命中）。
3. **rerank 成本被默认 `max_seq_length=8192` 放大**：整条 6000 字条目 ≈ 3.5 分钟/查询
   （实测 26 条 226s）；把送排正文截到 512 字后 ≈ 15 秒/查询。故新增 `MEMORY_RERANK_MAX_CHARS=512`。
4. **无答案 query 在 rerank 模式下 top-1 logit ≈ 0**（mean 0.0046 / max 0.0259）——只作描述性
   信号；是否拒答由生成层决定（ADR-0017），**不在此校准阈值**。

| 模式 | recall@1 | recall@3 | recall@5 | recall@10 | nDCG@10 | MRR | misses |
|---|---|---|---|---|---|---|---|
| **hybrid-rerank（基线）** | **0.8593** | **0.9444** | **0.9685** | **0.9852** | **0.9658** | **0.9778** | **0/45** |
| vector（纯向量） | 0.6407 | 0.8685 | 0.9185 | 0.9741 | 0.8524 | 0.8136 | 0/45 |
| hybrid（关键词优先，无 rerank） | 0.2500 | 0.5000 | 0.5556 | 0.9333 | 0.5739 | 0.4917 | 1/45 |

## 设置

- 索引：**gen-2**，134 条，`built_at=2026-09-14T17:46:16Z`（`memory_agent/vector_db`）。
  - 复跑时用 `MEMORY_INDEX_DIR` 指向该索引目录（本仓库默认；本次复用主树索引，只读）。
  - `AGENT_KB_DIR` = 全局 KB；只读语料 = 3 个仓库（`memory_agent/readonly_repos.json`）。
  - 语料现为 138 条（rebase 后新增 ADR-0016/0017 等）；评测集只覆盖 gen-2 的条目。
- 评测集：`retrieval_eval_set.json`（51 条 = 45 有答案 + 6 无答案；标注口径见 `README.md`）。
- 检索：`RETRIEVAL_POOL=20`；embedding `BAAI/bge-m3`；reranker `BAAI/bge-reranker-v2-m3`。
- rerank 正文截断：`MEMORY_RERANK_MAX_CHARS=512`（见结论 3）。
- 环境：Windows / CPU；两个模型进程内常驻。

## 复现

```powershell
chcp 65001 > $null; $env:PYTHONIOENCODING="utf-8"
$env:MEMORY_INDEX_DIR="<index root>\memory_agent\vector_db"   # 指到 gen-2 所在根
venv\Scripts\python.exe memory_agent/eval/retrieval_eval.py --mode hybrid-rerank `
  --out memory_agent/eval/retrieval_baseline.json
```

## 确定性

同一模式**独立跑两次**，`meta.run_hash` 相同：

- hybrid-rerank：`d9d2311f2342a7b8`（第一次 `elapsed=0.0s` 为复用 trace 重算指标；第二次全新跑
  `elapsed=879.69s`，`run_hash` 一致）。rankings 与标签无关；trace 复用只复用排名，标签取当前评测集。
- vector：`3b5cd4292c0ac02a`；hybrid：`794f0d4bf686e454`。

## 标注复核

baseline 里来源条目非 top-1 的 8 条（q010/016/020/030/031/032/035/041）逐条核对内容后补入
共相关条目（跨仓库同文 / 同主题），见评测集各条 `label_review`；q042 判定为真 miss。
**人工抽检待用户确认**（见 `README.md`）。

## 局限

- query 由来源条目生成，偏易（词面重叠），不是真实用户分布；适合做**相对**比较（优化前后 / 模式消融），不适合当绝对质量。
- ground truth 为条目级二值；只读语料里存在跨仓库同文（同一 `issue-tracker.md` 三份），已按共相关补标。
- 固定 gen-2；语料增长后需重建索引并重跑，另存新基线。
