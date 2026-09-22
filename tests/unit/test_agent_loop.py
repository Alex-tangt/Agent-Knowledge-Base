"""AgentLoop（运行时）单测：代码控停止 + trace 契约 + provider 解析。

全部用**假 LLM / 假工具**，不加载模型、不访问网络（确定性）。
"""
import pytest

from memory_agent.agent_loop.budget import Budget
from memory_agent.agent_loop.loop import AgentLoop, parse_decision
from memory_agent.agent_loop.llm import ProviderSpec, resolve_provider
from memory_agent.trace import (
    STOP_ANSWER,
    STOP_BUDGET,
    STOP_INSUFFICIENT,
    STOP_NO_NEW_IDS,
    Trace,
    append_trace,
    read_traces,
)

pytestmark = pytest.mark.filterwarnings("ignore")


class FakeLLM:
    """按脚本逐次返回；用尽后重复最后一条。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append(messages)
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


class FakeTools:
    """按 query 返回固定命中；命中形如 {"id": ..., "title": ..., "snippet": ...}。"""

    def __init__(self, by_query):
        self.by_query = by_query
        self.calls = []

    def list_tools(self):
        return [{"name": "memory_search"}]

    def call(self, name, args):
        self.calls.append((name, args))
        return list(self.by_query.get(args["query"], []))


def _hit(entry_id):
    return {"id": entry_id, "title": entry_id, "snippet": f"snip {entry_id}"}


class TestParseDecision:
    def test_answer_takes_precedence(self):
        assert parse_decision("ANSWER: 42") == parse_decision("ANSWER: 42")
        d = parse_decision("ANSWER: 42")
        assert d.kind == "answer" and d.value == "42"

    def test_insufficient(self):
        assert parse_decision("INSUFFICIENT").kind == "insufficient"

    def test_next_query(self):
        d = parse_decision("NEXT_QUERY: 张量并行 vs 流水线并行")
        assert d.kind == "next_query" and "张量并行" in d.value

    def test_unknown(self):
        assert parse_decision("随便说点什么").kind == "unknown"


class TestStopByCode:
    def test_stops_on_answer(self):
        llm = FakeLLM(["ANSWER: 42"])
        tools = FakeTools({"q": [_hit("a")]})
        trace = AgentLoop(llm, tools).run("q", trace_id="t1")
        assert trace.stop.trigger == STOP_ANSWER
        assert trace.final["answer"] == "42"
        assert trace.evidence_ids() == ["a"]
        assert len(trace.rounds) == 1

    def test_stops_on_insufficient(self):
        llm = FakeLLM(["INSUFFICIENT"])
        trace = AgentLoop(llm, FakeTools({"q": []})).run("q")
        assert trace.stop.trigger == STOP_INSUFFICIENT
        assert trace.final["answer"] is None

    def test_stops_when_no_new_ids(self):
        # 第 1 跳要下一跳；第 2 跳工具仍只回同一条 → 无新 id（确定性停止）。
        llm = FakeLLM(["NEXT_QUERY: q2", "NEXT_QUERY: q3"])
        tools = FakeTools({"q": [_hit("a")], "q2": [_hit("a")], "q3": [_hit("b")]})
        trace = AgentLoop(llm, tools, budget=Budget(max_hops=4)).run("q")
        assert trace.stop.trigger == STOP_NO_NEW_IDS
        assert len(trace.rounds) == 2

    def test_stops_on_budget(self):
        # 每跳都有新 id、模型一直要下一跳 → 用满预算。
        llm = FakeLLM(["NEXT_QUERY: q2", "NEXT_QUERY: q3", "NEXT_QUERY: q4"])
        tools = FakeTools({"q": [_hit("a")], "q2": [_hit("b")], "q3": [_hit("c")]})
        trace = AgentLoop(llm, tools, budget=Budget(max_hops=2)).run("q")
        assert trace.stop.trigger == STOP_BUDGET
        assert len(trace.rounds) == 2
        assert trace.evidence_ids() == ["a", "b"]

    def test_evidence_ids_dedup_and_order(self):
        llm = FakeLLM(["NEXT_QUERY: q2", "ANSWER: done"])
        tools = FakeTools({"q": [_hit("a"), _hit("b")], "q2": [_hit("b"), _hit("c")]})
        trace = AgentLoop(llm, tools, budget=Budget(max_hops=3)).run("q")
        assert trace.evidence_ids() == ["a", "b", "c"]


class TestTraceIO:
    def test_round_trip(self, tmp_path):
        llm = FakeLLM(["ANSWER: 42"])
        trace = AgentLoop(llm, FakeTools({"q": [_hit("a")]})).run("q", trace_id="t1")
        path = str(tmp_path / "trace.jsonl")
        append_trace(path, trace)
        loaded = read_traces(path)
        assert len(loaded) == 1
        assert loaded[0].to_dict() == trace.to_dict()
        assert loaded[0].stop.trigger == STOP_ANSWER
        assert loaded[0].rounds[0].tool_calls[0].tool == "memory_search"

    def test_read_missing_file_is_empty(self, tmp_path):
        assert read_traces(str(tmp_path / "nope.jsonl")) == []


class TestMemoryToolRegistry:
    class _FakeIndex:
        def __init__(self):
            self.search_calls = []

        def search(self, query, k=5, exclude_retired=False):
            self.search_calls.append((query, k, exclude_retired))
            return [{"id": "a"}]

        def get(self, entry_id):
            return {"id": entry_id, "content": "body"}

    def test_search_passes_k_and_retired_flag(self):
        from memory_agent.agent_loop.tools import MemoryToolRegistry

        index = self._FakeIndex()
        registry = MemoryToolRegistry(index, exclude_retired=True)
        hits = registry.call("memory_search", {"query": "q", "k": 3})
        assert hits == [{"id": "a"}]
        assert index.search_calls == [("q", 3, True)]

    def test_get_and_unknown_tool(self):
        from memory_agent.agent_loop.tools import MemoryToolRegistry

        registry = MemoryToolRegistry(self._FakeIndex())
        assert registry.call("memory_get", {"entry_id": "x"})["content"] == "body"
        with pytest.raises(KeyError):
            registry.call("nope", {})


class TestProviderResolution:
    def test_default_is_opencode_server(self, monkeypatch):
        for name in ("MEMORY_AGENT_LLM_PROVIDER", "MEMORY_AGENT_LLM_BASE_URL",
                     "MEMORY_AGENT_LLM_MODEL", "MEMORY_AGENT_LLM_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        assert resolve_provider().provider == "opencode-server"

    def test_env_openai_compat(self, monkeypatch):
        monkeypatch.setenv("MEMORY_AGENT_LLM_PROVIDER", "openai-compat")
        monkeypatch.setenv("MEMORY_AGENT_LLM_BASE_URL", "https://example/v1")
        monkeypatch.setenv("MEMORY_AGENT_LLM_MODEL", "qwen3.7-flash")
        monkeypatch.setenv("MEMORY_AGENT_LLM_SEED", "42")
        spec = resolve_provider()
        assert spec.provider == "openai-compat"
        assert spec.base_url == "https://example/v1"
        assert spec.model == "qwen3.7-flash"
        assert spec.seed == 42

    def test_override_beats_env(self, monkeypatch):
        monkeypatch.setenv("MEMORY_AGENT_LLM_PROVIDER", "opencode-server")
        spec = resolve_provider(ProviderSpec(provider="openai-compat", model="m"))
        assert spec.provider == "openai-compat"
        assert spec.model == "m"
