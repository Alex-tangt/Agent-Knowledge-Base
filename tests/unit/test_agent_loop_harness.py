"""harness 单测：Runner / scorer / stats / replay / 场景集 / 报告（全用包里 stub，零模型）。

假件（脚本 LLM + 回放工具）来自**包内** `memory_agent.eval.harness.stubs`，
不在测试里再写一份；包**不**反向 import `tests/`。
"""
import json
import subprocess
import sys

import pytest

from memory_agent.agent_loop.budget import Budget
from memory_agent.agent_loop.loop import AgentLoop
from memory_agent.eval.harness import (
    OUTCOME_CLASSES,
    ReplayLLM,
    Runner,
    ScenarioSetError,
    ScriptedLLM,
    StubTools,
    answer_matches,
    bootstrap_ci,
    budget_for,
    budget_from_trace,
    dev_ids,
    evaluate,
    holdout_ids,
    load_scenarios,
    paired_diffs,
    replay,
    score_trace,
    split_scenarios,
    stub_llm,
    stub_tools,
    traces_equal,
    validate_file,
    validate_scenarios,
)
from memory_agent.eval.harness.__main__ import build_report, main, markdown, stable_seed
from memory_agent.trace import STOP_TRIGGERS, Trace, append_trace, read_traces

pytestmark = pytest.mark.filterwarnings("ignore")


class FakeLLM(ScriptedLLM):
    """包内 `ScriptedLLM`（脚本回放）；保留本名以对齐运行时单测的习惯叫法。"""


class FakeTools(StubTools):
    """包内 `StubTools` 的 dict 便捷构造：`{query: [ids]}`。"""

    def __init__(self, by_query):
        super().__init__(by_query)


def _hit(entry_id):
    return {"id": entry_id, "title": entry_id, "snippet": entry_id}


def _defaults():
    """场景集 `defaults`（预算缺省）；测试跑单条场景时也要按它构造预算，
    否则 `max_evidence` 会退回硬缺省 20，与报告口径不一致。"""
    return dict(load_scenarios().get("defaults") or {})


def _run(scenario, *, k=None):
    """按 committed 场景的 stub 跑一遍，返回 trace（与报告路径同口径）。"""
    defaults = _defaults()
    return AgentLoop(
        stub_llm(scenario), stub_tools(scenario),
        budget=budget_for(scenario, defaults=defaults),
        k=int(defaults.get("k", 5) if k is None else k),
    ).run(scenario["query"], trace_id=scenario["id"])


def _scenario(scenario_id):
    for scenario in load_scenarios()["scenarios"]:
        if scenario["id"] == scenario_id:
            return scenario
    raise KeyError(scenario_id)


def _payload(**overrides):
    """一份最小合规场景集（改一处即可造出非法变体）。"""
    base = {
        "schema_version": 1,
        "defaults": {"k": 5, "max_hops": 3, "max_evidence": 3},
        "scenarios": [
            {"id": "s01-a", "query": "q1", "split": "dev", "kind": "answer",
             "relevant": ["g"], "gold_answer": "g",
             "stub_evidence": {"q1": ["g"]}, "script": ["ANSWER: g"]},
            {"id": "s06-b", "query": "q2", "split": "holdout", "kind": "answer",
             "relevant": ["g"], "gold_answer": "g",
             "stub_evidence": {"q2": ["g"]}, "script": ["ANSWER: g"]},
        ],
    }
    base.update(overrides)
    return base


class TestRunner:
    def test_runs_scenarios_and_writes_trace(self, tmp_path):
        runner = Runner(
            FakeLLM(["ANSWER: 42"]),
            lambda scenario: FakeTools({scenario["query"]: ["a"]}),
            budget=Budget(max_rounds=2),
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
                         FakeTools({"q": ["a", "b"]}), budget=Budget(max_rounds=2))
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
                             budget=Budget(max_rounds=3)).run("q", trace_id="t1")
        replayed = replay(original, tools)
        assert traces_equal(original, replayed)
        assert replayed.evidence_ids() == original.evidence_ids()

    def test_replay_llm_exhausted_raises(self):
        with pytest.raises(RuntimeError):
            ReplayLLM([]).complete([])

    def test_budget_from_trace_reads_both_spellings(self):
        """契约改名 `max_hops` → `max_rounds`：两种键都要能读回（新键优先）。"""
        old = Trace(id="t", query="q", meta={"max_hops": 5, "max_evidence": 7})
        new = Trace(id="t", query="q", meta={"max_rounds": 5, "max_evidence": 7})
        for trace in (old, new):
            budget = budget_from_trace(trace)
            assert budget.max_rounds == 5
            assert budget.max_evidence == 7
        assert budget_from_trace(Trace(id="t", query="q")).max_rounds == 3


