"""Canonical, source-agnostic contracts for pipeline data and execution.

These types deliberately contain no World Bank, IMF, Yahoo, or GDELT logic.
Adapters will be migrated to them in the next implementation phase.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class RunMode(str, Enum):
    MOCK = "mock"
    LIVE = "live"
    HISTORICAL = "historical"


class DataFrequency(str, Enum):
    DAILY = "daily"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    EVENT = "event"
    WINDOW = "window"


class SourceStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


def _required_text(value: str, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    return normalized


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class PipelineRunContext:
    """Immutable identity and time boundary shared by every stage in one run."""

    run_id: str
    as_of: datetime
    started_at: datetime
    mode: RunMode
    methodology_version: str
    config_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _required_text(self.run_id, "run_id"))
        object.__setattr__(
            self, "methodology_version", _required_text(self.methodology_version, "methodology_version")
        )
        object.__setattr__(self, "config_hash", _required_text(self.config_hash, "config_hash"))
        object.__setattr__(self, "as_of", _utc(self.as_of, "as_of"))
        object.__setattr__(self, "started_at", _utc(self.started_at, "started_at"))
        if not isinstance(self.mode, RunMode):
            object.__setattr__(self, "mode", RunMode(self.mode))

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "as_of": _iso(self.as_of),
            "started_at": _iso(self.started_at),
            "mode": self.mode.value,
            "methodology_version": self.methodology_version,
            "config_hash": self.config_hash,
        }


@dataclass(frozen=True)
class Observation:
    """One normalized numeric fact, independent of its upstream provider.

    ``period_start``/``period_end`` describe the period measured by the value.
    ``event_time`` is when the value was released; it may be unknown for a
    provider that does not expose release timestamps. ``observed_at`` is when
    MacroMind actually obtained this revision and is always required.
    """

    metric: str
    value: float
    unit: str
    country: str
    frequency: DataFrequency
    period_start: datetime
    period_end: datetime
    event_time: datetime | None
    observed_at: datetime
    source: str
    revision: str
    vintage: str

    def __post_init__(self) -> None:
        for name in ("metric", "unit", "country", "source", "revision", "vintage"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise TypeError("value must be numeric")
        numeric_value = float(self.value)
        if not math.isfinite(numeric_value):
            raise ValueError("value must be finite")
        object.__setattr__(self, "value", numeric_value)
        if not isinstance(self.frequency, DataFrequency):
            object.__setattr__(self, "frequency", DataFrequency(self.frequency))

        period_start = _utc(self.period_start, "period_start")
        period_end = _utc(self.period_end, "period_end")
        observed_at = _utc(self.observed_at, "observed_at")
        event_time = None if self.event_time is None else _utc(self.event_time, "event_time")
        if period_end < period_start:
            raise ValueError("period_end cannot be earlier than period_start")
        if event_time is not None and observed_at < event_time:
            raise ValueError("observed_at cannot be earlier than event_time")
        object.__setattr__(self, "period_start", period_start)
        object.__setattr__(self, "period_end", period_end)
        object.__setattr__(self, "event_time", event_time)
        object.__setattr__(self, "observed_at", observed_at)

    @property
    def identity(self) -> tuple[str, str, str, str, str, str, str]:
        """Natural revision identity used later by PIT persistence."""
        return (
            self.source,
            self.country,
            self.metric,
            _iso(self.period_start),
            _iso(self.period_end),
            self.revision,
            self.vintage,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "country": self.country,
            "frequency": self.frequency.value,
            "period_start": _iso(self.period_start),
            "period_end": _iso(self.period_end),
            "event_time": _iso(self.event_time),
            "observed_at": _iso(self.observed_at),
            "source": self.source,
            "revision": self.revision,
            "vintage": self.vintage,
        }


@dataclass(frozen=True)
class SourceError:
    """Structured adapter failure retained alongside successful observations."""

    code: str
    message: str
    retryable: bool = False
    country: str | None = None
    metric: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _required_text(self.code, "code"))
        object.__setattr__(self, "message", _required_text(self.message, "message"))
        if self.country is not None:
            object.__setattr__(self, "country", _required_text(self.country, "country"))
        if self.metric is not None:
            object.__setattr__(self, "metric", _required_text(self.metric, "metric"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "country": self.country,
            "metric": self.metric,
        }


@dataclass(frozen=True)
class SourceBatch:
    """The complete, inspectable result of one source execution in one run."""

    run_id: str
    source: str
    expected_observation_count: int
    requested_at: datetime
    completed_at: datetime
    observations: tuple[Observation, ...] = field(default_factory=tuple)
    errors: tuple[SourceError, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _required_text(self.run_id, "run_id"))
        object.__setattr__(self, "source", _required_text(self.source, "source"))
        if not isinstance(self.expected_observation_count, int):
            raise TypeError("expected_observation_count must be an integer")
        if self.expected_observation_count <= 0:
            raise ValueError("expected_observation_count must be positive")
        object.__setattr__(self, "requested_at", _utc(self.requested_at, "requested_at"))
        object.__setattr__(self, "completed_at", _utc(self.completed_at, "completed_at"))
        if self.completed_at < self.requested_at:
            raise ValueError("completed_at cannot be earlier than requested_at")

        observations = tuple(self.observations)
        errors = tuple(self.errors)
        if len(observations) > self.expected_observation_count:
            raise ValueError("observations cannot exceed expected_observation_count")
        if any(observation.source != self.source for observation in observations):
            raise ValueError("every observation source must match the batch source")
        if any(not isinstance(error, SourceError) for error in errors):
            raise TypeError("errors must contain SourceError values")
        object.__setattr__(self, "observations", observations)
        object.__setattr__(self, "errors", errors)

    @property
    def coverage(self) -> float:
        return len(self.observations) / self.expected_observation_count

    @property
    def status(self) -> SourceStatus:
        if not self.observations:
            return SourceStatus.FAILED
        if self.errors or len(self.observations) < self.expected_observation_count:
            return SourceStatus.PARTIAL
        return SourceStatus.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "source": self.source,
            "status": self.status.value,
            "expected_observation_count": self.expected_observation_count,
            "observation_count": len(self.observations),
            "coverage": self.coverage,
            "requested_at": _iso(self.requested_at),
            "completed_at": _iso(self.completed_at),
            "observations": [observation.to_dict() for observation in self.observations],
            "errors": [error.to_dict() for error in self.errors],
        }
