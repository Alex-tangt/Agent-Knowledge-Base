# 0017 通用 RAG 去强制拒答：无依据由 LLM 在对话中说明

Status: accepted (amended by ADR-0023：保留硬闸门，仅软化措辞)

## 背景

现状（`ragcore/services/rag_service.py`）：检索 + 重排后有一道**硬闸门** `_has_evidence()`——post-rerank 最优距离 > `RELEVANCE_THRESHOLD=0.85` 时**不调用 LLM**，直接返回模板 `NO_EVIDENCE_MESSAGE`。2026-08 拒答根因调查（`experiments/refusal-root-cause/`）曾选「A 接受现状」。

用户（2026-09-15）决定反转：**通用 RAG 不做强制拒答**，不设硬闸门，由 LLM 在对话中说明「知识库中无相关内容」。

## 决策

- **D1 去掉生成路径的硬闸门。** 检索后总是构造 prompt 并调用 LLM；`RELEVANCE_THRESHOLD` 不再触发「不调 LLM、回模板」。默认 `None`（保留为可观测的置信度信号，而非强制开关）。
- **D2 全局软拒答（含 `legal_web`）。** 所有 KB 一律由 LLM 判定：上下文无关 / 不足时，在回答里说明知识库中没有相关内容，并明确不编造。prompt 负责措辞。
- **D3 检索层不动。** 拒答是生成层行为；检索指标（recall@k / nDCG / MRR）不受影响。无答案 query 在评测里只作**描述性观察**，不用于校准阈值（对应 #24）。

## 理由

- 硬阈值把「边界相关」误判成「无依据」——2026-08 唯一误拒即 reranker 边界分 0.87>0.85。软拒答消除这类结构性误拒，口吻也更自然。
- 通用 RAG 的用户感知是对话，模板式拒绝生硬；由 LLM 组织语言更合适。
- 保持单一代码路径（无硬/软分叉），核心更简洁。

Considered options:
- A 全局软拒答（采用）——自然、无边界误拒、路径单一；代价是离题问题失去硬护栏。
- B 保留硬闸门但只软化措辞（弃）——仍依赖阈值，边界误拒还在。
- C 核心默认软 + 法律 KB 保留硬闸门（弃）——灵活性更高，但「通用 RAG 不做强制拒答」这一决定被稀释，多一层配置面。
- D 维持现状（2026-08 选 A，弃）——被本决定反转。

## Consequences

- **文档 / 评测口径要同步**：`legal_web` 的 `README.md`、`AGENTS.md`、`docs/test_report.md`、`legal_web/tests/results_scored.md` 中「无依据拒答防幻觉 / 拒答率」的说法与数字需更新或重跑，否则证据与实际不符。
- **风险移动**：离题问题不再有硬护栏，靠 prompt 兜底；幻觉 / 越界风险上升，须在评测中持续观察（LLM-as-judge 忠实度）。
- **延迟 / 成本**：无答案 query 也会走一次生成（原为 0 生成）。
- `_has_evidence()` 与 `RELEVANCE_THRESHOLD` 的硬闸门语义作废；是移除还是留作置信度信号，由实现票决定。
- #24 的评测基座与本决定一致：无答案 query 仅报告 top-1 分数分布，不设阈值。

Relates: #27（实现票：去闸门 + prompt + 文档/评测重跑）、#24（测量口径）、`experiments/refusal-root-cause/`、ADR-0003。
