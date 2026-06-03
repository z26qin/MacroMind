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
    assert set(snapshot) == {"as_of", "methodology_version", "universe", "economies"}
    assert snapshot["as_of"] == "2026-06-02"
    assert snapshot["methodology_version"] == "v0.1"


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
    df, provenance = se.load_macro_inputs(source="mock")
    assert len(df) == 6
    for economy in EXPECTED_UNIVERSE:
        assert provenance[economy]["inflation_yoy"] == "mock"
        assert provenance[economy]["policy_rate"] == "mock"


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

    df, provenance = se.load_macro_inputs(source="live", fetch_json=fake_fetch)
    # USA live columns overlaid with the fake actual (9.99)
    assert df.loc["United States of America", "inflation_yoy"] == 9.99
    assert provenance["United States of America"]["inflation_yoy"] == "world_bank:2024"
    # Non-live columns stay mock
    assert provenance["United States of America"]["pmi"] == "mock"
    # Economy with no live rows (e.g. Japan) falls back to mock for everything
    assert provenance["Japan"]["inflation_yoy"] == "mock"
    # Frame is still complete (no NaNs) so downstream validation holds
    assert not df.isna().any().any()
