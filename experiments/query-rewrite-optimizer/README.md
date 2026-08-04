# 查询改写优化器实验（Query Rewriting Optimizer）

**问题**：查询改写能否改善检索距离（`best_dist`）而不丢失用户意图？

**设置**：三个 LLM 上下文完全隔离——改写 LLM（无状态，出关键词短语/子查询）、意图判断 LLM（无状态，0.0~1.0 连续滑移分）、优化 LLM（唯一有会话历史，每轮产出 `{analysis, new_prompt}`）。dev set 按 `best_dist_orig` 降序筛选（优先选原检索差的 query）；test set 不参与优化轮次。

**数据**：`eval_result.json`（逐 query 的 best_dist / norm_delta / intent_drift）、`eval_progress.json`（轮次进度）、`dev_set_candidates.json`（候选池）。

**结论**：
- 核心发现：**二元 intent_drift 与关键词改写结构性冲突**——16 条 query 中 15/16 被判 drift=1。改为 0.0~1.0 连续打分，权重从 0.3 降至 0.1（CONTEXT.md 决策 #9）。
- 完整 9 条决策见 `CONTEXT.md`「查询改写优化器实验」；报告见 `Docs/optimizer_experiment_report.md`、`Docs/optimizer_summary_table.md`、`Docs/exp2-review.md`。

**状态**：已封存（ADR-0004：实验未决 → 记录结论，不再调优）。