class TestStubs:
    def test_scripted_llm_repeats_last_and_records(self):
        llm = FakeLLM(["NEXT_QUERY: q2", "ANSWER: done"])
        assert llm.complete([]) == "NEXT_QUERY: q2"
        assert llm.complete([]) == "ANSWER: done"
        assert llm.complete([]) == "ANSWER: done"  # 用尽后重复最后一条（预算场景要用）
        assert len(llm.calls) == 3

    def test_scripted_llm_empty_script_raises(self):
        with pytest.raises(RuntimeError):
            FakeLLM([]).complete([])

    def test_stub_tools_replay_and_empty_for_unknown(self):
        tools = StubTools({"q": ["a", "b"]})
        assert [item["id"] for item in tools.call("memory_search", {"query": "q"})] == ["a", "b"]
        assert tools.call("memory_search", {"query": "unknown"}) == []
        with pytest.raises(KeyError):
            tools.call("memory_get", {"entry_id": "a"})
        # `stub_tools(scenario)` 从场景的 `stub_evidence` 取映射（不是直接收 `{query: ids}`）
        assert stub_tools({"stub_evidence": {"q": ["a"]}}).call(
            "memory_search", {"query": "q"})[0]["id"] == "a"
        with pytest.raises(RuntimeError):
            ScriptedLLM([]).complete([])

    def test_stub_llm_and_budget_for(self):
        assert stub_llm({"script": ["ANSWER: x"]}).complete([]) == "ANSWER: x"
        assert budget_for({"max_hops": 2, "max_evidence": 1}).max_rounds == 2
        assert budget_for({}).max_rounds == 3
        # 场景级覆盖 > 场景集 defaults > 硬缺省
        defaults = {"max_hops": 4, "max_evidence": 2}
        assert budget_for({}, defaults=defaults).max_evidence == 2
        assert budget_for({"max_evidence": 9}, defaults=defaults).max_evidence == 9
        assert budget_for({}, defaults=defaults).max_rounds == 4


