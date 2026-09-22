"""harness 单测：Runner / scorer / stats / replay（全用假 LLM+工具，不加载模型）。"""
import pytest

from memory_agent.agent_loop.budget import Budget
from memory_agent.eval.harness import (
    ReplayLLM,
    Runner,
    answer_matches,
    bootstrap_ci,
    evaluate,
    paired_diffs,
    replay,
    score_trace,
    traces_equal,
)
from memory_agent.agent_loop.loop import AgentLoop

pytestmark = pytest.mark.filterwarnings("ignore")


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append(messages)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


class FakeTools:
    def __init__(self, by_query):
        self.by_query = by_query

    def call(self, name, args):
        return [{"id": i, "title": i, "snippet": f"snip {i}"}
                for i in self.by_query.get(args["query"], [])]


def _hit(entry_id):
    return {"id": entry_id, "title": entry_id, "snippet": entry_id}


class TestRunner:
    def test_runs_scenarios_and_writes_trace(self, tmp_path):
        runner = Runner(
            FakeLLM(["ANSWER: 42"]),
            lambda scenario: FakeTools({scenario["query"]: ["a"]}),
            budget=Budget(max_hops=2),
        )
        path = str(tmp_path / "traces.jsonl")
        traces = runner.run([{"id": "s1", "query": "q1"}, {"id": "s2", "query": "q2"}],
                            trace_path=path)
        assert [t.id for t in traces] == ["s1", "s2"]
        assert all(t.stop.trigger == "answer" for t in traces)
        with open(path, encoding="utf-8") as handle:
            assert sum(1 for _ in handle) == 2


class TestScorer:
    def test_score_trace_recall_and_correct(self):
        loop = AgentLoop(FakeLLM(["ANSWER: Paris"]),
                         FakeTools({"q": ["a", "b"]}), budget=Budget(max_hops=2))
        trace = loop.run("q", trace_id="q1")
        row = score_trace(trace, {"relevant": ["a", "b", "c"], "gold_answer": "paris"})
        assert row["evidence_recall"] == pytest.approx(2 / 3)
        assert row["answer_correct"] is True
        assert row["gold_unreached"] is True

    def test_no_answer_correct_when_no_fabrication(self):
        loop = AgentLoop(FakeLLM(["INSUFFICIENT"]), FakeTools({"q": []}))
        row = score_trace(loop.run("q", trace_id="n1"), {"kind": "no_answer"})
        assert row["answer_correct"] is True
        assert row["evidence_recall"] is None

    def test_evaluate_groups_by_stop(self):
        answer_loop = AgentLoop(FakeLLM(["ANSWER: x"]), FakeTools({"a": ["1"]}))
        insuff_loop = AgentLoop(FakeLLM(["INSUFFICIENT"]), FakeTools({"b": []}))
        traces = [answer_loop.run("a", trace_id="t1"),
                  insuff_loop.run("b", trace_id="t2")]
        report = evaluate(traces, {"t1": {"kind": "no_answer"}, "t2": {"kind": "no_answer"}})
        assert set(report["by_stop"]) == {"answer", "insufficient"}
        assert report["by_stop"]["answer"]["n"] == 1
        assert report["by_stop"]["answer"]["gold_unreached_rate"] is None
        assert report["overall"]["n"] == 2

    def test_answer_matches_variants(self):
        assert answer_matches("Paris.", "paris")
        assert answer_matches("the answer is 42", "42")
        assert answer_matches("a b c", "b")
        assert not answer_matches("42", "43")
        assert not answer_matches("", "x")


class TestStats:
    def test_paired_diffs_skips_missing(self):
        a = [{"id": "1", "v": 1.0}, {"id": "2", "v": 0.5}, {"id": "3", "v": None}]
        b = [{"id": "1", "v": 0.0}, {"id": "3", "v": 1.0}]
        assert paired_diffs(a, b, "v") == [1.0]

    def test_bootstrap_detects_positive_shift(self):
        report = bootstrap_ci([0.2] * 50, n_boot=2000, seed=1)
        assert report["significant"] is True
        assert report["lo"] > 0

    def test_bootstrap_zero_centered_not_significant(self):
        report = bootstrap_ci([-1.0, 1.0] * 25, n_boot=2000, seed=1)
        assert report["significant"] is False

    def test_bootstrap_empty(self):
        assert bootstrap_ci([]) == {"n": 0, "mean": None, "lo": None, "hi": None,
                                    "significant": None}


class TestReplay:
    def test_replay_reproduces_trace(self):
        outputs = ["NEXT_QUERY: q2", "ANSWER: done"]
        tools = FakeTools({"q": ["a"], "q2": ["b"]})
        original = AgentLoop(FakeLLM(list(outputs)), tools,
                             budget=Budget(max_hops=3)).run("q", trace_id="t1")
        replayed = replay(original, tools)
        assert traces_equal(original, replayed)
        assert replayed.evidence_ids() == original.evidence_ids()

    def test_replay_llm_exhausted_raises(self):
        with pytest.raises(RuntimeError):
            ReplayLLM([]).complete([])
