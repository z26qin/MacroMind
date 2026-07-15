from datetime import datetime, timedelta, timezone

import pytest

from pipeline.contracts import DataFrequency, Observation
from pipeline.fallback import FallbackAction
from pipeline.quality import (
    QUALITY_RULES,
    GateAction,
    QualityGate,
    QualityStatus,
    run_quality_gates,
)


UTC = timezone.utc
AS_OF = datetime(2026, 7, 15, 23, 59, tzinfo=UTC)
COUNTRIES = ("Canada", "Japan")


def observation(
    source: str,
    country: str,
    metric: str,
    *,
    value: float,
    unit: str,
    period_end: datetime,
    year: int | None = None,
) -> Observation:
    is_annual = year is not None
    return Observation(
        metric=metric,
        value=value,
        unit=unit,
        country=country,
        frequency=DataFrequency.ANNUAL if is_annual else DataFrequency.WINDOW,
        period_start=(
            datetime(year, 1, 1, tzinfo=UTC)
            if is_annual
            else period_end - timedelta(days=90)
        ),
        period_end=period_end,
        event_time=None,
        observed_at=AS_OF - timedelta(hours=1),
        source=source,
        revision="test",
        vintage="2026-07-15T22:59:00Z",
    )


def yahoo_observations(
    *,
    equity_canada_unit: str = "percent_return",
    fx_japan_value: float = 3.0,
    equity_canada_end: datetime | None = None,
) -> tuple[Observation, ...]:
    period_end = AS_OF - timedelta(days=1)
    return (
        observation(
            "yahoo",
            "Canada",
            "equity_3m_return",
            value=10.0,
            unit=equity_canada_unit,
            period_end=equity_canada_end or period_end,
        ),
        observation(
            "yahoo",
            "Japan",
            "equity_3m_return",
            value=8.0,
            unit="percent_return",
            period_end=period_end,
        ),
        observation(
            "yahoo",
            "Canada",
            "fx_3m_return",
            value=2.0,
            unit="percent_return",
            period_end=period_end,
        ),
        observation(
            "yahoo",
            "Japan",
            "fx_3m_return",
            value=fx_japan_value,
            unit="percent_return",
            period_end=period_end,
        ),
    )


def test_quality_rule_registry_covers_the_four_live_contracts():
    assert {source: tuple(rules) for source, rules in QUALITY_RULES.items()} == {
        "world_bank": ("inflation_yoy", "gdp_growth", "unemployment"),
        "imf_weo": ("inflation_yoy", "gdp_growth", "unemployment"),
        "yahoo": ("equity_3m_return", "fx_3m_return"),
        "gdelt": ("news_pressure",),
    }
    assert QUALITY_RULES["yahoo"]["equity_3m_return"].max_age_days == 45
    assert QUALITY_RULES["gdelt"]["news_pressure"].expected_unit == (
        "normalized_article_pressure"
    )


def test_latest_model_candidates_pass_without_flagging_stored_history():
    latest = yahoo_observations()
    old_history = tuple(
        observation(
            row.source,
            row.country,
            row.metric,
            value=row.value - 1.0,
            unit=row.unit,
            period_end=AS_OF - timedelta(days=100),
        )
        for row in latest
    )

    result = run_quality_gates(
        {"yahoo": old_history + latest},
        as_of=AS_OF,
        economies=COUNTRIES,
    )

    assert result.report.status is QualityStatus.PASS
    assert result.report.input_observation_count == 8
    assert result.report.candidate_observation_count == 4
    assert result.report.accepted_observation_count == 4
    assert result.report.blocked_observation_count == 0
    assert result.report.issues == ()
    assert len(result.by_source()["yahoo"]) == 4


def test_stale_candidate_is_rejected_and_all_or_none_metric_falls_back():
    result = run_quality_gates(
        {
            "yahoo": yahoo_observations(
                equity_canada_end=AS_OF - timedelta(days=46),
            )
        },
        as_of=AS_OF,
        economies=COUNTRIES,
    )

    assert result.report.status is QualityStatus.DEGRADED
    assert result.report.candidate_observation_count == 4
    assert result.report.accepted_observation_count == 2
    assert result.report.blocked_observation_count == 2
    assert dict(result.report.issue_counts)["freshness"] == 1
    assert dict(result.report.issue_counts)["coverage"] == 1
    assert {row.metric for row in result.by_source()["yahoo"]} == {"fx_3m_return"}


def test_unit_and_range_failures_block_both_cross_sectional_metrics():
    result = run_quality_gates(
        {
            "yahoo": yahoo_observations(
                equity_canada_unit="decimal_return",
                fx_japan_value=900.0,
            )
        },
        as_of=AS_OF,
        economies=COUNTRIES,
    )

    counts = dict(result.report.issue_counts)
    assert counts["unit"] == 1
    assert counts["range"] == 1
    assert counts["coverage"] == 2
    assert result.report.accepted_observation_count == 0
    assert result.report.blocked_observation_count == 4
    assert all(
        decision.action is FallbackAction.FALLBACK
        for decision in result.report.coverage_decisions
    )