class TestScenarioSet:
    def test_committed_set_is_valid(self):
        assert validate_scenarios(load_scenarios()) == []

    def test_committed_set_shape(self):
        payload = validate_file()
        scenarios = payload["scenarios"]
        assert len(scenarios) >= 8
        assert SCENARIO_FIELDS <= set(scenarios[0])
        ids = [scenario["id"] for scenario in scenarios]
        assert len(ids) == len(set(ids)), "场景 id 必须唯一"
        assert {scenario["split"] for scenario in scenarios} == {"dev", "holdout"}
        assert len(scenarios) == len({scenario["query"] for scenario in scenarios})
        for scenario in scenarios:
            expect = (scenario.get("expect") or {}).get("stop")
            if expect is not None:
                assert expect in STOP_TRIGGERS
            assert scenario["script"], f"{scenario['id']} 缺 script"
            assert scenario["stub_evidence"], f"{scenario['id']} 缺 stub_evidence"

    def test_committed_set_covers_acceptance_outcomes(self):
        """验收②点名的结局类别都要有场景，且 stop 覆盖齐全。"""
        from memory_agent.eval.harness.scenarios import CLASS_PREFIXES

        scenarios = load_scenarios()["scenarios"]
        covered = {CLASS_PREFIXES[prefix] for prefix in CLASS_PREFIXES
                   for scenario in scenarios if scenario["id"].startswith(prefix)}
        assert covered == set(OUTCOME_CLASSES)
        expected_stops = {(scenario.get("expect") or {}).get("stop")
                          for scenario in scenarios if scenario.get("expect")}
        assert expected_stops == set(STOP_TRIGGERS)
        kinds = {scenario["kind"] for scenario in scenarios}
        assert kinds == {"answer", "no_answer"}

    @pytest.mark.parametrize("mutate,needle", [
        (lambda p: p["scenarios"][0].pop("split"), "缺必填字段 split"),
        (lambda p: p["scenarios"][1].update(id=p["scenarios"][0]["id"]), "id 重复"),
        (lambda p: p["scenarios"][0].update(query=p["scenarios"][1]["query"]), "重复"),
        (lambda p: p["scenarios"][0].update(split=""), "split 必须是非空字符串"),
        (lambda p: p["scenarios"][0].update(kind="maybe"), "kind 必须是"),
        (lambda p: p["scenarios"][0].update(acceptance="answer_on_first_hop"), "未知字段"),
        (lambda p: p["scenarios"][0].update(script=[]), "script 必须是非空字符串列表"),
        (lambda p: p["scenarios"][0].update(stub_evidence={}), "stub_evidence 必须是非空对象"),
        (lambda p: p["scenarios"][0].update(relevant=[]), "kind=answer 必须有非空 relevant"),
        (lambda p: p["scenarios"][0].pop("gold_answer"), "必须有非空 gold_answer"),
        (lambda p: p["scenarios"][0].update(max_hops=0), "max_hops 必须是 >=1 的整数"),
        (lambda p: p["scenarios"][0].update(expect={"stop": "nope"}), "不是合法停止触发词"),
        (lambda p: p.update(schema_version=99), "schema_version"),
        (lambda p: p.update(scenarios=[]), "scenarios 必须是非空列表"),
        (lambda p: p["scenarios"].pop(1), "split 必须至少两个"),
        (lambda p: [s.update(split="dev") for s in p["scenarios"]], "必须有 holdout"),
    ])
    def test_invalid_sets_are_rejected(self, mutate, needle):
        payload = _payload()
        mutate(payload)
        errors = validate_scenarios(payload)
        assert any(needle in error for error in errors), errors

    def test_no_answer_must_not_carry_gold(self):
        payload = _payload()
        payload["scenarios"][0].update(kind="no_answer")
        errors = validate_scenarios(payload)
        assert any("不该有 gold_answer" in error for error in errors)
        assert any("relevant 必须为空" in error for error in errors)

    def test_missing_hop_query_registration_is_rejected(self):
        payload = _payload()
        payload["scenarios"][0]["stub_evidence"] = {"other": ["g"]}
        assert any("缺首跳 query" in error for error in validate_scenarios(payload))

    def test_validate_file_raises_with_all_errors(self):
        payload = _payload()
        payload["scenarios"][0].pop("split")
        payload["scenarios"][0]["expect"] = {"stop": "nope"}
        with pytest.raises(ScenarioSetError) as excinfo:
            _write_and_validate(payload)
        assert "split" in str(excinfo.value) and "expect.stop" in str(excinfo.value)

    def test_load_missing_file_raises(self, tmp_path):
        with pytest.raises(ScenarioSetError):
            load_scenarios(tmp_path / "nope.json")

    def test_load_bad_json_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ScenarioSetError):
            load_scenarios(path)


SCENARIO_FIELDS = {"id", "query", "split", "kind", "relevant", "stub_evidence", "script"}


def _write_and_validate(payload):
    from memory_agent.eval.harness.scenarios import validate_scenarios as validate

    errors = validate(payload)
    if errors:
        raise ScenarioSetError("；".join(errors))
    return payload


class TestHoldoutRule:
    def test_split_partition_is_explicit_and_disjoint(self):
        scenarios = load_scenarios()["scenarios"]
        dev = dev_ids(scenarios)
        holdout = holdout_ids(scenarios)
        grouped = split_scenarios(scenarios)
        assert dev and holdout
        assert not set(dev) & set(holdout)
        assert set(dev) | set(holdout) == {scenario["id"] for scenario in scenarios}
        assert sorted(grouped) == ["dev", "holdout"]
        assert grouped["holdout"] == [s for s in scenarios if s["split"] == "holdout"]

    def test_holdout_rule_is_documented(self):
        """留出规则必须写在 docstring 里（可被人和测试检索到）。"""
        import memory_agent.eval.harness.scenarios as module

        doc = module.__doc__ or ""
        assert "留出" in doc and "holdout" in doc
        assert "dev" in doc
        assert "调参" in doc

    def test_report_headline_number_is_holdout(self):
        report = build_report()
        assert report.splits["holdout"]["n"] == len(holdout_ids(load_scenarios()["scenarios"]))
        assert report.analyses[0].metric.startswith("holdout.")
        assert report.bootstrap["seed"] == report.bootstrap["seed"]  # 固定值，非随机
        assert any("只报 holdout" in note for note in report.notes)


