"""Confidence calibration metrics for directional narrative calls."""
from __future__ import annotations

from collections.abc import Sequence


def _validate(confidences: Sequence[float], outcomes: Sequence[int]) -> None:
    if len(confidences) != len(outcomes):
        raise ValueError("confidences and outcomes must have the same length")
    if any(not 0.0 <= value <= 1.0 for value in confidences):
        raise ValueError("confidences must be in [0, 1]")
    if any(value not in (0, 1) for value in outcomes):
        raise ValueError("outcomes must be binary")


def brier_score(confidences: Sequence[float], outcomes: Sequence[int]) -> float:
    _validate(confidences, outcomes)
    if not confidences:
        return 0.0
    return sum((confidence - outcome) ** 2 for confidence, outcome in zip(confidences, outcomes)) / len(confidences)


def expected_calibration_error(
    confidences: Sequence[float], outcomes: Sequence[int], bins: int = 10
) -> float:
    _validate(confidences, outcomes)
    if bins <= 0:
        raise ValueError("bins must be positive")
    if not confidences:
        return 0.0
    total = len(confidences)
    error = 0.0
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        members = [
            i
            for i, confidence in enumerate(confidences)
            if lower <= confidence < upper or (index == bins - 1 and confidence == 1.0)
        ]
        if not members:
            continue
        mean_confidence = sum(confidences[i] for i in members) / len(members)
        accuracy = sum(outcomes[i] for i in members) / len(members)
        error += len(members) / total * abs(accuracy - mean_confidence)
    return error
