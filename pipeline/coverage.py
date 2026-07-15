"""Series-level coverage reports for canonical source batches."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from pipeline.contracts import SourceBatch, SourceStatus
from pipeline.fallback import (
    FallbackAction,
    FallbackDecision,
    FallbackPolicy,
    decide_fallback,
    policy_for_source,
)


def _dimension(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    normalized = tuple(str(value).strip() for value in values)
    if not normalized or any(not value for value in normalized):
        raise ValueError(f"{field_name} must contain non-empty values")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must not contain duplicates")
    return normalized


@dataclass(frozen=True)
class MetricCoverage:
    metric: str
    expected_series_count: int
    observed_series_count: int
    coverage: float
    missing_countries: tuple[str, ...]
    error_count: int
    decision: FallbackDecision

    def to_dict(self) -> dict[str, object]:
        return {
            "metric": self.metric,
            "expected_series_count": self.expected_series_count,
            "observed_series_count": self.observed_series_count,
            "coverage": self.coverage,
            "missing_countries": list(self.missing_countries),
            "error_count": self.error_count,
            "decision": self.decision.to_dict(),
        }


@dataclass(frozen=True)
class CoverageReport:
    run_id: str
    source: str
    batch_status: SourceStatus
    policy: FallbackPolicy
    expected_observation_count: int
    observation_count: int
    raw_observation_coverage: float
    expected_series_count: int
    observed_series_count: int
    series_coverage: float
    metrics: tuple[MetricCoverage, ...]
    error_count: int
    errors_by_code: tuple[tuple[str, int], ...]
    unexpected_series: tuple[tuple[str, str], ...]

    @property
    def actions(self) -> dict[str, FallbackAction]:
        return {metric.metric: metric.decision.action for metric in self.metrics}

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "source": self.source,
            "batch_status": self.batch_status.value,
            "policy": self.policy.to_dict(),
            "expected_observation_count": self.expected_observation_count,
            "observation_count": self.observation_count,
            "raw_observation_coverage": self.raw_observation_coverage,
            "expected_series_count": self.expected_series_count,
            "observed_series_count": self.observed_series_count,
            "series_coverage": self.series_coverage,
            "metrics": [metric.to_dict() for metric in self.metrics],
            "error_count": self.error_count,
            "errors_by_code": dict(self.errors_by_code),
            "unexpected_series": [
                {"country": country, "metric": metric}
                for country, metric in self.unexpected_series
            ],
        }


@dataclass(frozen=True)
class PipelineCoverageReport:
    run_id: str
    sources: tuple[CoverageReport, ...]
    expected_observation_count: int
    observation_count: int
    raw_observation_coverage: float
    expected_series_count: int
    observed_series_count: int
    series_coverage: float
    action_counts: tuple[tuple[str, int], ...]
    error_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "expected_observation_count": self.expected_observation_count,
            "observation_count": self.observation_count,
            "raw_observation_coverage": self.raw_observation_coverage,
            "expected_series_count": self.expected_series_count,
            "observed_series_count": self.observed_series_count,
            "series_coverage": self.series_coverage,
            "action_counts": dict(self.action_counts),
            "error_count": self.error_count,
            "sources": [source.to_dict() for source in self.sources],
        }


def build_coverage_report(
    batch: SourceBatch,
    expected_economies: Iterable[str],
    expected_metrics: Iterable[str],
    policy: FallbackPolicy | None = None,
) -> CoverageReport:
    """Report country×metric coverage without double-counting time periods."""
    economies = _dimension(expected_economies, "expected_economies")
    metrics = _dimension(expected_metrics, "expected_metrics")
    selected_policy = policy or policy_for_source(batch.source)
    if selected_policy.source != batch.source:
        raise ValueError(
            f"Fallback policy source {selected_policy.source!r} does not match batch source {batch.source!r}"
        )

    expected_pairs = {(country, metric) for country in economies for metric in metrics}
    observed_pairs = {(obs.country, obs.metric) for obs in batch.observations}
    covered_pairs = observed_pairs & expected_pairs
    unexpected = tuple(sorted(observed_pairs - expected_pairs))

    metric_reports = []
    for metric in metrics:
        observed_countries = tuple(
            country for country in economies if (country, metric) in covered_pairs
        )
        decision = decide_fallback(
            selected_policy,
            metric,
            economies,
            observed_countries,
        )
        metric_reports.append(
            MetricCoverage(
                metric=metric,
                expected_series_count=len(economies),
                observed_series_count=len(observed_countries),
                coverage=len(observed_countries) / len(economies),
                missing_countries=tuple(
                    country for country in economies if country not in observed_countries
                ),
                error_count=sum(error.metric == metric for error in batch.errors),
                decision=decision,
            )
        )

    expected_series_count = len(expected_pairs)
    observed_series_count = len(covered_pairs)
    errors_by_code = tuple(sorted(Counter(error.code for error in batch.errors).items()))
    return CoverageReport(
        run_id=batch.run_id,
        source=batch.source,
        batch_status=batch.status,
        policy=selected_policy,
        expected_observation_count=batch.expected_observation_count,
        observation_count=len(batch.observations),
        raw_observation_coverage=batch.coverage,
        expected_series_count=expected_series_count,
        observed_series_count=observed_series_count,
        series_coverage=observed_series_count / expected_series_count,
        metrics=tuple(metric_reports),
        error_count=len(batch.errors),
        errors_by_code=errors_by_code,
        unexpected_series=unexpected,
    )


def combine_coverage_reports(
    reports: Iterable[CoverageReport],
) -> PipelineCoverageReport:
    """Combine source reports for one run using count-weighted coverage."""
    source_reports = tuple(reports)
    if not source_reports:
        raise ValueError("reports must contain at least one source report")
    run_ids = {report.run_id for report in source_reports}
    if len(run_ids) != 1:
        raise ValueError("all coverage reports must belong to the same run_id")
    sources = [report.source for report in source_reports]
    if len(set(sources)) != len(sources):
        raise ValueError("coverage reports must not contain duplicate sources")

    expected_observations = sum(report.expected_observation_count for report in source_reports)
    observations = sum(report.observation_count for report in source_reports)
    expected_series = sum(report.expected_series_count for report in source_reports)
    observed_series = sum(report.observed_series_count for report in source_reports)
    actions = Counter(
        metric.decision.action.value
        for report in source_reports
        for metric in report.metrics
    )
    return PipelineCoverageReport(
        run_id=source_reports[0].run_id,
        sources=source_reports,
        expected_observation_count=expected_observations,
        observation_count=observations,
        raw_observation_coverage=observations / expected_observations,
        expected_series_count=expected_series,
        observed_series_count=observed_series,
        series_coverage=observed_series / expected_series,
        action_counts=tuple(sorted(actions.items())),
        error_count=sum(report.error_count for report in source_reports),
    )