class TestReportEndToEnd:
    def test_committed_set_produces_report(self):
        report = build_report()
        assert report.schema_version == 1
        assert report.counts["total"] == len(load_scenarios()["scenarios"])
        assert report.counts["by_split"] == {"dev": 3, "holdout": 7}
        holdout = report.splits["holdout"]
        assert holdout["expectation_mismatch"] == []
        assert holdout["expectation_matched"] == holdout["expectation_total"] > 0
        # ①每个 stop 触发词都有切片 ②每个场景都落到了 expect 声明的 stop
        expected_stops = {(s.get("expect") or {}).get("stop")
                          for s in load_scenarios()["scenarios"] if s.get("expect")}
        assert set(holdout["by_stop"]) == expected_stops
        assert holdout["overall"]["n"] == 7
        assert holdout["by_stop"]["budget"]["n"] == 2
        assert holdout["by_stop"]["no_new_ids"]["n"] == 1
        assert holdout["by_stop"]["fallback"]["answer_correct_rate"] == 0.0

    def test_per_stop_breakdown_matches_hand_count(self):
        report = build_report()
        rows = report.splits["holdout"]["by_stop"]["answer"]
        assert rows["n"] == 2
        assert rows["gold_unreached_rate"] == 1.0  # 两个 answer 场景都没搜齐 gold

    def test_ci_is_a_real_bootstrap(self):
        """留出集正确率有真实方差 → CI 非零宽、区间含 mean、不含 0 不成立。"""
        from memory_agent.eval.harness.scenarios import eval_set, split_scenarios

        scenarios = split_scenarios(load_scenarios()["scenarios"])["holdout"]
        rows = [score_trace(_run(scenario), eval_set(scenarios)[scenario["id"]])
                for scenario in scenarios]
        flags = [1.0 if row["answer_correct"] else 0.0
                 for row in rows if row["answer_correct"] is not None]
        # 逐场景钉住**正确与否**（行序由文件顺序决定）
        by_id = {row["id"]: row for row in rows}
        expected = {
            "s04-budget-stop-partial": False,
            "s05-budget-stop-no-answer": True,
            "s06-no-new-ids-stop": True,
            "s07-fallback-stop": False,
            "s08-insufficient-must-not-answer": True,
            "s09-gold-unreached-max-evidence": True,
            "s10-gold-unreached-screening": True,
        }
        assert set(by_id) == set(expected)
        assert {key: by_id[key]["answer_correct"] for key in expected} == expected
        assert [by_id[scenario["id"]]["stop"] for scenario in scenarios] == [
            "budget", "budget", "no_new_ids", "fallback", "insufficient", "answer", "answer"]
        # 留出集 7 题里 5 对 2 错 → 有真实方差（不是 0/1 退化）
        assert flags == [0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 1.0]
        ci = bootstrap_ci(flags, n_boot=2000, seed=stable_seed("holdout", rows))
        assert ci["mean"] == pytest.approx(5 / 7)
        assert ci["lo"] < ci["mean"] < ci["hi"]  # 有真实方差 → 非零宽
        # `significant` = 区间不含 0（不是「优于某基线」——本报告没有对照组）
        assert ci["significant"] is True and ci["lo"] > 0
        # 报告里的主结论应等于同口径的 95% CI（n_boot 默认 10000）
        primary = build_report().analyses[0]
        assert primary.n == 7
        assert primary.mean == pytest.approx(5 / 7, abs=1e-6)
        assert primary.degenerate is False
        assert primary.lo == pytest.approx(2 / 7, abs=1e-6)
        assert primary.hi == 1.0
        assert primary.lo < primary.mean < primary.hi

    def test_cli_writes_same_bytes_to_out(self, tmp_path, capsys):
        out = tmp_path / "report.json"
        assert main(["--out", str(out)]) == 0
        printed = capsys.readouterr().out
        assert out.read_text(encoding="utf-8") == printed
        assert json.loads(printed)["splits"]["holdout"]["overall"]["n"] == 7

    def test_cli_markdown_is_rendered_from_same_report(self, capsys):
        assert main(["--format", "md"]) == 0
        text = capsys.readouterr().out
        assert text == markdown(build_report())
        assert "bootstrap CI" in text

    def test_cli_rejects_invalid_set(self, tmp_path, capsys):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(_payload(schema_version=7)), encoding="utf-8")
        assert main([str(path)]) == 2
        assert "schema_version" in capsys.readouterr().err

    def test_report_is_deterministic_across_runs(self, capsys):
        assert main([]) == 0
        first = capsys.readouterr().out
        assert main([]) == 0
        second = capsys.readouterr().out
        assert first == second
        assert first.encode("utf-8") == second.encode("utf-8")

    def test_deterministic_subprocess_runs(self, tmp_path):
        """跨进程也逐字节相同（不含 PYTHONHASHSEED 之类的影响）。"""
        out1, out2 = tmp_path / "a.json", tmp_path / "b.json"
        for out in (out1, out2):
            result = subprocess.run(
                [sys.executable, "-m", "memory_agent.eval.harness", "--out", str(out)],
                capture_output=True, text=True, encoding="utf-8",
            )
            assert result.returncode == 0, result.stderr
        assert out1.read_bytes() == out2.read_bytes()

    def test_traces_are_trace_contract_objects(self):
        """报告路径产出的确实是 `memory_agent.trace.Trace`，且带 run_hash。"""
        for scenario in load_scenarios()["scenarios"]:
            trace = _run(scenario)
            assert isinstance(trace, Trace)
            assert trace.stop.trigger in STOP_TRIGGERS
            assert trace.evidence_ids() == trace.final["evidence_ids"]
            assert isinstance(trace.run_hash(), str) and len(trace.run_hash()) == 64
            # 展示给模型的证据受 max_evidence 上限约束（场景集 defaults.max_evidence=3）
            assert len(trace.evidence_ids()) <= _defaults()["max_evidence"]


