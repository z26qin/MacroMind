import pytest

from signal_engine import ASSET_CLASSES, UNIVERSE, generate_snapshot


EXPECTED_UNIVERSE = [
    "United States of America",
    "Canada",
    "China",
    "Japan",
    "Brazil",
    "Euro Area",
]


@pytest.fixture()
def snapshot(tmp_path):
    return generate_snapshot(tmp_path / "snapshot.json", as_of="2026-06-02")


def test_snapshot_has_stable_top_level_schema(snapshot):
    assert set(snapshot) == {
        "as_of",
        "methodology_version",
        "data_source",
        "universe",
        "economies",
    }
    assert snapshot["as_of"] == "2026-06-02"
    assert snapshot["methodology_version"] == "v0.1"
    assert snapshot["data_source"] == "mock"


def test_snapshot_contains_exactly_six_expected_economies(snapshot):
    assert snapshot["universe"] == EXPECTED_UNIVERSE
    assert list(snapshot["economies"]) == EXPECTED_UNIVERSE
    assert len(snapshot["economies"]) == 6


def test_each_economy_has_four_asset_classes(snapshot):
    for economy in EXPECTED_UNIVERSE:
        signals = snapshot["economies"][economy]["signals"]
        assert set(signals) == set(ASSET_CLASSES)


def test_each_asset_signal_has_required_fields(snapshot):
    required_fields = {
        "deterministic",
        "rag",
        "final",
        "driver",
        "rag_summary",
    }
    for economy in snapshot["economies"].values():
        for signal in economy["signals"].values():
            assert required_fields <= set(signal)


def test_signal_values_are_clipped(snapshot):
    for economy in snapshot["economies"].values():
        for signal in economy["signals"].values():
            for key in ("deterministic", "rag", "final"):
                assert -1.0 <= signal[key] <= 1.0

        for key in ("deterministic", "rag", "final"):
            assert -1.0 <= economy["composite"][key] <= 1.0


def test_composites_equal_mean_of_asset_signals(snapshot):
    for economy in snapshot["economies"].values():
        for key in ("deterministic", "rag", "final"):
            values = [economy["signals"][asset][key] for asset in ASSET_CLASSES]
            assert economy["composite"][key] == pytest.approx(sum(values) / len(values), abs=1e-4)


def test_output_is_deterministic_across_runs(tmp_path):
    first = generate_snapshot(tmp_path / "first.json", as_of="2026-06-02")
    second = generate_snapshot(tmp_path / "second.json", as_of="2026-06-02")
    assert first == second


def test_euro_area_is_synthetic_economy(snapshot):
    euro_area = snapshot["economies"]["Euro Area"]
    assert euro_area["country"] == "Euro Area"
    assert euro_area["iso3"] == "EUR"


from data_sources import world_bank as wb
import signal_engine as se


def test_load_macro_inputs_mock_marks_all_provenance_mock():
    df, provenance, _expected_change = se.load_macro_inputs(source="mock")
    assert len(df) == 6
    for economy in EXPECTED_UNIVERSE:
        assert provenance[economy]["inflation_yoy"] == "mock"
        assert provenance[economy]["inflation_consensus"] == "mock"
        assert provenance[economy]["policy_rate"] == "mock"
        assert provenance[economy]["policy_rate_consensus"] == "mock"
        assert provenance[economy]["equity_3m_return"] == "mock"
        assert provenance[economy]["news_pressure"] == "mock"


