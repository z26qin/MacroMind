import pytest

from evals.calibration import brier_score, expected_calibration_error
from evals.ci import EvalThresholds, assert_thresholds, run_evaluations
from evals.grounding import grounding_rate
from evals.pit import find_lookahead_violations
from evals.ragas_metrics import answer_relevancy, faithfulness
from evals.retrieval_metrics import hit_rate_at_k, mrr, ndcg_at_k, recall_at_k


def test_retrieval_metrics_reward_relevant_first_result():
    retrieved = ["gold", "other"]
    gold = {"gold"}
    assert hit_rate_at_k(retrieved, gold, 1) == 1.0
    assert recall_at_k(retrieved, gold, 1) == 1.0
    assert mrr([(retrieved, gold)]) == 1.0
    assert ndcg_at_k(retrieved, gold, 2) == 1.0


def test_grounding_rate_requires_verbatim_support():
    assert grounding_rate(["policy is cautious", "unsupported claim"], "Policy is cautious.") == 0.5


def test_pit_checks_event_and_observation_timestamps():
    violations = find_lookahead_violations(
        [
            {
                "evidence_id": "late",
                "event_time": "2026-06-01T09:00:00Z",
                "observed_at": "2026-06-01T12:00:00Z",
            }
        ],
        "2026-06-01T10:00:00Z",
    )
    assert [(item.evidence_id, item.field) for item in violations] == [("late", "observed_at")]


def test_calibration_metrics():
    assert brier_score([1.0, 0.0], [1, 0]) == 0.0
    assert expected_calibration_error([1.0, 0.0], [1, 0], bins=2) == 0.0


def test_calibration_rejects_mismatched_inputs():
    with pytest.raises(ValueError):
        brier_score([0.5], [])


def test_generation_metrics_accept_a_judge_protocol():
    class Judge:
        def entails(self, claim, context):
            return claim.casefold() in context.casefold()

        def relevance(self, question, answer):
            return 0.8

    judge = Judge()
    assert faithfulness("Growth improved.", "Growth improved.", judge) == 1.0
    assert answer_relevancy("What changed?", "Growth improved.", judge) == 0.8


def test_end_to_end_eval_gate_passes():
    metrics = run_evaluations()
    assert metrics["cases"] == 6
    assert_thresholds(metrics, EvalThresholds())
