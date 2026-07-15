"""Explicit fallback policies and deterministic overlay decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Callable, Generic, Iterable, TypeVar


class FallbackScope(str, Enum):
    PER_CELL = "per_cell"
    ALL_OR_NONE = "all_or_none"


class FallbackAction(str, Enum):
    LIVE = "live"
    MIXED = "mixed"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class FallbackPolicy:
    source: str
    scope: FallbackScope
    fallback_source: str
    rationale: str

    def __post_init__(self) -> None:
        for field_name in ("source", "fallback_source", "rationale"):
            value = str(getattr(self, field_name)).strip()
            if not value:
                raise ValueError(f"{field_name} must be non-empty")
            object.__setattr__(self, field_name, value)
        if not isinstance(self.scope, FallbackScope):
            object.__setattr__(self, "scope", FallbackScope(self.scope))

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "scope": self.scope.value,
            "fallback_source": self.fallback_source,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class FallbackDecision:
    source: str
    metric: str
    scope: FallbackScope
    action: FallbackAction
    expected_countries: tuple[str, ...]
    observed_countries: tuple[str, ...]
    selected_live_countries: tuple[str, ...]
    fallback_countries: tuple[str, ...]
    coverage: float
    fallback_source: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "metric": self.metric,
            "scope": self.scope.value,
            "action": self.action.value,
            "expected_countries": list(self.expected_countries),
            "observed_countries": list(self.observed_countries),
            "selected_live_countries": list(self.selected_live_countries),
            "fallback_countries": list(self.fallback_countries),
            "coverage": self.coverage,
            "fallback_source": self.fallback_source,
            "reason": self.reason,
        }


_T = TypeVar("_T")


@dataclass(frozen=True)
class FallbackResolution(Generic[_T]):
    decision: FallbackDecision
    selected_values: tuple[tuple[str, _T], ...]

    def as_dict(self) -> dict[str, _T]:
        return dict(self.selected_values)


LIVE_FALLBACK_POLICIES = MappingProxyType(
    {
        "world_bank": FallbackPolicy(
            source="world_bank",
            scope=FallbackScope.PER_CELL,
            fallback_source="mock",
            rationale="Use each available realized macro value and retain mock only for missing cells.",
        ),
        "imf_weo": FallbackPolicy(
            source="imf_weo",
            scope=FallbackScope.ALL_OR_NONE,
            fallback_source="mock",
            rationale="Expected-change mode requires a matching forecast for every economy in a metric.",
        ),
        "yahoo": FallbackPolicy(
            source="yahoo",
            scope=FallbackScope.ALL_OR_NONE,
            fallback_source="mock",
            rationale="Cross-sectional market ranks must use one consistent live universe per metric.",
        ),
        "gdelt": FallbackPolicy(
            source="gdelt",
            scope=FallbackScope.ALL_OR_NONE,
            fallback_source="mock",
            rationale="Cross-sectional news pressure requires the complete economy universe.",
        ),
    }
)


def policy_for_source(source: str) -> FallbackPolicy:
    try:
        return LIVE_FALLBACK_POLICIES[source]
    except KeyError as exc:
        raise KeyError(f"No fallback policy registered for source {source!r}") from exc


def _dimension(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    normalized = tuple(str(value).strip() for value in values)
    if not normalized or any(not value for value in normalized):
        raise ValueError(f"{field_name} must contain non-empty values")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must not contain duplicates")
    return normalized


def decide_fallback(
    policy: FallbackPolicy,
    metric: str,
    expected_countries: Iterable[str],
    observed_countries: Iterable[str],
) -> FallbackDecision:
    """Decide which observed cells may replace the configured fallback."""
    expected = _dimension(expected_countries, "expected_countries")
    metric = str(metric).strip()
    if not metric:
        raise ValueError("metric must be non-empty")

    observed_set = set(observed_countries)
    expected_set = set(expected)
    unexpected = observed_set - expected_set
    if unexpected:
        raise ValueError(f"observed_countries contains unexpected values: {sorted(unexpected)!r}")
    observed = tuple(country for country in expected if country in observed_set)
    missing = tuple(country for country in expected if country not in observed_set)
    coverage = len(observed) / len(expected)

    if not missing:
        action = FallbackAction.LIVE
        selected = observed
        fallback = ()
        reason = "complete_coverage"
    elif policy.scope is FallbackScope.PER_CELL and observed:
        action = FallbackAction.MIXED
        selected = observed
        fallback = missing
        reason = "partial_live_per_cell"
    else:
        action = FallbackAction.FALLBACK
        selected = ()
        fallback = expected
        reason = "no_live_values" if not observed else "incomplete_all_or_none"

    return FallbackDecision(
        source=policy.source,
        metric=metric,
        scope=policy.scope,
        action=action,
        expected_countries=expected,
        observed_countries=observed,
        selected_live_countries=selected,
        fallback_countries=fallback,
        coverage=coverage,
        fallback_source=policy.fallback_source,
        reason=reason,
    )


def resolve_with_policy(
    policy: FallbackPolicy,
    metric: str,
    economies: Iterable[str],
    resolve: Callable[[str], _T | None],
) -> FallbackResolution[_T]:
    """Resolve candidate values and apply the source's declared fallback scope."""
    expected = _dimension(economies, "economies")
    candidates: dict[str, _T] = {}
    for economy in expected:
        value = resolve(economy)
        if value is not None:
            candidates[economy] = value

    decision = decide_fallback(policy, metric, expected, candidates)
    selected = tuple(
        (economy, candidates[economy])
        for economy in decision.selected_live_countries
    )
    return FallbackResolution(decision=decision, selected_values=selected)
