"""Phase B prompt 模板（issue #48）。版本一经固定即冻结；`PROMPT_VERSION` 落报告。

参考基线（**抄模式、不引依赖**，编号已核）：
- 改写（B2）：RRR 2305.14283「为检索而改写」；Self-Ask 2210.03350 / MindSearch 2407.20183
  的「约束不被丢」要求；HyDE 2212.10496 / Query2doc 2303.07678 / Step-back 2310.06117。
- 迭代（B3）：IRCoT 2212.10509（据已得证据派生下一跳、硬预算）、ReAct 2210.03629
  （Thought/Action/Observation）、Sufficient Context 2411.06037（充分性 autorater，弱判据）、
  MultiHop-RAG 仓库 `agentic_rag/`（同数据集直接对照：JudgeDecision 同时产出 enough+next_query）。
- 答案：MultiHop-RAG `qa_llama.py` 的 QA 前缀 + `qa_evaluate.py` 的判定口径。
"""
from __future__ import annotations

PROMPT_VERSION = "phase-b-v1-20260917"

# --------------------------------------------------------------------- B2 改写

REWRITE_SYSTEM = "You rewrite questions into search queries for a retrieval system."

REWRITE_TEMPLATE = """Rewrite the question below into ONE concise search query for a document \
retrieval system.

Rules:
- Preserve every distinguishing entity, person, organization, date, number and \
comparison/relation that the question asks about.
- Do not answer the question and do not add facts that are not in the question.
- Remove filler, politeness, and attribution phrases that do not help identify the answer \
(e.g. "according to X", "as reported by Y").
- Output a single line: only the rewritten query. No explanation, no quotation marks.

Question:
{question}

Rewritten query:"""


def build_rewrite_prompt(question: str) -> str:
    return REWRITE_TEMPLATE.format(question=question)


# ------------------------------------------------------------------- B3 判据

JUDGE_SYSTEM = "You decide whether retrieved evidence is sufficient to answer a question."

JUDGE_TEMPLATE = """Determine whether the retrieved evidence below is sufficient to answer the \
original question. Use ONLY the evidence; do not use outside knowledge.

Original question:
{question}

Previous search queries:
{history}

Retrieved evidence:
{evidence}

Return ONLY one valid JSON object with exactly these keys:
{{
  "enough": true or false,
  "missing_information": "what is still missing, or empty string",
  "next_query": "a new search query, or empty string",
  "evidence_ids_used": ["E1", "E2"],
  "reason": "one short sentence"
}}

Rules for "next_query":
- If the evidence is sufficient to answer, it MUST be an empty string.
- If it is insufficient, it MUST use an entity or fact already present in the evidence, and \
MUST NOT repeat any previous search query.
- It MUST be about the information still missing, NOT a restatement of the original question."""


def build_judge_prompt(question: str, evidence_context: str,
                       history: list[str]) -> str:
    history_text = "\n".join(f"- {q}" for q in history) or "- (none)"
    return JUDGE_TEMPLATE.format(
        question=question, history=history_text, evidence=evidence_context)


# --------------------------------------------------------------------- 答案

ANSWER_SYSTEM = "You answer questions strictly from the provided context."

ANSWER_TEMPLATE = """Below is a question followed by some context from different sources. Please \
answer the question based on the context. The answer to the question is a word or entity. If the \
provided information is insufficient to answer the question, respond 'Insufficient Information'. \
Answer directly without explanation.

Question: {question}

Context:

{context}"""


def build_answer_prompt(question: str, context: str) -> str:
    return ANSWER_TEMPLATE.format(question=question, context=context)


# ------------------------------------------------------- 答案判定（异模型裁判）

ANSWER_JUDGE_TEMPLATE = """You are grading an answer to a question against a gold answer.

Question: {question}
Gold answer: {gold}
Predicted answer: {prediction}

Decide whether the predicted answer conveys the same fact / entity / value as the gold answer.
Accept synonyms, aliases, abbreviations, reorderings and different surface forms.
Reject if it names a different entity or value, contradicts the gold answer, or says the \
information is insufficient.

Return ONLY one JSON object: {{"match": true or false, "reason": "one short sentence"}}"""


def build_answer_judge_prompt(question: str, gold: str, prediction: str) -> str:
    return ANSWER_JUDGE_TEMPLATE.format(question=question, gold=gold, prediction=prediction)
