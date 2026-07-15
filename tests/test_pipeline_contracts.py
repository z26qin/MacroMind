from datetime import datetime, timedelta, timezone

import pytest

from pipeline.contracts import (
    DataFrequency,
    Observation,
    PipelineRunContext,
    RunMode,
    SourceBatch,
    SourceError,
    SourceStatus,
)


UTC = timezone.utc
T0 = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)


def _observation(**overrides):
    values = {
        "metric": "gdp_growth",
        "value": 2.1,
        "unit": "percent_yoy",
        "country": "Canada",
        "frequency": DataFrequency.ANNUAL,
        "period_start": datetime(2025, 1, 1, tzinfo=UTC),
        "period_end": datetime(2025, 12, 31, 23, 59, tzinfo=UTC),
        "event_time": T0 - timedelta(days=1),
        "observed_at": T0,
        "source": "world_bank",
        "revision": "1",
        "vintage": "2026-07-15",
    }
    values.update(overrides)
    return Observation(**values)


def _batch(**overrides):
    values = {
        "run_id": "run-20260715",
        "source": "world_bank",
        "expected_observation_count": 1,
        "requested_at": T0,
        "completed_at": T0 + timedelta(seconds=2),
        "observations": (_observation(),),
        "errors": (),
    }
    values.update(overrides)
    return SourceBatch(**values)


def test_run_context_normalizes_times_and_serializes_stably():
    context = PipelineRunContext(
        run_id="run-1",
        as_of=datetime(2026, 7, 15, 4, 0, tzinfo=timezone(timedelta(hours=-4))),
        started_at=T0,
        mode="historical",
        methodology_version="v0.2",
        config_hash="abc123",
    )
    assert context.mode is RunMode.HISTORICAL
    assert context.as_of == T0
    assert context.to_dict()["as_of"] == "2026-07-15T08:00:00Z"


def test_run_context_rejects_naive_time():
    with pytest.raises(ValueError, match="timezone-aware"):
        PipelineRunContext(
            run_id="run-1",
            as_of=datetime(2026, 7, 15),
            started_at=T0,
            mode=RunMode.LIVE,
            methodology_version="v0.2",
            config_hash="abc123",
        )


def test_observation_keeps_reference_period_separate_from_release_time():
    observation = _observation()
    assert observation.period_end.year == 2025
    assert observation.event_time.year == 2026
    assert observation.observed_at > observation.event_time
    assert observation.to_dict()["frequency"] == "annual"


def test_observation_allows_unknown_provider_release_time():
    observation = _observation(event_time=None)
    assert observation.event_time is None
    assert observation.to_dict()["event_time"] is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_observation_rejects_non_finite_values(value):
    with pytest.raises(ValueError, match="finite"):
        _observation(value=value)


def test_observation_rejects_invalid_time_ordering():
    with pytest.raises(ValueError, match="period_end"):
        _observation(
            period_start=datetime(2025, 12, 31, tzinfo=UTC),
            period_end=datetime(2025, 1, 1, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="observed_at"):
        _observation(event_time=T0 + timedelta(seconds=1))


def test_observation_identity_is_revision_aware():
    assert _observation().identity == (
        "world_bank",
        "Canada",
        "gdp_growth",
        "2025-01-01T00:00:00Z",
        "2025-12-31T23:59:00Z",
        "1",
        "2026-07-15",
    )


def test_source_batch_success_contract():
    batch = _batch()
    assert batch.status is SourceStatus.SUCCESS
    assert batch.coverage == 1.0
    assert batch.to_dict()["observation_count"] == 1


def test_source_batch_partial_contract():
    error = SourceError(
        code="missing_country",
        message="Japan was absent from the response",
        country="Japan",
        metric="gdp_growth",
    )
    batch = _batch(
        expected_observation_count=2,
        errors=(error,),
    )
    assert batch.status is SourceStatus.PARTIAL
    assert batch.coverage == 0.5
    assert batch.to_dict()["errors"][0]["code"] == "missing_country"


def test_source_batch_failed_contract():
    batch = _batch(observations=(), errors=(SourceError("timeout", "request timed out", True),))
    assert batch.status is SourceStatus.FAILED
    assert batch.coverage == 0.0


def test_source_batch_rejects_cross_source_observation():
    with pytest.raises(ValueError, match="batch source"):
        _batch(observations=(_observation(source="imf_weo"),))


def test_source_batch_rejects_impossible_counts():
    with pytest.raises(ValueError, match="cannot exceed"):
        _batch(
            expected_observation_count=1,
            observations=(_observation(), _observation(metric="inflation_yoy")),
        )
