# 相关性阈值校准（Relevance Threshold Calibration）

**问题**：`RELEVANCE_THRESHOLD=0.85`（post-reranker 距离）是否合理——误拒 vs 幻觉的权衡点。

**设置**：`calibrate_relevance.py`（Qdrant + BGE-M3 版），对候选样本测 post-reranker 距离分布，给出阈值建议。

**数据**：无记录。

**结论**：未记录。若 2026-08 冲刺内拒答调查需要调整阈值，在此回填数据与结论。
