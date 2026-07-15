"""Shared normalization helpers for live source adapters."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Iterable

from pipeline.contracts import PipelineRunContext


Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def capture_utc(clock: Clock, field_name: str) -> datetime:
    value = clock()
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} clock value must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} clock value must be timezone-aware")
    return value.astimezone(timezone.utc)


def normalize_request(
    context: PipelineRunContext,
    economies: Iterable[str],
) -> tuple[str, ...]:
    """Validate the shared portion of every adapter request."""
    if not isinstance(context, PipelineRunContext):
        raise TypeError("context must be a PipelineRunContext")
    normalized = tuple(str(economy).strip() for economy in economies)
    if not normalized or any(not economy for economy in normalized):
        raise ValueError("economies must contain at least one non-empty value")
    if len(set(normalized)) != len(normalized):
        raise ValueError("economies must not contain duplicates")
    return normalized


def ingestion_vintage(observed_at: datetime) -> str:
    """Use acquisition time when a provider exposes no formal vintage id."""
    return observed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def annual_period(year: int) -> tuple[datetime, datetime]:
    return (
        datetime(year, 1, 1, tzinfo=timezone.utc),
        datetime(year, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc),
    )
