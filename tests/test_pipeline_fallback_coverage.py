from datetime import datetime, timezone

import pytest

from pipeline.contracts import (
    DataFrequency,
    Observation,
    SourceBatch,
    SourceError,
    SourceStatus,
)
from pipeline.coverage import build_coverage_report, combine_coverage_reports
from pipeline.fallback import (
    FallbackAction,
    FallbackScope,
    LIVE_FALLBACK_POLICIES,
    policy_for_source,
    resolve_with_policy,
)


UTC = timezone.utc
T0 = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def _observation(
    country: str,
    metric: str,
    *,
    source: str = "world_bank",
    year: int = 2024,
) -> Observation:
    return Observation(
        metric=metric,
        value=1.0,
        unit="percent",
        country=country,
        frequency=DataFrequency.ANNUAL,
        period_start=datetime(year, 1, 1, tzinfo=UTC),
        period_end=datetime(year, 12, 31, tzinfo=UTC),
        event_time=None,
        observed_at=T0,
        source=source,
        revision="unreported",
        vintage="2026-07-15T12:00:00Z",
    )


def _batch(
    observations: tuple[Observation, ...],
    *,
    source: str = "world_bank",
    expected_count: int = 8,
    errors: tuple[SourceError, ...] = (),
) -> SourceBatch:
    return SourceBatch(
        run_id="run-policy",
        source=source,
        expected_observation_count=expected_count,
        requested_at=T0,
        completed_at=T0,
        observations=observations,
        errors=errors,
    )


def test_live_fallback_policy_registry_documents_current_behavior():
    assert set(LIVE_FALLBACK_POLICIES) == {
        "world_bank",
        "imf_weo",
        "yahoo",
        "gdelt",
    }
    assert policy_for_source("world_bank").scope is FallbackScope.PER_CELL
    for source in ("imf_weo", "yahoo", "gdelt"):
        policy = policy_for_source(source)
        assert policy.scope is FallbackScope.ALL_OR_NONE
        assert policy.fallback_source == "mock"
        assert policy.rationale


def test_per_cell_policy_selects_available_values_and_falls_back_missing_cells():
    values = {"Canada": 2.0}
    resolution = resolve_with_policy(
        policy_for_source("world_bank"),
        "gdp_growth",
        ("Canada", "Japan"),
        values.get,
    )

    assert resolution.decision.action is FallbackAction.MIXED
    assert resolution.decision.coverage == 0.5
    assert resolution.decision.observed_countries == ("Canada",)
    assert resolution.decision.selected_live_countries == ("Canada",)
    assert resolution.decision.fallback_countries == ("Japan",)
    assert resolution.as_dict() == {"Canada": 2.0}


def test_all_or_none_policy_discards_partial_live_values():
    values = {"Canada": 2.0}
    resolution = resolve_with_policy(
        policy_for_source("imf_weo"),
        "gdp_growth",
        ("Canada", "Japan"),
        values.get,
    )

    assert resolution.decision.action is FallbackAction.FALLBACK
    assert resolution.decision.reason == "incomplete_all_or_none"
    assert resolution.decision.observed_countries == ("Canada",)
    assert resolution.decision.selected_live_countries == ()
    assert resolution.decision.fallback_countries == ("Canada", "Japan")
    assert resolution.as_dict() == {}


def test_complete_all_or_none_policy_selects_the_full_universe():
    values = {"Canada": 2.0, "Japan": 1.0}
    resolution = resolve_with_policy(
        policy_for_source("yahoo"),
        "equity_3m_return",
        ("Canada", "Japan"),
        values.get,
    )

    assert resolution.decision.action is FallbackAction.LIVE
    assert resolution.decision.coverage == 1.0
    assert resolution.as_dict() == values


def test_unknown_source_has_no_implicit_fallback():
    with pytest.raises(KeyError, match="No fallback policy"):
        policy_for_source("unknown_vendor")


def test_coverage_report_separates_raw_rows_from_country_metric_series():
    batch = _batch(
        (
            _observation("Canada", "inflation_yoy", year=2023),
            _observation("Canada", "inflation_yoy", year=2024),
            _observation("Japan", "gdp_growth"),
        ),
        errors=(
            SourceError(
                code="missing_series",
                message="missing",
                country="Japan",
                metric="inflation_yoy",
            ),
        ),
    )

    report = build_coverage_report(
        batch,
        expected_economies=("Canada", "Japan"),
        expected_metrics=("inflation_yoy", "gdp_growth"),
    )

    assert report.batch_status is SourceStatus.PARTIAL
    assert report.raw_observation_coverage == 3 / 8
    assert report.expected_series_count == 4
    assert report.observed_series_count == 2
    assert report.series_coverage == 0.5
    assert report.actions == {
        "inflation_yoy": FallbackAction.MIXED,
        "gdp_growth": FallbackAction.MIXED,
    }
    inflation = report.metrics[0]
    assert inflation.observed_series_count == 1
    assert inflation.missing_countries == ("Japan",)
    assert inflation.error_count == 1
    assert report.errors_by_code == (("missing_series", 1),)


