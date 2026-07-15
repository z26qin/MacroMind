from datetime import datetime, timedelta, timezone
from inspect import signature
from urllib.parse import parse_qs, urlparse

import pytest

from data_sources import gdelt, imf_weo, market, world_bank
from pipeline.contracts import (
    DataFrequency,
    PipelineRunContext,
    RunMode,
    SourceBatch,
    SourceStatus,
)


UTC = timezone.utc
T0 = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
USA = "United States of America"
CONTEXT = PipelineRunContext(
    run_id="run-live-contracts",
    as_of=T0,
    started_at=T0,
    mode=RunMode.LIVE,
    methodology_version="v0.2",
    config_hash="test-config",
)


def fixed_clock():
    return T0


def test_all_live_adapters_expose_the_same_contract_entrypoint():
    for adapter in (world_bank, imf_weo, market, gdelt):
        parameters = tuple(signature(adapter.load_observations).parameters.values())
        assert tuple(parameter.name for parameter in parameters[:2]) == (
            "context",
            "economies",
        )
        assert all(
            parameter.default is not parameter.empty
            for parameter in parameters[2:]
        )


def _world_bank_payload(indicator: str, value: float):
    return [
        {"page": 1, "pages": 1, "total": 1},
        [
            {
                "indicator": {"id": indicator},
                "countryiso3code": "USA",
                "date": "2024",
                "value": value,
            }
        ],
    ]


def test_world_bank_loads_canonical_annual_observations():
    values = {
        "FP.CPI.TOTL.ZG": 2.9,
        "NY.GDP.MKTP.KD.ZG": 2.8,
        "SL.UEM.TOTL.ZS": 4.1,
    }

    def fake_fetch(url):
        indicator = next(code for code in values if code in url)
        return _world_bank_payload(indicator, values[indicator])

    batch = world_bank.load_observations(
        CONTEXT,
        (USA,),
        start_year=2024,
        end_year=2024,
        fetch_json=fake_fetch,
        clock=fixed_clock,
    )

    assert isinstance(batch, SourceBatch)
    assert batch.source == "world_bank"
    assert batch.run_id == CONTEXT.run_id
    assert batch.status is SourceStatus.SUCCESS
    assert len(batch.observations) == 3
    inflation = next(obs for obs in batch.observations if obs.metric == "inflation_yoy")
    assert inflation.value == 2.9
    assert inflation.unit == "percent_yoy"
    assert inflation.frequency is DataFrequency.ANNUAL
    assert inflation.period_start.year == 2024
    assert inflation.event_time is None
    assert inflation.observed_at == T0


def test_world_bank_converts_source_failure_to_structured_errors():
    def fail(_url):
        raise TimeoutError("upstream timeout")

    batch = world_bank.load_observations(
        CONTEXT,
        (USA,),
        start_year=2024,
        end_year=2024,
        fetch_json=fail,
        clock=fixed_clock,
    )

    assert batch.status is SourceStatus.FAILED
    assert len(batch.errors) == 3
    assert {error.code for error in batch.errors} == {"fetch_failed"}
    assert all(error.retryable for error in batch.errors)


def _imf_payload(indicator: str, value: float, include_canada: bool = False):
    values = {"USA": {"2025": value}}
    if include_canada:
        values["CAN"] = {"2025": value}
    return {"values": {indicator: values}}


def test_imf_loads_canonical_forecast_observations():
    values = {"PCPIPCH": 2.3, "NGDP_RPCH": 2.0, "LUR": 4.2}

    def fake_fetch(url):
        indicator = url.rsplit("/", 1)[-1]
        return _imf_payload(indicator, values[indicator])

    batch = imf_weo.load_observations(
        CONTEXT,
        (USA,),
        fetch_json=fake_fetch,
        clock=fixed_clock,
    )

    assert batch.source == "imf_weo"
    assert batch.status is SourceStatus.SUCCESS
    assert len(batch.observations) == 3
    assert {obs.period_start.year for obs in batch.observations} == {2025}
    assert all(obs.event_time is None for obs in batch.observations)


