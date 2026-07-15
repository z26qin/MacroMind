import json
from datetime import date as real_date
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from data_sources import gdelt, imf_weo, world_bank
from evidence_store import EvidenceStore
from pipeline.contracts import RunMode
from pipeline.orchestrator import run_signal_pipeline
from pipeline.store import ObservationStore


UTC = timezone.utc
NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def fixed_clock():
    return NOW


def world_bank_fetch(url):
    indicator = next(
        code for code in world_bank.WB_INDICATOR_BY_COLUMN.values() if code in url
    )
    rows = []
    for code in world_bank.WB_CODE_BY_ECONOMY.values():
        rows.extend(
            (
                {
                    "indicator": {"id": indicator},
                    "countryiso3code": code,
                    "date": "2024",
                    "value": 10.0,
                },
                {
                    "indicator": {"id": indicator},
                    "countryiso3code": code,
                    "date": "2023",
                    "value": 9.0,
                },
            )
        )
    return [{"page": 1, "pages": 1, "total": len(rows)}, rows]


def imf_fetch(url):
    indicator = url.rsplit("/", 1)[-1]
    assert indicator in imf_weo.IMF_INDICATOR_BY_COLUMN.values()
    values = {
        code: {"2024": 8.0, "2025": 12.0}
        for code in imf_weo.IMF_CODE_BY_ECONOMY.values()
    }
    return {"values": {indicator: values}}


def market_fetch(_url):
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
            ],
            "error": None,
        }
    }


def gdelt_fetch(url):
    query = parse_qs(urlparse(url).query)["query"][0]
    count = 4 if "policy uncertainty" in query else 1
    return {"articles": [{} for _ in range(count)]}


def freeze_gdelt_date(monkeypatch):
    class FixedDate:
        @staticmethod
        def today():
            return real_date(2026, 7, 15)

        @staticmethod
        def fromisoformat(value):
            return real_date.fromisoformat(value)

    monkeypatch.setattr(gdelt, "date", FixedDate)


def run_live(tmp_path, monkeypatch, *, as_of, run_id):
    freeze_gdelt_date(monkeypatch)
    observation_store = ObservationStore(":memory:")
    evidence_store = EvidenceStore(":memory:")
    result = run_signal_pipeline(
        tmp_path / f"{run_id}.json",
        as_of=as_of,
        source="live",
        world_bank_fetch_json=world_bank_fetch,
        imf_fetch_json=imf_fetch,
        market_fetch_json=market_fetch,
        gdelt_fetch_json=gdelt_fetch,
        observation_store=observation_store,
        evidence_store=evidence_store,
        clock=fixed_clock,
        run_id=run_id,
    )
    return result, observation_store, evidence_store


def test_mock_run_exposes_explicit_stage_sequence(tmp_path):
    path = tmp_path / "mock.json"
    with EvidenceStore(":memory:") as evidence_store:
        result = run_signal_pipeline(
            path,
            as_of="2026-07-15",
            source="mock",
            evidence_store=evidence_store,
            clock=fixed_clock,
            run_id="mock-stage-run",
        )

    assert result.context.mode is RunMode.MOCK
    assert result.completed_stages == (
        "context",
        "config",
        "baseline",
        "features",
        "snapshot",
        "output",
    )
    assert result.batches == ()
    assert result.coverage is None
    assert result.store_write is None
    assert json.loads(path.read_text(encoding="utf-8")) == result.snapshot


def test_live_run_connects_adapters_coverage_store_and_pit_overlay(
    tmp_path,
    monkeypatch,
):
    result, observation_store, evidence_store = run_live(
        tmp_path,
        monkeypatch,
        as_of="2026-07-15",
        run_id="live-stage-run",
    )
    try:
        assert result.context.mode is RunMode.LIVE
        assert result.completed_stages == (
            "context",
            "config",
            "baseline",
            "acquire",
            "coverage",
            "persist",
            "pit_select",
            "overlay",
            "features",
            "snapshot",
            "output",
        )
        assert tuple(batch.source for batch in result.batches) == (
            "world_bank",
            "imf_weo",
            "yahoo",
            "gdelt",
        )
        assert result.coverage is not None
        assert tuple(report.source for report in result.coverage.sources) == (
            "world_bank",
            "imf_weo",
            "yahoo",
            "gdelt",
        )
        assert result.store_write is not None
        assert result.store_write.inserted_batches == 4
        assert observation_store.counts() == {
            "pipeline_runs": 1,
            "source_batches": 4,
            "source_errors": 0,
            "observations": 90,
        }

        provenance = result.snapshot["economies"]["Canada"]["provenance"]
        assert provenance["inflation_yoy"] == "world_bank:2024"
        assert provenance["inflation_consensus"] == "imf_weo:2025"
        assert provenance["equity_3m_return"] == "yahoo:2024-04"
        assert provenance["news_pressure"] == "gdelt:2026-07-15"
    finally:
        observation_store.close()
        evidence_store.close()


def test_historical_run_persists_new_data_but_cannot_see_it_as_of_the_past(
    tmp_path,
    monkeypatch,
):
    result, observation_store, evidence_store = run_live(
        tmp_path,
        monkeypatch,
        as_of="2025-07-15",
        run_id="historical-stage-run",
    )
    try:
        assert result.context.mode is RunMode.HISTORICAL
        assert result.store_write is not None
        assert result.store_write.inserted_observations == 90
        assert observation_store.query_as_of(result.context.as_of) == ()

        provenance = result.snapshot["economies"]["Canada"]["provenance"]
        assert provenance["inflation_yoy"] == "mock"
        assert provenance["inflation_consensus"] == "mock"
        assert provenance["equity_3m_return"] == "mock"
        assert provenance["news_pressure"] == "mock"
    finally:
        observation_store.close()
        evidence_store.close()
