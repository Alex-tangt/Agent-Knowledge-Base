# #27 软拒答：保留硬闸门，只软化措辞

## 问题

`ragcore/services/rag_service.py` 的 `_has_evidence()` 在 post-rerank 最优距离 > `RELEVANCE_THRESHOLD=0.85`
时不调 LLM、直接回模板 `NO_EVIDENCE_MESSAGE`。模板措辞生硬（"知识库中未找到直接依据，建议……"）。
ADR-0023 收窄 ADR-0017：**保留硬闸门，只软化措辞**——不改"离题不生成"的行为。
（背景：`experiments/refusal-root-cause/`，基线误拒率 2.4% / 无答案拒答率 91.7%。）

## 改了什么

行为冻结，只有措辞 + 可观测字段：

| 位置 | 改前 | 改后 |
|---|---|---|
| `NO_EVIDENCE_MESSAGE` | `知识库中未找到直接依据，建议提供更具体的问题或补充相关资料。` | `抱歉，知识库中没有检索到与这个问题直接相关的内容，暂时无法给出有依据的回答。你可以把问题描述得更具体一些（例如涉及哪部法律、具体条款或适用情形），我再帮你查一次。` |
| `PROMPT_NO_EVIDENCE` | 「若上下文……才回复：<模板>」 | 「只有当上下文完全不涉及用户问题时，才用自然的口吻说明……；其余情况都要基于上下文作答」 |
| system prompt 末句 | 「仅当上下文完全不涉及问题时才说明无法回答。」 | 「如果上下文与问题基本无关，请用自然、诚恳的口吻说明知识库里没有找到相关依据，不要凭空作答。」 |
| SSE `metadata`（两条分支） | 无 | 新增 `best_distance`（最优 post-rerank 距离，未命中/命中都带；`None` = 无候选） |

新增 `NO_EVIDENCE_MARKER`（可检测短语）单一来源；`legal_web/tests/score_eval.py` 的
`NO_EVIDENCE_HINTS` 直接从 `rag_service` 导入它（另保留两条旧措辞兜底历史回答）。

## 行为不变（硬指标）

- `_has_evidence()` 语义不变：离题 → **不调 LLM**、回模板；`RELEVANCE_THRESHOLD=0.85` 不变。
- 抽取 `_best_distance()` 只为复用；硬闸门用**未取整**原值比较（`0.85001 > 0.85` 仍判无依据），
  取整仅发生在透出 metadata 时，避免边界被四舍五入改写。
- 无需 legal 评测重跑。

## 验证

```
venv\Scripts\python.exe -m pytest tests/unit -q
# 299 passed
```

新增/更新单测（`tests/unit/test_rag_service.py`）：`_best_distance` 三例、
闸门边界不取整、软模板含 marker、流边界下**离题零 LLM 调用**且 metadata 带 `best_distance`、
命中路径 metadata 带 `best_distance`。

## 结论

措辞已软化，硬闸门与阈值语义逐位不变；`best_distance` 作为可观测置信度随 SSE `metadata` 透出。
