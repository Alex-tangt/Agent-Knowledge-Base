# 0003 评估：单一路径 LLM-as-judge，砍 RAGAS 与脚手架

Status: accepted

评估收敛为 canonical 四件套：`tests/questions.md`、`tests/ground_truth.md`、`tests/run_eval.py`、`tests/score_eval.py`。打分采用单次调用的多维 LLM-as-judge（忠实度/相关性/上下文精确率/召回/正确性）+ 两个确定性硬指标（`source_recall`、拒答率）。删除废弃尝试（`tests/golden_qa/`、`tests/tech_docs/`、`gen_tech_qa.py`、RAGAS 脚手架）与迭代残留结果文件。

理由：RAGAS 每指标多次调用 DeepSeek 会超时（>5min/题），中文法律域质量一般；简历需要可复现证据与诚实的边界，不是指标广度。`tests/score_eval.py` 头部已记录该权衡。

Consequences: 唯一证据产物为一份 `results_scored.md`（修前基线 + 修后终版两次运行，前后对照）；不填充任何遗留空白。