def test_future_realized_period_fails_date_alignment():
    rows = (
        observation(
            "gdelt",
            "Canada",
            "news_pressure",
            value=1.0,
            unit="normalized_article_pressure",
            period_end=AS_OF + timedelta(days=1),
        ),
        observation(
            "gdelt",
            "Japan",
            "news_pressure",
            value=2.0,
            unit="normalized_article_pressure",
            period_end=AS_OF - timedelta(days=1),
        ),
    )

    result = run_quality_gates(
        {"gdelt": rows},
        as_of=AS_OF,
        economies=COUNTRIES,
    )

    assert dict(result.report.issue_counts)["date_alignment"] == 1
    assert result.report.accepted_observation_count == 0
    assert result.report.issues[0].code == "future_realized_period"


def test_partial_coverage_gate_discards_all_or_none_source_values():
    canada = observation(
        "gdelt",
        "Canada",
        "news_pressure",
        value=1.0,
        unit="normalized_article_pressure",
        period_end=AS_OF - timedelta(days=1),
    )

    result = run_quality_gates(
        {"gdelt": (canada,)},
        as_of=AS_OF,
        economies=COUNTRIES,
    )

    assert result.report.candidate_observation_count == 1
    assert result.report.accepted_observation_count == 0
    assert result.report.blocked_observation_count == 1
    issue = next(issue for issue in result.report.issues if issue.gate is QualityGate.COVERAGE)
    assert issue.action is GateAction.FALLBACK_METRIC
    assert result.report.coverage_decisions[0].action is FallbackAction.FALLBACK


def test_per_cell_coverage_retains_valid_world_bank_countries():
    units = {
        "inflation_yoy": "percent_yoy",
        "gdp_growth": "percent_yoy",
        "unemployment": "percent_labor_force",
    }
    canada_only = tuple(
        observation(
            "world_bank",
            "Canada",
            metric,
            value=5.0,
            unit=unit,
            period_end=datetime(2025, 12, 31, tzinfo=UTC),
            year=2025,
        )
        for metric, unit in units.items()
    )

    result = run_quality_gates(
        {"world_bank": canada_only},
        as_of=AS_OF,
        economies=COUNTRIES,
    )

    assert result.report.accepted_observation_count == 3
    assert result.report.blocked_observation_count == 0
    assert all(
        decision.action is FallbackAction.MIXED
        for decision in result.report.coverage_decisions
    )
    coverage_issues = [
        issue for issue in result.report.issues if issue.gate is QualityGate.COVERAGE
    ]
    assert len(coverage_issues) == 3
    assert {issue.action for issue in coverage_issues} == {GateAction.RETAIN_PARTIAL}


def test_imf_forecast_must_align_to_quality_approved_actual_year_plus_one():
    units = {
        "inflation_yoy": "percent_yoy",
        "gdp_growth": "percent_yoy",
        "unemployment": "percent_labor_force",
    }
    world_bank_rows = tuple(
        observation(
            "world_bank",
            country,
            metric,
            value=5.0,
            unit=unit,
            period_end=datetime(2025, 12, 31, tzinfo=UTC),
            year=2025,
        )
        for country in COUNTRIES
        for metric, unit in units.items()
    )
    imf_rows = tuple(
        observation(
            "imf_weo",
            country,
            metric,
            value=6.0,
            unit=unit,
            period_end=datetime(
                2027 if (country, metric) == ("Canada", "gdp_growth") else 2026,
                12,
                31,
                tzinfo=UTC,
            ),
            year=2027 if (country, metric) == ("Canada", "gdp_growth") else 2026,
        )
        for country in COUNTRIES
        for metric, unit in units.items()
    )

    result = run_quality_gates(
        {"world_bank": world_bank_rows, "imf_weo": imf_rows},
        as_of=AS_OF,
        economies=COUNTRIES,
    )

    alignment = [
        issue
        for issue in result.report.issues
        if issue.gate is QualityGate.DATE_ALIGNMENT
    ]
    assert len(alignment) == 1
    assert alignment[0].code == "missing_target_period"
    assert alignment[0].country == "Canada"
    assert alignment[0].metric == "gdp_growth"
    accepted_imf = result.by_source()["imf_weo"]
    assert len(accepted_imf) == 4
    assert {row.metric for row in accepted_imf} == {
        "inflation_yoy",
        "unemployment",
    }


def test_quality_report_is_serializable_and_unknown_source_is_rejected():
    result = run_quality_gates(
        {"gdelt": ()},
        as_of=AS_OF,
        economies=COUNTRIES,
    )
    payload = result.report.to_dict()
    assert payload["policy_version"] == "v1"
    assert payload["status"] == "degraded"
    assert payload["issue_counts"]["coverage"] == 1
    assert payload["coverage_decisions"][0]["action"] == "fallback"

    with pytest.raises(KeyError, match="No quality rules"):
        run_quality_gates(
            {"unknown": ()},
            as_of=AS_OF,
            economies=COUNTRIES,
        )
