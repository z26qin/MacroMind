"""Model-input quality gates for point-in-time observations.

Raw observations remain immutable in the store. These gates operate only on
the PIT-visible candidates that could enter a signal, reject unsafe live data,
and then reuse the explicit fallback policies to keep the model frame complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping

from pipeline.contracts import Observation
from pipeline.fallback import (
    FallbackAction,
    FallbackDecision,
    decide_fallback,
    policy_for_source,
)


class QualityGate(str, Enum):
    FRESHNESS = "freshness"
    UNIT = "unit"
    RANGE = "range"
    COVERAGE = "coverage"
    DATE_ALIGNMENT = "date_alignment"


class QualityStatus(str, Enum):
    PASS = "pass"
    DEGRADED = "degraded"


class GateAction(str, Enum):
    REJECT_OBSERVATION = "reject_observation"
    FALLBACK_METRIC = "fallback_metric"
    RETAIN_PARTIAL = "retain_partial"


class FreshnessBasis(str, Enum):
    PERIOD_END = "period_end"
    OBSERVED_AT = "observed_at"


class DateSemantics(str, Enum):
    REALIZED = "realized"
    FORECAST_TARGET = "forecast_target"


@dataclass(frozen=True)
class MetricQualityRule:
    expected_unit: str
    minimum: float
    maximum: float
    max_age_days: int
    freshness_basis: FreshnessBasis
    date_semantics: DateSemantics

    def __post_init__(self) -> None:
        if not str(self.expected_unit).strip():
            raise ValueError("expected_unit must be non-empty")
        if self.minimum > self.maximum:
            raise ValueError("minimum cannot exceed maximum")
        if not isinstance(self.max_age_days, int) or self.max_age_days <= 0:
            raise ValueError("max_age_days must be a positive integer")


def _rule(
    unit: str,
    minimum: float,
    maximum: float,
    max_age_days: int,
    freshness_basis: FreshnessBasis,
    date_semantics: DateSemantics,
) -> MetricQualityRule:
    return MetricQualityRule(
        expected_unit=unit,
        minimum=minimum,
        maximum=maximum,
        max_age_days=max_age_days,
        freshness_basis=freshness_basis,
        date_semantics=date_semantics,
    )


_REALIZED_ANNUAL = (730, FreshnessBasis.PERIOD_END, DateSemantics.REALIZED)
_CURRENT_FORECAST = (240, FreshnessBasis.OBSERVED_AT, DateSemantics.FORECAST_TARGET)
_CURRENT_MARKET = (45, FreshnessBasis.PERIOD_END, DateSemantics.REALIZED)
_CURRENT_NEWS = (2, FreshnessBasis.PERIOD_END, DateSemantics.REALIZED)
QUALITY_POLICY_VERSION = "v1"


QUALITY_RULES = MappingProxyType(
    {
        "world_bank": MappingProxyType(
            {
                "inflation_yoy": _rule("percent_yoy", -50.0, 500.0, *_REALIZED_ANNUAL),
                "gdp_growth": _rule("percent_yoy", -50.0, 50.0, *_REALIZED_ANNUAL),
                "unemployment": _rule(
                    "percent_labor_force", 0.0, 100.0, *_REALIZED_ANNUAL
                ),
            }
        ),
        "imf_weo": MappingProxyType(
            {
                "inflation_yoy": _rule("percent_yoy", -50.0, 500.0, *_CURRENT_FORECAST),
                "gdp_growth": _rule("percent_yoy", -50.0, 50.0, *_CURRENT_FORECAST),
                "unemployment": _rule(
                    "percent_labor_force", 0.0, 100.0, *_CURRENT_FORECAST
                ),
            }
        ),
        "yahoo": MappingProxyType(
            {
                "equity_3m_return": _rule(
                    "percent_return", -100.0, 500.0, *_CURRENT_MARKET
                ),
                "fx_3m_return": _rule(
                    "percent_return", -100.0, 500.0, *_CURRENT_MARKET
                ),
            }
        ),
        "gdelt": MappingProxyType(
            {
                "news_pressure": _rule(
                    "normalized_article_pressure", -25.0, 25.0, *_CURRENT_NEWS
                ),
            }
        ),
    }
)


@dataclass(frozen=True)
class QualityIssue:
    gate: QualityGate
    action: GateAction
    source: str
    metric: str
    code: str
    message: str
    country: str | None = None
    expected: str | None = None
    actual: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "gate": self.gate.value,
            "action": self.action.value,
            "source": self.source,
            "metric": self.metric,
            "country": self.country,
            "code": self.code,
            "message": self.message,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass(frozen=True)
class DataQualityReport:
    policy_version: str
    as_of: datetime
    status: QualityStatus
    input_observation_count: int
    candidate_observation_count: int
    accepted_observation_count: int
    blocked_observation_count: int
    coverage_decisions: tuple[FallbackDecision, ...]
    issues: tuple[QualityIssue, ...]

    @property
    def issue_counts(self) -> tuple[tuple[str, int], ...]:
        return tuple(
            (gate.value, sum(issue.gate is gate for issue in self.issues))
            for gate in QualityGate
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "as_of": self.as_of.isoformat().replace("+00:00", "Z"),
            "status": self.status.value,
            "input_observation_count": self.input_observation_count,
            "candidate_observation_count": self.candidate_observation_count,
            "accepted_observation_count": self.accepted_observation_count,
            "blocked_observation_count": self.blocked_observation_count,
            "issue_counts": dict(self.issue_counts),
            "coverage_decisions": [
                decision.to_dict() for decision in self.coverage_decisions
            ],
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class QualityGateResult:
    report: DataQualityReport
    accepted_observations: tuple[Observation, ...]

    def by_source(self) -> dict[str, tuple[Observation, ...]]:
        return {
            source: tuple(
                observation
                for observation in self.accepted_observations
                if observation.source == source
            )
            for source in QUALITY_RULES
        }


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _latest_by_series(
    observations: Iterable[Observation],
    *,
    source: str,
    countries: tuple[str, ...],
    metrics: tuple[str, ...],
) -> dict[tuple[str, str], Observation]:
    expected_countries = set(countries)
    expected_metrics = set(metrics)
    latest: dict[tuple[str, str], Observation] = {}
    for observation in observations:
        if (
            observation.source != source
            or observation.country not in expected_countries
            or observation.metric not in expected_metrics
        ):
            continue
        key = (observation.country, observation.metric)
        current = latest.get(key)
        order = (
            observation.period_end,
            observation.observed_at,
            observation.vintage,
            observation.revision,
        )
        if current is None or order > (
            current.period_end,
            current.observed_at,
            current.vintage,
            current.revision,
        ):
            latest[key] = observation
    return latest


def _latest_by_year(
    observations: Iterable[Observation],
    *,
    source: str,
    countries: tuple[str, ...],
    metrics: tuple[str, ...],
) -> dict[tuple[str, str, int], Observation]:
    expected_countries = set(countries)
    expected_metrics = set(metrics)
    latest: dict[tuple[str, str, int], Observation] = {}
    for observation in observations:
        if (
            observation.source != source
            or observation.country not in expected_countries
            or observation.metric not in expected_metrics
        ):
            continue
        key = (observation.country, observation.metric, observation.period_start.year)
        current = latest.get(key)
        if current is None or (
            observation.observed_at,
            observation.vintage,
            observation.revision,
        ) > (current.observed_at, current.vintage, current.revision):
            latest[key] = observation
    return latest


def _observation_issues(
    observation: Observation,
    rule: MetricQualityRule,
    as_of: datetime,
) -> tuple[QualityIssue, ...]:
    issues: list[QualityIssue] = []

    def reject(
        gate: QualityGate,
        code: str,
        message: str,
        *,
        expected: object | None = None,
        actual: object | None = None,
    ) -> None:
        issues.append(
            QualityIssue(
                gate=gate,
                action=GateAction.REJECT_OBSERVATION,
                source=observation.source,
                metric=observation.metric,
                country=observation.country,
                code=code,
                message=message,
                expected=None if expected is None else str(expected),
                actual=None if actual is None else str(actual),
            )
        )

    if observation.observed_at > as_of:
        reject(
            QualityGate.DATE_ALIGNMENT,
            "observed_after_decision",
            "Observation was not available at the decision timestamp",
            expected=f"<= {as_of.isoformat()}",
            actual=observation.observed_at.isoformat(),
        )
    if observation.event_time is not None and observation.event_time > as_of:
        reject(
            QualityGate.DATE_ALIGNMENT,
            "event_after_decision",
            "Observation event occurs after the decision timestamp",
            expected=f"<= {as_of.isoformat()}",
            actual=observation.event_time.isoformat(),
        )
    if (
        rule.date_semantics is DateSemantics.REALIZED
        and observation.period_end > as_of
    ):
        reject(
            QualityGate.DATE_ALIGNMENT,
            "future_realized_period",
            "Realized input period ends after the decision timestamp",
            expected=f"<= {as_of.isoformat()}",
            actual=observation.period_end.isoformat(),
        )

    if observation.unit != rule.expected_unit:
        reject(
            QualityGate.UNIT,
            "unexpected_unit",
            "Observation unit does not match the metric contract",
            expected=rule.expected_unit,
            actual=observation.unit,
        )

    if not rule.minimum <= observation.value <= rule.maximum:
        reject(
            QualityGate.RANGE,
            "out_of_range",
            "Observation value is outside the admissible range",
            expected=f"[{rule.minimum}, {rule.maximum}]",
            actual=observation.value,
        )

    freshness_time = (
        observation.period_end
        if rule.freshness_basis is FreshnessBasis.PERIOD_END
        else observation.observed_at
    )
    if as_of - freshness_time > timedelta(days=rule.max_age_days):
        reject(
            QualityGate.FRESHNESS,
            "stale_observation",
            "Observation is older than the source/metric freshness limit",
            expected=f"<= {rule.max_age_days} days old by {rule.freshness_basis.value}",
            actual=freshness_time.isoformat(),
        )

    return tuple(issues)


def run_quality_gates(
    observations_by_source: Mapping[str, Iterable[Observation]],
    *,
    as_of: datetime,
    economies: Iterable[str],
) -> QualityGateResult:
    """Return only model-ready observations plus a complete gate report.

    Selection is deliberately before freshness evaluation: old raw history is
    retained for audit, but only the latest relevant value is a model candidate.
    IMF candidates are aligned to the accepted World Bank actual year + 1.
    """
    decision_time = _utc(as_of, "as_of")
    countries = tuple(str(economy).strip() for economy in economies)
    if not countries or any(not country for country in countries):
        raise ValueError("economies must contain non-empty values")
    if len(set(countries)) != len(countries):
        raise ValueError("economies must not contain duplicates")

    materialized = {
        source: tuple(observations)
        for source, observations in observations_by_source.items()
    }
    unknown_sources = set(materialized) - set(QUALITY_RULES)
    if unknown_sources:
        raise KeyError(f"No quality rules registered for sources: {sorted(unknown_sources)!r}")

    issues: list[QualityIssue] = []
    candidates: dict[tuple[str, str, str], Observation] = {}
    accepted: dict[tuple[str, str, str], Observation] = {}

    def validate_candidate(observation: Observation) -> None:
        key = (observation.source, observation.country, observation.metric)
        candidates[key] = observation
        found = _observation_issues(
            observation,
            QUALITY_RULES[observation.source][observation.metric],
            decision_time,
        )
        issues.extend(found)
        if not found:
            accepted[key] = observation

    for source in ("world_bank", "yahoo", "gdelt"):
        if source not in materialized:
            continue
        metrics = tuple(QUALITY_RULES[source])
        selected = _latest_by_series(
            materialized[source],
            source=source,
            countries=countries,
            metrics=metrics,
        )
        for country in countries:
            for metric in metrics:
                observation = selected.get((country, metric))
                if observation is not None:
                    validate_candidate(observation)

    if "imf_weo" in materialized:
        source = "imf_weo"
        metrics = tuple(QUALITY_RULES[source])
        forecasts = _latest_by_year(
            materialized[source],
            source=source,
            countries=countries,
            metrics=metrics,
        )
        for country in countries:
            for metric in metrics:
                actual = accepted.get(("world_bank", country, metric))
                if actual is None:
                    if any(key[:2] == (country, metric) for key in forecasts):
                        issues.append(
                            QualityIssue(
                                gate=QualityGate.DATE_ALIGNMENT,
                                action=GateAction.FALLBACK_METRIC,
                                source=source,
                                metric=metric,
                                country=country,
                                code="missing_actual_dependency",
                                message="IMF forecast has no quality-approved World Bank actual",
                            )
                        )
                    continue
                target_year = actual.period_start.year + 1
                observation = forecasts.get((country, metric, target_year))
                if observation is None:
                    issues.append(
                        QualityIssue(
                            gate=QualityGate.DATE_ALIGNMENT,
                            action=GateAction.FALLBACK_METRIC,
                            source=source,
                            metric=metric,
                            country=country,
                            code="missing_target_period",
                            message="IMF forecast is not aligned to World Bank actual year + 1",
                            expected=str(target_year),
                        )
                    )
                    continue
                validate_candidate(observation)

    coverage_decisions: list[FallbackDecision] = []
    for source in QUALITY_RULES:
        if source not in materialized:
            continue
        policy = policy_for_source(source)
        for metric in QUALITY_RULES[source]:
            observed_countries = tuple(
                country
                for country in countries
                if (source, country, metric) in accepted
            )
            decision = decide_fallback(policy, metric, countries, observed_countries)
            coverage_decisions.append(decision)
            if decision.action is FallbackAction.LIVE:
                continue

            action = (
                GateAction.RETAIN_PARTIAL
                if decision.action is FallbackAction.MIXED
                else GateAction.FALLBACK_METRIC
            )
            issues.append(
                QualityIssue(
                    gate=QualityGate.COVERAGE,
                    action=action,
                    source=source,
                    metric=metric,
                    code=decision.reason,
                    message="Quality-approved country coverage triggered the fallback policy",
                    expected=f"{len(countries)}/{len(countries)} countries",
                    actual=f"{len(observed_countries)}/{len(countries)} countries",
                )
            )
            if decision.action is FallbackAction.FALLBACK:
                for country in observed_countries:
                    accepted.pop((source, country, metric), None)

    accepted_observations = tuple(
        accepted[key]
        for key in sorted(
            accepted,
            key=lambda item: (
                tuple(QUALITY_RULES).index(item[0]),
                countries.index(item[1]),
                tuple(QUALITY_RULES[item[0]]).index(item[2]),
            ),
        )
    )
    report = DataQualityReport(
        policy_version=QUALITY_POLICY_VERSION,
        as_of=decision_time,
        status=QualityStatus.PASS if not issues else QualityStatus.DEGRADED,
        input_observation_count=sum(len(rows) for rows in materialized.values()),
        candidate_observation_count=len(candidates),
        accepted_observation_count=len(accepted_observations),
        blocked_observation_count=len(candidates) - len(accepted_observations),
        coverage_decisions=tuple(coverage_decisions),
        issues=tuple(issues),
    )
    return QualityGateResult(
        report=report,
        accepted_observations=accepted_observations,
    )