def test_load_macro_inputs_live_overlays_world_bank_values():
    def fake_fetch(url):
        # Return one USA observation for whichever indicator is requested.
        for code in wb.WB_INDICATOR_BY_COLUMN.values():
            if code in url:
                return [
                    {"page": 1, "pages": 1, "per_page": 20000, "total": 2},
                    [
                        {"countryiso3code": "USA", "date": "2024", "value": 9.99},
                        {"countryiso3code": "USA", "date": "2023", "value": 1.11},
                    ],
                ]
        raise AssertionError(url)

    def fake_imf(url):
        # Empty IMF series for every indicator -> no network, columns fall back.
        indicator = url.rsplit("/", 1)[-1]
        return {"values": {indicator: {}}}

    df, provenance, _expected_change = se.load_macro_inputs(
        source="live", fetch_json=fake_fetch, imf_fetch_json=fake_imf
    )
    # USA live columns overlaid with the fake actual (9.99)
    assert df.loc["United States of America", "inflation_yoy"] == 9.99
    assert provenance["United States of America"]["inflation_yoy"] == "world_bank:2024"
    # Non-live columns stay mock
    assert provenance["United States of America"]["pmi"] == "mock"
    # Economy with no live rows (e.g. Japan) falls back to mock for everything
    assert provenance["Japan"]["inflation_yoy"] == "mock"
    # Frame is still complete (no NaNs) so downstream validation holds
    assert not df.isna().any().any()


def test_each_economy_reports_provenance(snapshot):
    for economy in snapshot["economies"].values():
        provenance = economy["provenance"]
        assert provenance["inflation_yoy"] == "mock"
        assert set(provenance) >= {
            "inflation_yoy", "inflation_consensus",
            "gdp_growth", "gdp_consensus",
            "unemployment", "unemployment_consensus",
            "policy_rate", "policy_rate_consensus",
            "pmi", "pmi_consensus",
            "fx_3m_return", "fx_carry", "equity_3m_return", "news_pressure",
        }


def test_generate_snapshot_records_requested_source(tmp_path):
    snap = generate_snapshot(tmp_path / "s.json", as_of="2026-06-02", source="mock")
    assert snap["data_source"] == "mock"


import pandas as pd


def _surprise_frame():
    return pd.DataFrame({
        "inflation_yoy": [3.0], "inflation_consensus": [2.5],
        "gdp_growth": [2.0], "gdp_consensus": [1.5],
        "unemployment": [4.0], "unemployment_consensus": [4.5],
        "policy_rate": [5.0], "policy_rate_consensus": [4.8],
        "pmi": [51.0], "pmi_consensus": [50.0],
    })


def test_add_surprises_default_is_actual_minus_consensus():
    out = se.add_surprises(_surprise_frame())
    assert out["inflation_surprise"].iloc[0] == pytest.approx(0.5)    # 3.0 - 2.5
    assert out["growth_surprise"].iloc[0] == pytest.approx(0.5)       # 2.0 - 1.5
    assert out["unemployment_surprise"].iloc[0] == pytest.approx(-0.5)
    assert out["policy_surprise"].iloc[0] == pytest.approx(0.2)


def test_add_surprises_expected_change_flips_named_columns_only():
    out = se.add_surprises(
        _surprise_frame(),
        expected_change_columns={"inflation_surprise", "growth_surprise", "unemployment_surprise"},
    )
    # forecast(consensus) - actual  => expected change
    assert out["inflation_surprise"].iloc[0] == pytest.approx(-0.5)   # 2.5 - 3.0
    assert out["growth_surprise"].iloc[0] == pytest.approx(-0.5)      # 1.5 - 2.0
    assert out["unemployment_surprise"].iloc[0] == pytest.approx(0.5) # 4.5 - 4.0
    # non-IMF columns stay beat/miss
    assert out["policy_surprise"].iloc[0] == pytest.approx(0.2)       # 5.0 - 4.8
    assert out["pmi_surprise"].iloc[0] == pytest.approx(1.0)          # 51 - 50


from data_sources import world_bank as wb_mod
from data_sources import imf_weo as imf_mod


def _wb_fake_all_six(url):
    """World Bank fake: actual(2024)=10.0, prior(2023)=9.0 for all six economies."""
    for code in wb_mod.WB_INDICATOR_BY_COLUMN.values():
        if code in url:
            rows = []
            for iso in wb_mod.WB_CODE_BY_ECONOMY.values():
                rows.append({"countryiso3code": iso, "date": "2024", "value": 10.0})
                rows.append({"countryiso3code": iso, "date": "2023", "value": 9.0})
            return [{"page": 1, "pages": 1, "per_page": 20000, "total": len(rows)}, rows]
    raise AssertionError(url)


