"""`AgentLoop` —— **代码控**的检索循环（运行时，ADR-0030 D7）。

停止由**代码**判定（预算 ∪ 无新 id ∪ 充分性信号），不依赖模型自报；过程落成 `Trace`。
本包**不 import 评测**（守卫见 `tests/unit/test_agent_loop_isolation.py`）。
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from memory_agent.agent_loop.budget import Budget
from memory_agent.agent_loop.ports import LLMClient, SufficiencyChecker, ToolRegistry
from memory_agent.agent_loop.tools import SEARCH_TOOL
from memory_agent.trace import (
    STOP_ANSWER,
    STOP_BUDGET,
    STOP_FALLBACK,
    STOP_INSUFFICIENT,
    STOP_NO_NEW_IDS,
    Round,
    Stop,
    ToolCall,
    Trace,
)

SYSTEM_PROMPT = (
    "你是「记忆检索循环」中的一步：根据问题与已检索证据，判断是否足以作答。\n"
    "- 证据不足 → 只回一行：NEXT_QUERY: <聚焦的新 query>（不得重复已用 query）\n"
    "- 足以作答 → 回：ANSWER: <答案>\n"
    "- 找不到 → 只回：INSUFFICIENT\n"
    "不要输出其它内容。"
)

_NEXT_QUERY_RE = re.compile(r"^[ \t]*NEXT_QUERY[ \t]*:[ \t]*(.+)$", re.IGNORECASE | re.MULTILINE)
_ANSWER_RE = re.compile(r"^[ \t]*ANSWER[ \t]*:[ \t]*(.*)$", re.IGNORECASE | re.MULTILINE)
_INSUFFICIENT_RE = re.compile(r"^[ \t]*INSUFFICIENT[ \t]*$", re.IGNORECASE | re.MULTILINE)


@dataclass
class Decision:
    """从模型输出解析出的决策。kind ∈ {answer, insufficient, next_query, unknown}。"""
    kind: str
    value: str = ""


def parse_decision(model_output: str) -> Decision:
    """解析模型输出（终结标记优先，其次下一跳 query）。"""
    text = model_output or ""
    match = _ANSWER_RE.search(text)
    if match:
        return Decision("answer", match.group(1).strip())
    if _INSUFFICIENT_RE.search(text):
        return Decision("insufficient")
    match = _NEXT_QUERY_RE.search(text)
    if match:
        return Decision("next_query", match.group(1).strip())
    return Decision("unknown")


class ModelDeclaredSufficiency:
    """默认充分性信号 = 模型给出**终结**回复（ANSWER / INSUFFICIENT）。

    这是**可替换的接缝**：按 ADR-0030 的谨慎口径，生产可换成独立校验器（或确定性判据），
    而**不改循环**。
    """

    def sufficient(self, *, question: str, evidence: list[dict[str, Any]],
                   model_output: str) -> bool:
        return parse_decision(model_output).kind in ("answer", "insufficient")


def _terminal_trigger(decision: Decision) -> str:
    if decision.kind in ("answer", "insufficient"):
        return decision.kind
    return STOP_ANSWER


class AgentLoop:
    """一条查询的检索循环：检索 → 模型决策 →（代码判定）停止或继续。"""

    def __init__(self, llm: LLMClient, tools: ToolRegistry, *,
                 budget: Budget | None = None,
                 sufficiency: SufficiencyChecker | None = None,
                 k: int = 5):
        self.llm = llm
        self.tools = tools
        self.budget = budget or Budget()
        self.sufficiency = sufficiency or ModelDeclaredSufficiency()
        self.k = k

    def run(self, query: str, *, trace_id: str | None = None) -> Trace:
        question = query
        trace_id = trace_id or f"tr-{uuid.uuid4().hex[:12]}"
        rounds: list[Round] = []
        evidence: list[dict[str, Any]] = []
        seen: set[str] = set()
        history: list[str] = []
        stop: Stop | None = None
        current = query
        last = Decision("unknown")

        for index in range(1, self.budget.max_hops + 1):
            args: dict[str, Any] = {"query": current, "k": self.k}
            hits = list(self.tools.call(SEARCH_TOOL, args) or [])
            added: list[str] = []
            for hit in hits:
                entry_id = hit.get("id")
                if not entry_id or entry_id in seen:
                    continue
                seen.add(entry_id)
                added.append(entry_id)
                if len(evidence) < self.budget.max_evidence:
                    evidence.append(hit)

            model_output = self.llm.complete(self._messages(question, evidence, history))
            last = parse_decision(model_output)
            rounds.append(Round(
                round=index, query=current,
                tool_calls=[ToolCall(tool=SEARCH_TOOL, args=args,
                                     added_ids=added, result_count=len(hits))],
                added_ids=added, model_output=model_output,
            ))

            if self.sufficiency.sufficient(question=question, evidence=evidence,
                                           model_output=model_output):
                stop = Stop(_terminal_trigger(last), "模型给出终结回复")
                break
            if not added:
                stop = Stop(STOP_NO_NEW_IDS, "本轮无新条目")
                break
            if index >= self.budget.max_hops:
                stop = Stop(STOP_BUDGET, f"用满 {self.budget.max_hops} 轮预算")
                break
            if last.kind != "next_query" or not last.value:
                stop = Stop(STOP_FALLBACK, "模型未给出可用的下一跳 query")
                break
            history.append(current)
            current = last.value

        answer = last.value if last.kind == "answer" else None
        return Trace(
            id=trace_id, query=question, rounds=rounds, stop=stop,
            final={"answer": answer, "evidence_ids": [hit["id"] for hit in evidence]},
            meta={"k": self.k, "max_hops": self.budget.max_hops},
        )

    def _messages(self, question: str, evidence: list[dict[str, Any]],
                  history: list[str]) -> list[dict[str, str]]:
        evidence_text = "\n\n".join(
            f"[{hit.get('id')}] {hit.get('title', '')}\n{hit.get('snippet', '')}"
            for hit in evidence
        ) or "(无)"
        history_text = "\n".join(f"- {q}" for q in history) or "- (无)"
        user = (
            f"问题：{question}\n\n已用检索 query：\n{history_text}\n\n"
            f"已检索证据：\n{evidence_text}"
        )
        return [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user}]
