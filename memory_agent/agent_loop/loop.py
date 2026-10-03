"""`AgentLoop` —— **代码控**的检索循环（运行时，ADR-0030 D7）。

停止由**代码**判定：预算（确定性）∪ 无新条目（确定性）∪ 充分性信号（**默认 = 模型给出
终结回复**，是可替换接缝——生产可换成独立校验器而不改循环）；过程落成 `Trace` 并盖
`run_hash`（确定性锚点）。本包**不 import 评测**（守卫见 `tests/unit/test_agent_loop_isolation.py`）。
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from memory_agent.agent_loop.budget import Budget
from memory_agent.agent_loop.ports import LLMClient, SufficiencyChecker, ToolRegistry
from memory_agent.agent_loop.tools import SEARCH_TOOL, describe_tools
from memory_agent.trace import (
    STOP_BUDGET,
    STOP_FALLBACK,
    STOP_NO_NEW_IDS,
    Round,
    Stop,
    ToolCall,
    Trace,
    seal_trace,
)

SYSTEM_PROMPT = (
    "你是「记忆检索循环」中的一步：根据问题与已检索证据，判断是否足以作答。\n"
    "- 证据不足 → 只回一行：NEXT_QUERY: <聚焦的新 query>（不得重复已用 query）\n"
    "- 需要读回某条证据的全文 → 只回一行：TOOL: <工具名> <JSON 参数>\n"
    "- 足以作答 → 回：ANSWER: <答案>\n"
    "- 找不到 → 只回：INSUFFICIENT\n"
    "不要输出其它内容。"
)

_NEXT_QUERY_RE = re.compile(r"^[ \t]*NEXT_QUERY[ \t]*:[ \t]*(.+)$", re.IGNORECASE | re.MULTILINE)
_ANSWER_RE = re.compile(r"^[ \t]*ANSWER[ \t]*:[ \t]*(.*)$", re.IGNORECASE | re.MULTILINE)
_INSUFFICIENT_RE = re.compile(r"^[ \t]*INSUFFICIENT[ \t]*$", re.IGNORECASE | re.MULTILINE)
_TOOL_RE = re.compile(
    r"^[ \t]*TOOL[ \t]*:[ \t]*([A-Za-z_][A-Za-z0-9_.]*)[ \t]*(.*)$",
    re.IGNORECASE | re.MULTILINE,
)

MAX_TOOL_RESULT_CHARS = 2000


@dataclass
class Decision:
    """从模型输出解析出的决策。

    kind ∈ {answer, insufficient, tool, next_query, unknown}；`tool`/`args` 仅 kind="tool" 时有值。
    """
    kind: str
    value: str = ""
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)


def _parse_args(raw: str) -> dict[str, Any] | None:
    """解析工具参数（`{}` = 合法空参；JSON 非法/非对象 → None，表示这次决策不可用）。"""
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_decision(model_output: str) -> Decision:
    """解析模型输出（**终结标记优先**，其次命名工具，其次下一跳 query）。"""
    text = model_output or ""
    match = _ANSWER_RE.search(text)
    if match:
        return Decision("answer", match.group(1).strip())
    if _INSUFFICIENT_RE.search(text):
        return Decision("insufficient")
    match = _TOOL_RE.search(text)
    if match:
        args = _parse_args(match.group(2))
        if args is None:
            return Decision("unknown")
        return Decision("tool", tool=match.group(1), args=args)
    match = _NEXT_QUERY_RE.search(text)
    if match:
        return Decision("next_query", match.group(1).strip())
    return Decision("unknown")


class ModelDeclaredSufficiency:
    """默认充分性信号 = 模型给出**终结**回复（ANSWER / INSUFFICIENT）。

    这是**可替换的接缝**：按 ADR-0030 的谨慎口径，生产可换成独立校验器（或确定性判据），
    而**不改循环**。注意：它**不是**确定性判据——确定性停止只有 `no_new_ids` 与 `budget`。
    """

    def sufficient(self, *, question: str, evidence: list[dict[str, Any]],
                   model_output: str) -> bool:
        return parse_decision(model_output).kind in ("answer", "insufficient")


def _render(value: Any) -> str:
    if isinstance(value, str):
        return value[:MAX_TOOL_RESULT_CHARS]
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(value)
    return text[:MAX_TOOL_RESULT_CHARS]


class AgentLoop:
    """一条查询的检索循环：检索 → 模型决策 →（代码判定）停止 / 派发工具 / 继续。"""

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
        tool_results: list[dict[str, Any]] = []
        seen: set[str] = set()
        history: list[str] = []
        stop: Stop | None = None
        current = query
        last = Decision("unknown")
        skip_search = False

        for index in range(1, self.budget.max_rounds + 1):
            calls: list[ToolCall] = []
            added: list[str] = []
            run_search = not skip_search
            skip_search = False

            if run_search:
                args: dict[str, Any] = {"query": current, "k": self.k}
                hits = list(self.tools.call(SEARCH_TOOL, args) or [])
                for hit in hits:
                    entry_id = hit.get("id")
                    if not entry_id or entry_id in seen:
                        continue
                    seen.add(entry_id)
                    added.append(entry_id)
                    if len(evidence) < self.budget.max_evidence:
                        evidence.append(hit)
                calls.append(ToolCall(tool=SEARCH_TOOL, args=args,
                                      added_ids=added, result_count=len(hits)))

            model_output = self.llm.complete(
                self._messages(question, evidence, history, tool_results))
            last = parse_decision(model_output)

            if last.kind == "tool":
                call, rendered = self._dispatch(last, seen, evidence)
                calls.append(call)
                tool_results.append({"tool": last.tool, "result": rendered})
                skip_search = True

            rounds.append(Round(
                round=index, query=current, tool_calls=calls, model_output=model_output,
            ))

            if self.sufficiency.sufficient(question=question, evidence=evidence,
                                           model_output=model_output):
                stop = Stop(last.kind, "模型给出终结回复")
                break
            if not added and last.kind != "tool":
                stop = Stop(STOP_NO_NEW_IDS, "本轮无新条目")
                break
            if index >= self.budget.max_rounds:
                stop = Stop(STOP_BUDGET, f"用满 {self.budget.max_rounds} 轮预算")
                break
            if last.kind == "tool":
                continue
            if last.kind != "next_query" or not last.value:
                stop = Stop(STOP_FALLBACK, "模型未给出可用的下一跳 query")
                break
            history.append(current)
            current = last.value

        answer = last.value if last.kind == "answer" else None
        trace = Trace(
            id=trace_id, query=question, rounds=rounds, stop=stop,
            final={"answer": answer, "evidence_ids": [hit["id"] for hit in evidence]},
            meta={"k": self.k, "max_rounds": self.budget.max_rounds,
                  "max_evidence": self.budget.max_evidence},
        )
        return seal_trace(trace)

    def _dispatch(self, decision: Decision, seen: set[str],
                  evidence: list[dict[str, Any]]) -> tuple[ToolCall, str]:
        """执行模型点名的工具；**未知工具不抛出**——把错误当作工具结果回喂，交模型下一轮改正。"""
        try:
            result = self.tools.call(decision.tool, decision.args)
        except KeyError as error:
            call = ToolCall(tool=decision.tool, args=decision.args,
                            added_ids=[], result_count=0)
            return call, f"未知工具：{error}"
        added: list[str] = []
        for item in (result if isinstance(result, list) else [result]):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            entry_id = str(item["id"])
            if entry_id in seen:
                continue
            seen.add(entry_id)
            added.append(entry_id)
            if len(evidence) < self.budget.max_evidence:
                evidence.append(item)
        return (ToolCall(tool=decision.tool, args=decision.args, added_ids=added,
                         result_count=1 if result is not None else 0),
                _render(result))

    def _messages(self, question: str, evidence: list[dict[str, Any]],
                  history: list[str], tool_results: list[dict[str, Any]]) -> list[dict[str, str]]:
        evidence_text = "\n\n".join(
            f"[{hit.get('id')}] {hit.get('title', '')}\n{hit.get('snippet', '')}"
            for hit in evidence
        ) or "(无)"
        history_text = "\n".join(f"- {q}" for q in history) or "- (无)"
        results_text = "\n".join(
            f"- {item['tool']} → {item['result']}" for item in tool_results
        ) or "(无)"
        user = (
            f"问题：{question}\n\n可用工具：\n{describe_tools(self.tools)}\n\n"
            f"已用检索 query：\n{history_text}\n\n"
            f"已检索证据：\n{evidence_text}\n\n"
            f"已派发工具结果：\n{results_text}"
        )
        return [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user}]