def test_all_or_none_coverage_report_exposes_discarded_partial_data():
    batch = _batch(
        (
            _observation("Canada", "equity_3m_return", source="yahoo"),
            _observation("Canada", "fx_3m_return", source="yahoo"),
        ),
        source="yahoo",
        expected_count=4,
    )

    report = build_coverage_report(
        batch,
        expected_economies=("Canada", "Japan"),
        expected_metrics=("equity_3m_return", "fx_3m_return"),
    )

    assert report.series_coverage == 0.5
    assert set(report.actions.values()) == {FallbackAction.FALLBACK}
    for metric in report.metrics:
        assert metric.decision.observed_countries == ("Canada",)
        assert metric.decision.selected_live_countries == ()
        assert metric.decision.fallback_countries == ("Canada", "Japan")


def test_coverage_report_serializes_errors_decisions_and_unexpected_series():
    batch = _batch(
        (
            _observation("Canada", "inflation_yoy"),
            _observation("Brazil", "inflation_yoy"),
        ),
        expected_count=2,
        errors=(SourceError("missing_series", "missing", country="Japan"),),
    )

    report = build_coverage_report(
        batch,
        expected_economies=("Canada", "Japan"),
        expected_metrics=("inflation_yoy",),
    )
    payload = report.to_dict()

    assert payload["errors_by_code"] == {"missing_series": 1}
    assert payload["policy"] == {
        "source": "world_bank",
        "scope": "per_cell",
        "fallback_source": "mock",
        "rationale": policy_for_source("world_bank").rationale,
    }
    assert payload["metrics"][0]["decision"]["action"] == "mixed"
    assert payload["unexpected_series"] == [
        {"country": "Brazil", "metric": "inflation_yoy"}
    ]


def test_coverage_report_rejects_policy_for_another_source():
    batch = _batch((_observation("Canada", "inflation_yoy"),), expected_count=1)
    with pytest.raises(ValueError, match="does not match"):
        build_coverage_report(
            batch,
            expected_economies=("Canada",),
            expected_metrics=("inflation_yoy",),
            policy=policy_for_source("gdelt"),
        )


def test_pipeline_coverage_report_combines_sources_with_weighted_counts():
    world_bank_report = build_coverage_report(
        _batch(
            (
                _observation("Canada", "inflation_yoy", year=2023),
                _observation("Canada", "inflation_yoy", year=2024),
            ),
            expected_count=4,
        ),
        expected_economies=("Canada", "Japan"),
        expected_metrics=("inflation_yoy",),
    )
    yahoo_report = build_coverage_report(
        _batch(
            (
                _observation("Canada", "equity_3m_return", source="yahoo"),
                _observation("Japan", "equity_3m_return", source="yahoo"),
            ),
            source="yahoo",
            expected_count=2,
        ),
        expected_economies=("Canada", "Japan"),
        expected_metrics=("equity_3m_return",),
    )

    report = combine_coverage_reports((world_bank_report, yahoo_report))

    assert report.run_id == "run-policy"
    assert report.expected_observation_count == 6
    assert report.observation_count == 4
    assert report.raw_observation_coverage == 4 / 6
    assert report.expected_series_count == 4
    assert report.observed_series_count == 3
    assert report.series_coverage == 0.75
    assert report.action_counts == (("live", 1), ("mixed", 1))
    assert report.to_dict()["action_counts"] == {"live": 1, "mixed": 1}


def test_pipeline_coverage_report_rejects_mixed_runs_and_duplicate_sources():
    report = build_coverage_report(
        _batch((_observation("Canada", "inflation_yoy"),), expected_count=1),
        expected_economies=("Canada",),
        expected_metrics=("inflation_yoy",),
    )
    other_run_batch = SourceBatch(
        run_id="another-run",
        source="yahoo",
        expected_observation_count=1,
        requested_at=T0,
        completed_at=T0,
        observations=(_observation("Canada", "equity_3m_return", source="yahoo"),),
    )
    other_run = build_coverage_report(
        other_run_batch,
        expected_economies=("Canada",),
        expected_metrics=("equity_3m_return",),
    )

    with pytest.raises(ValueError, match="same run_id"):
        combine_coverage_reports((report, other_run))
    with pytest.raises(ValueError, match="duplicate sources"):
        combine_coverage_reports((report, report))
