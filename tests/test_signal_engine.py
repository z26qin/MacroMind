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
