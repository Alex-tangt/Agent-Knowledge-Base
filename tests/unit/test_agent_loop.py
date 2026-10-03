"""AgentLoop（运行时）单测：代码控停止 + trace 契约 + 工具派发 + provider 解析。

全部用**假 LLM / 假工具**，不加载模型、不访问网络（确定性）。
"""
import json

import httpx
import pytest

from memory_agent.agent_loop.budget import Budget
from memory_agent.agent_loop.llm import (
    OpencodeServerClient,
    ProviderSpec,
    resolve_provider,
)
from memory_agent.agent_loop.loop import AgentLoop, parse_decision
from memory_agent.agent_loop.tools import describe_tools
from memory_agent.trace import (
    STOP_ANSWER,
    STOP_BUDGET,
    STOP_INSUFFICIENT,
    STOP_NO_NEW_IDS,
    STOP_TRIGGERS,
    Stop,
    append_trace,
    compute_run_hash,
    read_traces,
    seal_trace,
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
        return [
            {
                "name": "memory_search",
                "description": "语义检索记忆条目。",
                "parameters": {"type": "object",
                               "properties": {"query": {"type": "string"},
                                              "k": {"type": "integer"}},
                               "required": ["query"]},
            },
        ]

    def call(self, name, args):
        self.calls.append((name, args))
        return list(self.by_query.get(args["query"], []))


def _hit(entry_id):
    return {"id": entry_id, "title": entry_id, "snippet": f"snip {entry_id}"}


class TestParseDecision:
    def test_answer_takes_precedence(self):
        d = parse_decision("ANSWER: 42")
        assert d.kind == "answer" and d.value == "42"

    def test_insufficient(self):
        assert parse_decision("INSUFFICIENT").kind == "insufficient"

    def test_next_query(self):
        d = parse_decision("NEXT_QUERY: 张量并行 vs 流水线并行")
        assert d.kind == "next_query" and "张量并行" in d.value

    def test_tool_with_json_args(self):
        d = parse_decision('TOOL: memory_get {"entry_id": "abc"}')
        assert d.kind == "tool" and d.tool == "memory_get"
        assert d.args == {"entry_id": "abc"}

    def test_tool_with_bad_json_is_unknown(self):
        # 参数不是合法 JSON → 该次决策不可用（由循环兜底停止），而不是抛异常。
        assert parse_decision("TOOL: memory_get {oops").kind == "unknown"

    def test_unknown(self):
        assert parse_decision("随便说点什么").kind == "unknown"


class TestBudget:
    def test_max_rounds_must_be_positive(self):
        with pytest.raises(ValueError):
            Budget(max_rounds=0)

    def test_max_evidence_must_be_positive(self):
        with pytest.raises(ValueError):
            Budget(max_evidence=0)


class TestStopContract:
    def test_known_triggers_accepted(self):
        for trigger in STOP_TRIGGERS:
            assert Stop(trigger).trigger == trigger

    def test_unknown_trigger_rejected(self):
        with pytest.raises(ValueError):
            Stop("bogus")


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
        trace = AgentLoop(llm, tools, budget=Budget(max_rounds=4)).run("q")
        assert trace.stop.trigger == STOP_NO_NEW_IDS
        assert len(trace.rounds) == 2

    def test_stops_on_budget(self):
        # 每跳都有新 id、模型一直要下一跳 → 用满预算。
        llm = FakeLLM(["NEXT_QUERY: q2", "NEXT_QUERY: q3", "NEXT_QUERY: q4"])
        tools = FakeTools({"q": [_hit("a")], "q2": [_hit("b")], "q3": [_hit("c")]})
        trace = AgentLoop(llm, tools, budget=Budget(max_rounds=2)).run("q")
        assert trace.stop.trigger == STOP_BUDGET
        assert len(trace.rounds) == 2
        assert trace.evidence_ids() == ["a", "b"]

    def test_evidence_ids_dedup_and_order(self):
        llm = FakeLLM(["NEXT_QUERY: q2", "ANSWER: done"])
        tools = FakeTools({"q": [_hit("a"), _hit("b")], "q2": [_hit("b"), _hit("c")]})
        trace = AgentLoop(llm, tools, budget=Budget(max_rounds=3)).run("q")
        assert trace.evidence_ids() == ["a", "b", "c"]


class TestEvidenceContract:
    """证据 id 只有**一个**来源：真正展示给模型（受 `max_evidence` 上限）的那些条目。"""

    def test_capped_evidence_is_the_reported_one(self):
        tools = FakeTools({"q": [_hit("a"), _hit("b"), _hit("c")]})
        trace = AgentLoop(FakeLLM(["ANSWER: x"]), tools,
                          budget=Budget(max_rounds=2, max_evidence=2)).run("q")
        assert trace.final["evidence_ids"] == ["a", "b"]
        assert trace.evidence_ids() == ["a", "b"]
        # 命中记录仍完整（轮次里三个都记着）——但**被展示的**只有两个。
        assert trace.rounds[0].added_ids == ["a", "b", "c"]

    def test_legacy_trace_without_final_evidence_falls_back(self):
        trace = AgentLoop(FakeLLM(["ANSWER: x"]),
                          FakeTools({"q": [_hit("a")]})).run("q")
        legacy = trace.to_dict()
        legacy["final"].pop("evidence_ids")
        from memory_agent.trace import Trace

        assert Trace.from_dict(legacy).evidence_ids() == ["a"]

    def test_round_added_ids_derived_from_tool_calls(self):
        trace = AgentLoop(FakeLLM(["ANSWER: x"]),
                          FakeTools({"q": [_hit("a"), _hit("b")]})).run("q")
        round_ = trace.rounds[0]
        assert round_.tool_calls[0].added_ids == ["a", "b"]
        assert round_.added_ids == ["a", "b"]
        # JSONL 里不再单独存一份 added_ids（同一事实两个来源）。
        assert "added_ids" not in json.loads(json.dumps(trace.to_dict()))["rounds"][0]


class TestToolDispatch:
    class Registry:
        def __init__(self):
            self.calls = []

        def list_tools(self):
            return [
                {"name": "memory_search", "description": "检索。",
                 "parameters": {"type": "object",
                                "properties": {"query": {"type": "string"}},
                                "required": ["query"]}},
                {"name": "memory_get", "description": "按 id 读回。",
                 "parameters": {"type": "object",
                                "properties": {"entry_id": {"type": "string"}},
                                "required": ["entry_id"]}},
            ]

        def call(self, name, args):
            self.calls.append((name, args))
            if name == "memory_search":
                return [_hit("a")]
            if name == "memory_get":
                return {"id": "a", "title": "A", "content": "BODY"}
            raise KeyError(f"未知工具：{name}")

    def test_named_tool_is_reachable_and_result_is_fed_back(self):
        registry = self.Registry()
        llm = FakeLLM(['TOOL: memory_get {"entry_id": "a"}', "ANSWER: BODY"])
        trace = AgentLoop(llm, registry, budget=Budget(max_rounds=3)).run("q", trace_id="t")
        assert [name for name, _ in registry.calls] == ["memory_search", "memory_get"]
        assert trace.stop.trigger == STOP_ANSWER
        # 派发那一轮记下两次工具调用；**下一轮不再重复检索**（同 query 重搜只会 no_new_ids）。
        assert [call.tool for call in trace.rounds[0].tool_calls] == ["memory_search", "memory_get"]
        assert trace.rounds[1].tool_calls == []
        assert "BODY" in llm.calls[1][-1]["content"]
        assert trace.evidence_ids() == ["a"]

    def test_unknown_tool_does_not_crash(self):
        registry = self.Registry()
        llm = FakeLLM(["TOOL: nope {}", "ANSWER: done"])
        trace = AgentLoop(llm, registry, budget=Budget(max_rounds=3)).run("q")
        assert trace.stop.trigger == STOP_ANSWER
        assert "未知工具" in llm.calls[1][-1]["content"]
        assert trace.rounds[0].tool_calls[1].result_count == 0

    def test_tool_descriptions_are_advertised(self):
        registry = self.Registry()
        llm = FakeLLM(["ANSWER: x"])
        AgentLoop(llm, registry).run("q")
        prompt = llm.calls[0][-1]["content"]
        assert "memory_get(entry_id*)" in prompt
        assert describe_tools(registry).count("memory_") == 2


class TestRunHash:
    """`run_hash` 是 ADR-0030 D2 的确定性锚点：内容同 → hash 同（`id` 随机不算内容）。"""

    def _trace(self, trace_id):
        outputs = ["NEXT_QUERY: q2", "ANSWER: done"]
        tools = FakeTools({"q": [_hit("a")], "q2": [_hit("b")]})
        return AgentLoop(FakeLLM(list(outputs)), tools,
                         budget=Budget(max_rounds=3)).run("q", trace_id=trace_id)

    def test_same_content_same_hash_despite_different_ids(self):
        first, second = self._trace("t1"), self._trace("t2")
        assert first.id != second.id
        assert first.run_hash() == second.run_hash()
        assert first.meta["run_hash"] == first.run_hash()

    def test_hash_changes_with_query(self):
        assert self._trace("t1").run_hash() != AgentLoop(
            FakeLLM(["ANSWER: done"]), FakeTools({"other": []})).run("other").run_hash()

    def test_seal_is_idempotent(self):
        trace = self._trace("t1")
        before = trace.run_hash()
        assert seal_trace(trace).run_hash() == before

    def test_hash_survives_round_trip(self, tmp_path):
        trace = self._trace("t1")
        path = str(tmp_path / "trace.jsonl")
        append_trace(path, trace)
        loaded = read_traces(path)[0]
        assert loaded.meta["run_hash"] == trace.meta["run_hash"]
        assert loaded.run_hash() == trace.run_hash()

    def test_compute_matches_method(self):
        trace = self._trace("t1")
        assert compute_run_hash(trace) == trace.run_hash()


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

    def test_list_tools_shape(self):
        from memory_agent.agent_loop.tools import MemoryToolRegistry

        names = [tool["name"] for tool in MemoryToolRegistry(self._FakeIndex()).list_tools()]
        assert names == ["memory_search", "memory_get"]


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

    def test_partial_override_keeps_env_provider(self, monkeypatch):
        """只覆盖 model 时**不得**把环境里的 provider 吃掉（ProviderSpec.provider 默认 None）。"""
        monkeypatch.setenv("MEMORY_AGENT_LLM_PROVIDER", "openai-compat")
        monkeypatch.setenv("MEMORY_AGENT_LLM_BASE_URL", "https://example/v1")
        spec = resolve_provider(ProviderSpec(model="qwen3.7-flash"))
        assert spec.provider == "openai-compat"
        assert spec.base_url == "https://example/v1"
        assert spec.model == "qwen3.7-flash"

    def test_api_key_never_in_repr(self):
        assert "s3cr3t" not in repr(ProviderSpec(api_key="s3cr3t"))

    def test_openai_compat_errors_name_the_env_knob(self):
        """错误信息报**变量名**、不回显值（AGENTS.md 安全条）：顺便锁住 settings 登记表。"""
        from memory_agent.agent_loop.llm import OpenAICompatClient

        with pytest.raises(ValueError) as missing_url:
            OpenAICompatClient(base_url=None, model="qwen3.7-flash")
        assert "MEMORY_AGENT_LLM_BASE_URL" in str(missing_url.value)

        with pytest.raises(ValueError) as missing_model:
            OpenAICompatClient(base_url="https://example/v1", model=None)
        assert "MEMORY_AGENT_LLM_MODEL" in str(missing_model.value)


class TestOpencodeServerClient:
    """接入契约的**离线回归**（真实 server 冒烟见 eval/opencode_server_smoke_62.py）。"""

    def _client(self, captured, *, model=None, payload=None, status=200):
        def handler(request):
            captured.append(request)
            if request.url.path == "/session":
                return httpx.Response(200, json={"id": "ses_test"})
            return httpx.Response(status, json=payload or {
                "info": {"modelID": "m"},
                "parts": [{"type": "step-start"},
                          {"type": "text", "text": "可以"},
                          {"type": "step-finish"}],
            })

        return OpencodeServerClient(base_url="http://127.0.0.1:4096", model=model,
                                    transport=httpx.MockTransport(handler))

    def test_sends_tools_as_map_and_extracts_text(self):
        captured = []
        client = self._client(captured)
        text = client.complete([{"role": "system", "content": "S"},
                                {"role": "user", "content": "U"}])
        assert text == "可以"
        body = json.loads(captured[-1].content)
        # 回归：`tools` 必须是 map（传 [] 会被 opencode 判 400 BadRequest）。
        assert body["tools"] == {"*": False}
        assert isinstance(body["tools"], dict)
        assert body["system"] == "S"
        assert "user: U" in body["parts"][0]["text"]
        assert "model" not in body

    def test_session_created_once_and_reused(self):
        captured = []
        client = self._client(captured)
        client.complete([{"role": "user", "content": "one"}])
        client.complete([{"role": "user", "content": "two"}])
        paths = [request.url.path for request in captured]
        assert paths.count("/session") == 1
        assert paths.count("/session/ses_test/message") == 2

    def test_model_requires_provider_slash_model(self):
        with pytest.raises(ValueError):
            OpencodeServerClient(model="qwen3.7-flash")

    def test_model_body_is_split(self):
        client = OpencodeServerClient(model="dashscope/qwen3.7-flash")
        assert client._model_body == {"providerID": "dashscope", "modelID": "qwen3.7-flash"}

    def test_http_error_becomes_actionable_runtime_error(self):
        captured = []
        client = self._client(captured, payload={"name": "BadRequest"},
                              status=400)
        with pytest.raises(RuntimeError) as excinfo:
            client.complete([{"role": "user", "content": "x"}])
        assert "opencode serve" in str(excinfo.value)
        assert "400" in str(excinfo.value)