class TestDeterminism:
    def test_two_runs_identical_report(self):
        assert build_report().to_json() == build_report().to_json()

    def test_identical_runs_share_run_hash(self):
        """契约：两条内容相同的轨迹 → 同一 `run_hash`（且与随机 `id` 无关）。"""
        scenario = _scenario("s01-answer-first-hop")
        first = _run(scenario)
        second = AgentLoop(stub_llm(scenario), stub_tools(scenario),
                           budget=budget_for(scenario, defaults=_defaults()),
                           k=int(_defaults().get("k", 5))).run(
            scenario["query"], trace_id="other-trace-id")
        assert first.id != second.id
        assert first.run_hash() == second.run_hash()
        assert first.run_hash() == _run(scenario).run_hash()

    def test_replay_reproduces_committed_scenarios(self):
        """committed 场景集里每条都能被 replay 逐位复现（traces_equal）。"""
        from memory_agent.eval.harness.scenarios import split_scenarios

        scenarios = load_scenarios()["scenarios"]
        for scenario in split_scenarios(scenarios)["holdout"]:
            original = _run(scenario)
            replayed = replay(original, stub_tools(scenario))
            assert traces_equal(original, replayed), scenario["id"]
            assert replayed.evidence_ids() == original.evidence_ids()
            assert replayed.run_hash() == original.run_hash()

    def test_trace_round_trip_keeps_contract(self, tmp_path):
        """落盘 → 读回：`final.evidence_ids` 与 stop 不变（契约面）。"""
        trace = _run(load_scenarios()["scenarios"][0])
        path = str(tmp_path / "trace.jsonl")
        append_trace(path, trace)
        loaded = read_traces(path)
        assert len(loaded) == 1
        assert loaded[0].to_dict() == trace.to_dict()
        assert loaded[0].stop.trigger == trace.stop.trigger
        assert loaded[0].evidence_ids() == trace.evidence_ids()
        assert loaded[0].run_hash() == trace.run_hash()
