"""检索 agent 的轨迹（trace）——**运行时与评测之间唯一的契约**（ADR-0030 D7）。

- 运行时（`memory_agent.agent_loop`）只负责**写**：它不看 gold、不 import 评测。
- 评测（`memory_agent.eval.harness`）只负责**读** + 打分。
- 两侧都只依赖本模块，**彼此不依赖**。

确定性锚点（ADR-0030 D2/D7）：运行时落盘前调 `seal_trace()` 把 **`run_hash`**（轨迹的
内容哈希，不含随机 `id`）写进 `meta`——同一输入跑两次得同一 hash，重放可逐位核对。

序列化用 JSONL（一行一条 trace），便于流式追加与离线分析。
"""
from __future__ import annotations

import hashlib
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
    """一跳：一次检索（或一次命名工具派发）+ 一次模型决策。

    `added_ids` 由 `tool_calls` 派生（**不再**单独存一份，避免同一事实两个来源）。
    """
    round: int
    query: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    model_output: str = ""

    @property
    def added_ids(self) -> list[str]:
        """本轮新增条目 id：各次工具调用的 `added_ids` 归并去重（保持首次出现顺序）。"""
        seen: set[str] = set()
        out: list[str] = []
        for call in self.tool_calls:
            for entry_id in call.added_ids:
                if entry_id not in seen:
                    seen.add(entry_id)
                    out.append(entry_id)
        return out


@dataclass
class Stop:
    """停止判定。`trigger` 必须是 `STOP_TRIGGERS` 之一（契约收口，写错即报错）。"""
    trigger: str
    reason: str = ""

    def __post_init__(self) -> None:
        if self.trigger not in STOP_TRIGGERS:
            raise ValueError(
                f"未知 stop trigger：{self.trigger!r}（可选：{', '.join(STOP_TRIGGERS)}）"
            )


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
                model_output=raw.get("model_output", ""),
            ))
        stop_raw = data.get("stop")
        stop = Stop(**stop_raw) if isinstance(stop_raw, dict) else None
        return cls(
            id=data["id"], query=data["query"], rounds=rounds, stop=stop,
            final=dict(data.get("final", {})), meta=dict(data.get("meta", {})),
        )

    def evidence_ids(self) -> list[str]:
        """证据 id —— 语义 = **真正展示给模型的条目**（`final.evidence_ids` 为唯一来源）。

        旧 trace（本语义前落盘）没有 `final.evidence_ids`，回退按轮次派生，只作兼容读取。
        """
        stated = self.final.get("evidence_ids")
        if isinstance(stated, list):
            return [str(entry_id) for entry_id in stated]
        seen: set[str] = set()
        out: list[str] = []
        for round_ in self.rounds:
            for entry_id in round_.added_ids:
                if entry_id not in seen:
                    seen.add(entry_id)
                    out.append(entry_id)
        return out

    def run_hash(self) -> str:
        """本轨迹的内容哈希（见 `compute_run_hash`）。"""
        return compute_run_hash(self)


def _canonical(trace: Trace) -> dict[str, Any]:
    """哈希输入：轨迹内容，**剔除随机 `id` 与自指的 `meta.run_hash`**。"""
    data = trace.to_dict()
    data.pop("id", None)
    meta = dict(data.get("meta") or {})
    meta.pop("run_hash", None)
    data["meta"] = meta
    return data


def compute_run_hash(trace: Trace) -> str:
    """轨迹的**内容哈希**（ADR-0030 D2/D7 的确定性锚点）。

    覆盖 query / 每一跳（query、工具调用与参数、命中 id、模型输出）/ stop / final / meta
    （`run_hash` 自身除外）；**不含随机 `id`**——同一输入两次运行得同一 hash。
    """
    payload = json.dumps(_canonical(trace), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def seal_trace(trace: Trace) -> Trace:
    """把 `run_hash` 写进 `meta` 并返回同一条 trace（落盘前调用；幂等）。"""
    trace.meta["run_hash"] = compute_run_hash(trace)
    return trace


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
