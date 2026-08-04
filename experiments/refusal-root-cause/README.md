# 拒答根因调查（Refusal Root Cause）

**问题**：高拒答率根因未明确——是生成中的"无依据拒答"判定 prompt 过严，还是检索不足？（两者修法完全不同。）

**假设**：① prompt/`RELEVANCE_THRESHOLD` 判定过严；② 检索候选不足或相关片段未召回；③ 切块上下文断裂（因果链：解析丢格式 → 切块断裂 → 检索差 → 拒答）与 ①② 共同作用。

**设置**：本目录是 2026-08 一周冲刺第 3 阶段（≤1 天时间盒）的基地。工具：
- `diagnose_refusals.py` — 拒答样本诊断
- `diagnose_false_refusal.py` — 假拒答（本可回答却拒答）判定
- `diagnose_chunks.py` — 切块断裂诊断
- `validate_pool_assumption.py` — 候选池规模假设验证

**数据**：`llm_refusal_trace.txt`、`diagnostic_output.txt`（调查输入，保留至此）。

**结论**：未定论——本周调查产出结论后回填此处，并记录最小修复 + 前后评估对照。