def test_imf_reports_partial_country_coverage():
    values = {"PCPIPCH": 2.3, "NGDP_RPCH": 2.0, "LUR": 4.2}

    def fake_fetch(url):
        indicator = url.rsplit("/", 1)[-1]
        return _imf_payload(indicator, values[indicator])

    batch = imf_weo.load_observations(
        CONTEXT,
        (USA, "Canada"),
        fetch_json=fake_fetch,
        clock=fixed_clock,
    )

    assert batch.status is SourceStatus.PARTIAL
    assert batch.coverage == 0.5
    assert {error.country for error in batch.errors} == {"Canada"}
    assert {error.metric for error in batch.errors} == set(imf_weo.FORECAST_COLUMNS)


def _market_payload():
    timestamps = [1704067200, 1706745600, 1709251200, 1711929600]
    closes = [100.0, 105.0, 108.0, 110.0]
    return {
        "chart": {
            "result": [
                {
                    "timestamp": timestamps,
                    "indicators": {
                        "adjclose": [{"adjclose": closes}],
                        "quote": [{"close": closes}],
                    },
                }
            ]
        }
    }


def test_market_loads_equity_and_derived_us_numeraire_observations():
    batch = market.load_observations(
        CONTEXT,
        (USA,),
        fetch_json=lambda _url: _market_payload(),
        clock=fixed_clock,
    )

    assert batch.source == "yahoo"
    assert batch.status is SourceStatus.SUCCESS
    by_metric = {obs.metric: obs for obs in batch.observations}
    assert by_metric["equity_3m_return"].value == 10.0
    assert by_metric["equity_3m_return"].revision == "adjusted_close"
    assert by_metric["fx_3m_return"].value == 0.0
    assert by_metric["fx_3m_return"].revision == "usd_numeraire"
    assert by_metric["equity_3m_return"].event_time == datetime(
        2024, 4, 1, tzinfo=UTC
    )


def test_market_converts_fetch_failure_and_dependency_to_errors():
    def fail(_url):
        raise TimeoutError("Yahoo timeout")

    batch = market.load_observations(
        CONTEXT,
        (USA,),
        fetch_json=fail,
        clock=fixed_clock,
    )

    assert batch.status is SourceStatus.FAILED
    assert {error.code for error in batch.errors} == {
        "fetch_failed",
        "dependency_missing",
    }


def _freeze_gdelt_date(monkeypatch):
    real_date = gdelt.date

    class FakeDate:
        @staticmethod
        def today():
            return real_date(2026, 7, 15)

        @staticmethod
        def fromisoformat(value):
            return real_date.fromisoformat(value)

    monkeypatch.setattr(gdelt, "date", FakeDate)


def test_gdelt_loads_canonical_window_observation(monkeypatch):
    _freeze_gdelt_date(monkeypatch)

    def fake_fetch(url):
        query = parse_qs(urlparse(url).query)["query"][0]
        return {"articles": [{}, {}, {}, {}]} if "policy uncertainty" in query else {
            "articles": [{}]
        }

    batch = gdelt.load_observations(
        CONTEXT,
        ("Canada",),
        fetch_json=fake_fetch,
        clock=fixed_clock,
    )

    assert batch.source == "gdelt"
    assert batch.status is SourceStatus.SUCCESS
    observation = batch.observations[0]
    assert observation.metric == "news_pressure"
    assert observation.value == pytest.approx(1.3416)
    assert observation.event_time == datetime(2026, 7, 15, tzinfo=UTC)
    assert observation.period_end - observation.period_start == timedelta(days=7)
    assert observation.revision.startswith("terms:")


def test_gdelt_converts_failed_economy_to_structured_error(monkeypatch):
    _freeze_gdelt_date(monkeypatch)

    def fail(_url):
        raise RuntimeError("GDELT unavailable")

    batch = gdelt.load_observations(
        CONTEXT,
        ("Canada",),
        fetch_json=fail,
        clock=fixed_clock,
    )

    assert batch.status is SourceStatus.FAILED
    assert batch.errors[0].code == "fetch_failed"
    assert batch.errors[0].retryable is True
