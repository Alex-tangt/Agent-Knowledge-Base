# 查询改写优化器实验（Query Rewriting Optimizer）

**问题**：查询改写能否改善检索距离（`best_dist`）而不丢失用户意图？

**设置**：三个 LLM 上下文完全隔离——改写 LLM（无状态，出关键词短语/子查询）、意图判断 LLM（无状态，0.0~1.0 连续滑移分）、优化 LLM（唯一有会话历史，每轮产出 `{analysis, new_prompt}`）。dev set 按 `best_dist_orig` 降序筛选（优先选原检索差的 query）；test set 不参与优化轮次。

**数据**：`eval_result.json`（逐 query 的 best_dist / norm_delta / intent_drift）、`eval_progress.json`（轮次进度）、`dev_set_candidates.json`（候选池）。

**结论**：
- 核心发现：**二元 intent_drift 与关键词改写结构性冲突**——16 条 query 中 15/16 被判 drift=1。改为 0.0~1.0 连续打分，权重从 0.3 降至 0.1（CONTEXT.md 决策 #9）。
- 完整 9 条决策见 `CONTEXT.md`「查询改写优化器实验」；报告见 `docs/optimizer_experiment_report.md`、`docs/optimizer_summary_table.md`、`docs/exp2-review.md`。

**状态**：已封存（ADR-0004：实验未决 → 记录结论，不再调优）。

**补充发现（2026-08 e2e-latency 调查）**：本实验的评测工具 `eval_service.py` 存在两处问题，且是用户曾观察到的"单次检索 2-3 分钟"的来源：
1. **对子查询重排（设计错误）**：`_retrieve_multi` 对每个分解后的子查询 `sq` 调用 `_rerank(sq, results)`——用子查询而非原始 query 重排，丢失原始约束，排序语义错误。
2. **候选爆炸**：`pool_size` 默认 100 × 多路改写（≤5 子查询）→ 数百对 cross-encoder 在 CPU 上 = 分钟级。
生产路径（`rag_service.rag_chat_stream`）不受影响：单改写 + 池 ≤32 + 对原始 query 重排（正确）。本实验已封存，不修复；记录备查。