def _imf_fake_all_six(url):
    """IMF fake: forecast(2025)=12.0 for every economy/indicator."""
    for ind in imf_mod.IMF_INDICATOR_BY_COLUMN.values():
        if url.endswith("/" + ind):
            by_code = {code: {"2024": 8.0, "2025": 12.0}
                       for code in imf_mod.IMF_CODE_BY_ECONOMY.values()}
            return {"values": {ind: by_code}}
    raise AssertionError(url)


def test_load_macro_inputs_mock_returns_empty_expected_change():
    df, provenance, expected_change = se.load_macro_inputs(source="mock")
    assert expected_change == frozenset()
    assert len(df) == 6


def test_load_macro_inputs_live_overlays_imf_consensus_and_marks_expected_change():
    df, provenance, expected_change = se.load_macro_inputs(
        source="live", fetch_json=_wb_fake_all_six, imf_fetch_json=_imf_fake_all_six,
    )
    assert expected_change == frozenset(
        {"inflation_surprise", "growth_surprise", "unemployment_surprise"}
    )
    usa = "United States of America"
    # consensus column holds the REAL IMF forecast (2025), not a naive baseline
    assert df.loc[usa, "inflation_consensus"] == 12.0
    assert df.loc[usa, "gdp_consensus"] == 12.0
    # actual still from World Bank
    assert df.loc[usa, "inflation_yoy"] == 10.0
    assert provenance[usa]["inflation_yoy"] == "world_bank:2024"
    assert provenance[usa]["inflation_consensus"] == "imf_weo:2025"
    # the resulting feature is the expected change forecast(T+1) - actual(T)
    out = se.add_surprises(df, expected_change)
    assert out.loc[usa, "inflation_surprise"] == pytest.approx(2.0)   # 12 - 10


def test_blend_signal_full_confidence_matches_legacy_weights():
    # confidence 1.0 -> 0.75*det + 0.25*rag
    assert se.blend_signal(0.8, 0.4, 1.0, 0.25) == pytest.approx(0.7)


def test_blend_signal_zero_confidence_ignores_rag():
    assert se.blend_signal(0.8, 0.4, 0.0, 0.25) == pytest.approx(0.8)


def test_blend_signal_partial_confidence_scales_rag():
    # effective_rag = 0.25*0.75 = 0.1875 -> 0.8125*0.8 + 0.1875*0.4
    assert se.blend_signal(0.8, 0.4, 0.75, 0.25) == pytest.approx(0.725)


def test_blend_signal_is_clipped():
    assert se.blend_signal(1.0, 1.0, 1.0, 0.25) == 1.0
    assert se.blend_signal(-1.0, -1.0, 1.0, 0.25) == -1.0


from data_sources import market as market_mod


def _market_chart(closes):
    ts = [1704067200, 1706745600, 1709251200, 1711929600]  # Jan-Apr 2024 UTC
    return {"chart": {"result": [{
        "timestamp": ts,
        "indicators": {"adjclose": [{"adjclose": closes}], "quote": [{"close": closes}]},
    }], "error": None}}


def _market_fake_all(url):
    # FX pairs (".=X") -> +5% ; equity ETFs -> +10%
    return _market_chart([1.00, 1.02, 1.04, 1.05]) if "=X" in url else _market_chart([100.0, 105.0, 108.0, 110.0])


def test_overlay_market_inputs_mock_is_noop():
    df, provenance, _ec = se.load_macro_inputs(source="mock")
    before = df["equity_3m_return"].tolist()
    se.overlay_market_inputs(df, provenance, source="mock")
    assert df["equity_3m_return"].tolist() == before
    assert provenance["United States of America"]["equity_3m_return"] == "mock"


