# RAG 评测结果（索引）

> 本文件原为 `run_eval.py`（RAG vs LLM-only 原始对比）的输出。
> **当前权威评测报告为 `results_scored.md`**——它已在一份文档中整合了：
> 评分汇总（忠实度 / 相关性 / 上下文精确率 / 上下文召回 / 正确性）、
> RAG 与 LLM-only 对比概览、逐题评分表，以及**每题的 RAG 回答、检索来源、
> LLM-only 回答与裁判说明**。

## 文件清单
- `results_scored.md` —— 主评测报告（53 题，含 RAG 回答 + LLM-only 回答 + 裁判打分）。
- `failure_analysis.md` —— 失败案例分析（9 例）+ 总体改进方向。
- `questions.md` —— 评测问题集（25 事实型 / 16 综合型 / 12 无答案型）。
- `ground_truth.md` —— 事实型/无答案型标准答案与预期来源。
- `score_eval.py` —— 自动化评测脚本（LLM-as-judge，单次运行产出上述报告）。
- `run_eval.py` —— 轻量 RAG vs LLM-only 原始对比脚本（如需单独重跑原始对比可用）。

> 如需重新生成原始对比，可运行 `python tests/run_eval.py`（需后端服务在 `http://127.0.0.1:8000` 运行）。
