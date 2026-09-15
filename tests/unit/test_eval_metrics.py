"""检索评测指标：recall@k / nDCG@k / MRR / 无答案分流（纯函数，确定性）。"""
import pytest

from memory_agent.eval.metrics import (
    evaluate,
    first_hit_rank,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)

pytestmark = pytest.mark.filterwarnings("ignore")


class TestRecallAtK:
    def test_partial_hit(self):
        assert recall_at_k(["a", "b", "c"], ["b"], 3) == 1.0

    def test_recall_counts_all_relevant(self):
        assert recall_at_k(["a", "x"], ["a", "b"], 2) == 0.5

    def test_k_cutoff(self):
        assert recall_at_k(["x", "a"], ["a"], 1) == 0.0

    def test_empty_relevant_returns_zero(self):
        assert recall_at_k(["a"], [], 3) == 0.0


class TestNdcgAtK:
    def test_perfect_ranking_is_one(self):
        assert ndcg_at_k(["a", "b"], ["a", "b"], 10) == pytest.approx(1.0)

    def test_below_perfect_when_hit_pushed_down(self):
        assert 0.0 < ndcg_at_k(["b", "a"], ["a"], 10) < 1.0

    def test_ideal_discounts_by_hit_count(self):
        # 只有 1 个相关且在第 1 位 = 理想，nDCG = 1
        assert ndcg_at_k(["a"], ["a"], 10) == pytest.approx(1.0)

    def test_end_to_end_known_value(self):
        # 相关在 rank 2：DCG = 1/log2(3)；IDCG(2 命中) = 1 + 1/log2(3)
        expected = (1 / 1.5849625007211563) / (1 + 1 / 1.5849625007211563)
        assert ndcg_at_k(["x", "a"], ["a", "b"], 10) == pytest.approx(expected)


class TestMrrAndFirstHit:
    def test_reciprocal_rank(self):
        assert reciprocal_rank(["x", "a"], ["a"]) == pytest.approx(0.5)

    def test_reciprocal_rank_none_found(self):
        assert reciprocal_rank(["x", "y"], ["a"]) == 0.0

    def test_first_hit_rank_none(self):
        assert first_hit_rank(["x"], ["a"]) is None

    def test_first_hit_rank(self):
        assert first_hit_rank(["x", "y", "a"], ["a"]) == 3


class TestEvaluate:
    def _records(self):
        return [
            {"id": "q1", "query": "hit", "relevant": ["a"],
             "ranked": ["a", "b"], "ranked_scores": [0.9, 0.5]},
            {"id": "q2", "query": "miss", "relevant": ["c"],
             "ranked": ["x", "y"], "ranked_scores": [0.4, 0.3]},
            {"id": "na1", "query": "off-corpus", "relevant": [],
             "ranked": ["x"], "ranked_scores": [0.7]},
        ]

    def test_aggregate_over_answerable_only(self):
        report = evaluate(self._records(), ks=(1, 3, 5, 10), ndcg_k=10)
        agg = report["aggregate"]
        assert agg["queries_total"] == 3
        assert agg["queries_answerable"] == 2
        assert agg["queries_no_answer"] == 1
        assert agg["recall"]["1"] == pytest.approx(0.5)
        assert agg["recall"]["10"] == pytest.approx(0.5)
        assert agg["mrr"] == pytest.approx(0.5)
        assert agg["misses"] == ["q2"]

    def test_no_answer_top1_score_reported(self):
        report = evaluate(self._records())
        assert report["aggregate"]["no_answer_top1_score"] == {
            "count": 1, "mean": 0.7, "max": 0.7,
        }
        assert report["no_answer"] == [{
            "id": "na1", "query": "off-corpus",
            "ranked": ["x"], "ranked_scores": [0.7],
        }]

    def test_deterministic(self):
        a = evaluate(self._records())
        b = evaluate(self._records())
        assert a == b