def test_overlay_market_inputs_live_overlays_fx_and_equity():
    df, provenance, _ec = se.load_macro_inputs(source="mock")
    se.overlay_market_inputs(df, provenance, source="live", fetch_json=_market_fake_all)
    usa = "United States of America"
    assert df.loc[usa, "equity_3m_return"] == 10.0
    assert df.loc[usa, "fx_3m_return"] == 0.0            # numeraire
    assert df.loc["Canada", "fx_3m_return"] == 5.0
    assert df.loc["Euro Area", "equity_3m_return"] == 10.0
    assert provenance[usa]["equity_3m_return"] == "yahoo:2024-04"
    assert provenance[usa]["fx_3m_return"] == "yahoo:2024-04"


def test_overlay_news_pressure_mock_is_noop():
    df, provenance, _ec = se.load_macro_inputs(source="mock")
    before = df["news_pressure"].tolist()
    se.overlay_news_pressure(df, provenance, source="mock")
    assert df["news_pressure"].tolist() == before
    assert provenance["United States of America"]["news_pressure"] == "mock"


def test_overlay_news_pressure_live_overlays_when_all_economies_resolve():
    df, provenance, _ec = se.load_macro_inputs(source="mock")

    def fake_fetch(url):
        return {"articles": [{}, {}, {}]} if "policy+uncertainty" in url else {"articles": [{}]}

    se.overlay_news_pressure(df, provenance, source="live", fetch_json=fake_fetch)
    usa = "United States of America"
    assert df.loc[usa, "news_pressure"] > 0
    assert provenance[usa]["news_pressure"].startswith("gdelt:")


def test_news_pressure_rank_is_configured_for_each_asset_class():
    config = se.load_signal_config()
    for asset_weights in config["weights"].values():
        assert "news_pressure_rank" in asset_weights


def test_overlay_fx_carry_mock_is_noop():
    df, provenance, _ec = se.load_macro_inputs(source="mock")
    before = df["fx_carry"].tolist()
    se.overlay_fx_carry(df, provenance, source="mock")
    assert df["fx_carry"].tolist() == before
    assert provenance["United States of America"]["fx_carry"] == "mock"


def test_overlay_fx_carry_live_derives_from_policy_rate_diff():
    df, provenance, _ec = se.load_macro_inputs(source="mock")
    df.loc["United States of America", "policy_rate"] = 5.0
    df.loc["Brazil", "policy_rate"] = 11.0
    df.loc["Japan", "policy_rate"] = 0.5
    se.overlay_fx_carry(df, provenance, source="live")
    assert df.loc["United States of America", "fx_carry"] == 0.0   # numeraire
    assert df.loc["Brazil", "fx_carry"] == 6.0                     # 11.0 - 5.0
    assert df.loc["Japan", "fx_carry"] == -4.5                     # 0.5 - 5.0
    assert provenance["Brazil"]["fx_carry"] == "derived:policy_rate_diff"
    assert provenance["United States of America"]["fx_carry"] == "derived:policy_rate_diff"


def test_resolve_all_or_none_returns_map_when_every_economy_resolves():
    out = se.resolve_all_or_none(("a", "b", "c"), lambda economy: economy.upper())
    assert out == {"a": "A", "b": "B", "c": "C"}


def test_resolve_all_or_none_returns_none_when_any_economy_misses():
    seen = []

    def resolve(economy):
        seen.append(economy)
        return None if economy == "b" else economy.upper()

    assert se.resolve_all_or_none(("a", "b", "c"), resolve) is None
    assert seen == ["a", "b"]  # short-circuits on the first miss


def test_load_signal_config_rejects_blend_not_summing_to_one(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "weights:\n"
        "  fx: {growth_surprise_rank: 1.0}\n"
        "  rates: {inflation_surprise_rank: -1.0}\n"
        "  equity: {growth_surprise_rank: 1.0}\n"
        "  real_estate: {rate_3m_change_rank: -1.0}\n"
        "signal_blend:\n"
        "  deterministic_weight: 0.5\n"
        "  rag_weight: 0.25\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must sum to 1.0"):
        se.load_signal_config(bad)
