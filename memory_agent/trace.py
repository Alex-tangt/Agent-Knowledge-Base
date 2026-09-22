"""检索 agent 的轨迹（trace）——**运行时与评测之间唯一的契约**（ADR-0030 D7）。

- 运行时（`memory_agent.agent_loop`）只负责**写**：它不看 gold、不 import 评测。
- 评测（`memory_agent.eval.harness`）只负责**读** + 打分。
- 两侧都只依赖本模块，**彼此不依赖**。

序列化用 JSONL（一行一条 trace），便于流式追加与离线分析。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any

# 停止触发词（stop.trigger）：由**代码**判定后写死，评测据此切片。
STOP_ANSWER = "answer"                 # 模型给出结论
STOP_INSUFFICIENT = "insufficient"     # 模型明说证据不足
STOP_NO_NEW_IDS = "no_new_ids"         # 本轮无新条目（确定性）
STOP_BUDGET = "budget"                 # 用满 hop 预算（确定性）
STOP_FALLBACK = "fallback"             # 模型未给出可用的下一跳 query（兜底）

STOP_TRIGGERS = (
    STOP_ANSWER, STOP_INSUFFICIENT, STOP_NO_NEW_IDS, STOP_BUDGET, STOP_FALLBACK,
)


@dataclass
class ToolCall:
    """运行时的一次工具调用（只记**摘要**：id/计数，不把召回正文灌进 trace）。"""
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    added_ids: list[str] = field(default_factory=list)
    result_count: int = 0


@dataclass
class Round:
    """一跳：一次检索 + 一次模型决策。"""
    round: int
    query: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    added_ids: list[str] = field(default_factory=list)
    model_output: str = ""


@dataclass
class Stop:
    trigger: str
    reason: str = ""


@dataclass
class Trace:
    """一条完整轨迹。`final.answer` / `final.evidence_ids` 供评测消费。"""
    id: str
    query: str
    rounds: list[Round] = field(default_factory=list)
    stop: Stop | None = None
    final: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Trace":
        rounds = []
        for raw in data.get("rounds", []):
            calls = [ToolCall(**c) for c in raw.get("tool_calls", [])]
            rounds.append(Round(
                round=raw["round"], query=raw["query"], tool_calls=calls,
                added_ids=list(raw.get("added_ids", [])),
                model_output=raw.get("model_output", ""),
            ))
        stop_raw = data.get("stop")
        stop = Stop(**stop_raw) if isinstance(stop_raw, dict) else None
        return cls(
            id=data["id"], query=data["query"], rounds=rounds, stop=stop,
            final=dict(data.get("final", {})), meta=dict(data.get("meta", {})),
        )

    def evidence_ids(self) -> list[str]:
        """累计证据 id（按首次出现排序，去重）。"""
        seen: set[str] = set()
        out: list[str] = []
        for round_ in self.rounds:
            for entry_id in round_.added_ids:
                if entry_id not in seen:
                    seen.add(entry_id)
                    out.append(entry_id)
        return out


def append_trace(path: str, trace: Trace) -> None:
    """追加一行 JSONL（父目录自动创建）。"""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(trace.to_dict(), ensure_ascii=False) + "\n")


def read_traces(path: str) -> list[Trace]:
    """读回 JSONL（忽略空行）。文件不存在返回空表。"""
    if not os.path.isfile(path):
        return []
    traces: list[Trace] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            traces.append(Trace.from_dict(json.loads(line)))
    return traces
