# k census（#21）：返回条数 k 的收益曲线（默认链路 vs rerank）

> 2026-09-16 · 分支 `feat/21-k-census` · worktree `wk-kc`
> 对象 = **记忆检索**的**消费者口径返回条数 k**（`memory_search(query, k=?)`）。
> **纯离线**：只用已冻结的逐题排名（池 14 的完整降序 top-10），不加载任何模型。
> 脚本 `census.py`；证据 `census.json`。

## 问题 → 假设

- **问题**：`memory_search` 默认 **k=5**。ADR-0022 #29 追加的后果节把「先动 **k**（返回条数）」
  列为优先级最高。k 是**免费杠杆**（只加 LLM 上下文，不加检索/重排延迟）还是不值得？
- **假设**：默认链路（rerank 关）的 recall 在 k>5 仍显著上升 → 有免费的「多返回几条」空间。

## 方法

- 数据源：三份已冻结的逐题排名（`experiments/rerank-jina-35/{retriever_only,jina_onnx,m3_torch}.json`），
  同 gen-2（134 条）、同池 14、同题集（51 题 = 45 有答案 + 6 无答案）。
- 对每个 k = 1..10 计算 `recall@k`（有答案题）/ `hits@k` / `precision@k`，以及
  `marginal@k = recall@k − recall@k−1`（每多返回一条的边际收益）。

## 数据（recall@k）

| 链路 | r@1 | r@2 | r@3 | r@4 | r@5 | r@6 | r@7 | r@8 | r@9 | r@10 |
|---|---|---|---|---|---|---|---|---|---|---|
| **默认（dense+关键词，rerank 关）** | 0.707 | 0.844 | 0.869 | 0.913 | **0.918** | 0.952 | 0.952 | **0.974** | 0.974 | 0.974 |
| rerank=jina int8 | 0.832 | 0.900 | 0.933 | 0.956 | **0.969** | 0.969 | 0.974 | **0.980** | 0.980 | 0.980 |
| rerank=m3 | 0.881 | 0.933 | 0.944 | 0.969 | **0.969** | 0.974 | 0.974 | **0.980** | 0.980 | 0.980 |

边际收益（默认链路）：`m@2 +0.137`、`m@4 +0.044`、`m@6 +0.033`、`m@8 +0.022`，**k≥8 后为 0**。
precision@k（默认）：0.800@1 → 0.227@5 → **0.153@8** → 0.122@10。

## 结论

1. **k 是免费杠杆，且默认链路在 k=8 饱和**：默认链路 recall 从 k=5 的 **0.918** 升到 k=8 的
   **0.974（+5.6pp ≈ 2.5 题）**；k≥8 再无收益。rerank 链路在 **k=5 已 0.969**、k=8 封顶 0.980。
2. **「k=8 不重排」≈「k=5 + m3 重排」**：默认@8 = **0.974** > m3@5 = 0.969，且**零重排延迟**
   （代价仅每条多 ~240 字 snippet × 3 条上下文）。
3. **k=5 默认留了 +5.6pp 在桌上**；但 k>8 是纯成本无收益（precision 只有 0.12–0.15，
   返回的大部分不是 gold）。**建议把默认 k 从 5 提到 8**——属默认链路变更，**待 owner 批**。
4. rerank 的收益在**排序**（把 gold 提到更前），k 的收益在**召回**（把 gold 纳入返回集）；
   两条路径不可互相替代，但按**消费者口径（gold 在不在读到的集合里）**，
   **先调 k 比开 rerank 更划算**（零延迟、免 NC 许可）。

## 复现

```powershell
chcp 65001 > $null; $env:PYTHONIOENCODING="utf-8"
venv\Scripts\python.exe experiments/k-census/census.py `
  --report experiments/rerank-jina-35/retriever_only.json `
  --report experiments/rerank-jina-35/jina_onnx.json `
  --report experiments/rerank-jina-35/m3_torch.json `
  --out experiments/k-census/census.json
```

## 局限

- 排名来自池 14 的 top-10；k>10 不可测（评测集 harness 的 `top_k=10`）。
- 45 有答案题，+5.6pp ≈ 2.5 题 → 方向性明确，绝对值不宜过度解读。
- 语料固定 gen-2（134 条）；增长后需重跑。
- 未量化 LLM 侧上下文成本（snippet 每条 ~240 字），只按条数近似。

Relates: #21、#24、ADR-0022 #29 追加（后果节点名「先动 k」）、ADR-0021、
`experiments/{rerank-jina-35,rerank-model-survey,local-lexical-40}/`。
