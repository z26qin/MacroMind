"""End-to-end quality gate for retrieval, grounding, PIT, and calibration."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from evidence_store import DEFAULT_SEED_PATH, EvidenceRecord, EvidenceStore
from rag_signal import compute_rag_signal, decision_timestamp

from .calibration import brier_score, expected_calibration_error
from .grounding import grounding_rate
from .pit import assert_no_lookahead
from .retrieval_metrics import hit_rate_at_k, mrr, ndcg_at_k, recall_at_k


DEFAULT_GOLDEN_PATH = Path("eval_data") / "pit_narrative_golden_set.jsonl"


@dataclass(frozen=True)
class EvalThresholds:
    hit_rate_at_3: float = 1.0
    recall_at_3: float = 1.0
    mrr: float = 1.0
    ndcg_at_3: float = 1.0
    grounding_rate: float = 1.0
    max_brier_score: float = 0.10
    max_calibration_error: float = 0.35


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _future_canary() -> EvidenceRecord:
    return EvidenceRecord.from_dict(
        {
            "evidence_id": "future-leakage-canary",
            "event_time": "2099-01-01T00:00:00Z",
            "observed_at": "2099-01-01T00:01:00Z",
            "source": "ci_canary",
            "revision": "1",
            "vintage": "2099-01-01",
            "country": "United States of America",
            "asset": "equity",
            "horizon": "3m",
            "title": "Future canary",
            "content": "This future record must never be retrieved by a historical query.",
            "source_uri": "ci://future-canary",
        }
    )


def run_evaluations(
    golden_path: Path = DEFAULT_GOLDEN_PATH,
    seed_path: Path = DEFAULT_SEED_PATH,
) -> dict[str, float]:
    cases = _load_jsonl(golden_path)
    with EvidenceStore(":memory:") as store:
        store.ingest_jsonl(seed_path)
        store.put(_future_canary())

        hits: list[float] = []
        recalls: list[float] = []
        ndcgs: list[float] = []
        rankings = []
        grounding_scores: list[float] = []
        confidences: list[float] = []
        outcomes: list[int] = []

        for case in cases:
            cutoff = decision_timestamp(case["decision_time"])
            records = store.query(
                country=case["country"],
                asset=case["asset"],
                horizon=case["horizon"],
                as_of=cutoff,
                limit=3,
            )
            assert_no_lookahead(records, cutoff)
            retrieved = [record.evidence_id for record in records]
            gold = set(case["gold_evidence_ids"])
            hits.append(hit_rate_at_k(retrieved, gold, 3))
            recalls.append(recall_at_k(retrieved, gold, 3))
            ndcgs.append(ndcg_at_k(retrieved, gold, 3))
            rankings.append((retrieved, gold))

            result = compute_rag_signal(
                case["country"],
                case["asset"],
                store=store,
                as_of=case["decision_time"],
                horizon=case["horizon"],
            )
            excerpts = [item["excerpt"] for item in result["analysis"]["citations"]]
            source_text = "\n".join(record.content for record in records)
            grounding_scores.append(grounding_rate(excerpts, source_text))
            confidences.append(float(result["confidence"]))
            outcomes.append(int(result["analysis"]["direction"] == case["expected_direction"]))

    mean = lambda values: sum(values) / len(values) if values else 0.0
    return {
        "cases": float(len(cases)),
        "hit_rate_at_3": mean(hits),
        "recall_at_3": mean(recalls),
        "mrr": mrr(rankings),
        "ndcg_at_3": mean(ndcgs),
        "grounding_rate": mean(grounding_scores),
        "brier_score": brier_score(confidences, outcomes),
        "calibration_error": expected_calibration_error(confidences, outcomes, bins=5),
    }


def assert_thresholds(metrics: dict[str, float], thresholds: EvalThresholds) -> None:
    failures = []
    configured = asdict(thresholds)
    for metric in ("hit_rate_at_3", "recall_at_3", "mrr", "ndcg_at_3", "grounding_rate"):
        if metrics[metric] < configured[metric]:
            failures.append(f"{metric}={metrics[metric]:.4f} < {configured[metric]:.4f}")
    if metrics["brier_score"] > thresholds.max_brier_score:
        failures.append(
            f"brier_score={metrics['brier_score']:.4f} > {thresholds.max_brier_score:.4f}"
        )
    if metrics["calibration_error"] > thresholds.max_calibration_error:
        failures.append(
            "calibration_error="
            f"{metrics['calibration_error']:.4f} > {thresholds.max_calibration_error:.4f}"
        )
    if failures:
        raise AssertionError("Evaluation gate failed: " + "; ".join(failures))


def main() -> None:
    metrics = run_evaluations()
    print(json.dumps(metrics, indent=2, sort_keys=True))
    assert_thresholds(metrics, EvalThresholds())


if __name__ == "__main__":
    main()
