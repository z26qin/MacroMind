import pytest

import regime_engine as re_eng

EXPECTED_UNIVERSE = ["Argentina", "Greece", "Turkey", "Japan", "China", "Brazil"]


TH = {"deteriorating_max": -0.10, "repricing_gap": 0.30, "active_min": 0.10, "confirmation_min": 0.25}


def test_regime_verdict_ladder_with_confirmation():
    assert re_eng.regime_verdict(-0.5, 0.0, 1.0, TH) == "Deteriorating"
    assert re_eng.regime_verdict(0.6, 0.4, 1.0, TH) == "Repricing"
    assert re_eng.regime_verdict(0.5, 0.15, 1.0, TH) == "Early"
    assert re_eng.regime_verdict(0.5, 0.0, 1.0, TH) == "Priced in"
    assert re_eng.regime_verdict(0.05, 0.0, 1.0, TH) == "Neutral"


def test_low_confirmation_downgrades_activation_verdicts_only():
    weak = 0.10  # below the 0.25 gate
    assert re_eng.regime_verdict(0.6, 0.4, weak, TH) == "Unconfirmed"   # was Repricing
    assert re_eng.regime_verdict(0.5, 0.15, weak, TH) == "Unconfirmed"  # was Early
    # Non-activation verdicts are not gated
    assert re_eng.regime_verdict(-0.5, 0.0, weak, TH) == "Deteriorating"
    assert re_eng.regime_verdict(0.5, 0.0, weak, TH) == "Priced in"
    assert re_eng.regime_verdict(0.05, 0.0, weak, TH) == "Neutral"
    # Boundary: exactly at the gate counts as confirmed
    assert re_eng.regime_verdict(0.6, 0.4, 0.25, TH) == "Repricing"


def test_turkey_is_unconfirmed_with_mock_data():
    cfg = re_eng.load_regime_config()
    df = re_eng.load_regime_inputs()
    scores = re_eng.compute_regime_scores(df, cfg)
    assert scores["Turkey"]["verdict"] == "Unconfirmed"
    assert scores["Turkey"]["confirmation_score"] == pytest.approx(0.2286, abs=1e-4)
    # The other five verdicts are unchanged by the gate
    assert scores["Argentina"]["verdict"] == "Repricing"
    assert scores["Greece"]["verdict"] == "Early"
    assert scores["Japan"]["verdict"] == "Priced in"
    assert scores["China"]["verdict"] == "Deteriorating"
    assert scores["Brazil"]["verdict"] == "Priced in"


def test_load_regime_config_requires_confirmation_min(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "regime_weights: {policy: 0.3, liquidity: 0.2, foreign_access: 0.2, rating_momentum: 0.2, index_catalyst: 0.1}\n"
        "verdict: {deteriorating_max: -0.1, repricing_gap: 0.3, active_min: 0.1}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="confirmation_min"):
        re_eng.load_regime_config(bad)


def test_load_regime_inputs_has_six_countries():
    df = re_eng.load_regime_inputs()
    assert list(df.index) == EXPECTED_UNIVERSE
    assert not df.isna().any().any()


def test_load_regime_config_has_weights_and_thresholds():
    cfg = re_eng.load_regime_config()
    assert set(cfg["regime_weights"]) >= set(re_eng.STRUCTURAL_BUCKETS)
    assert {"deteriorating_max", "repricing_gap", "active_min"} <= set(cfg["verdict"])


def test_load_regime_templates_covers_universe():
    tpl = re_eng.load_regime_templates()
    for country in EXPECTED_UNIVERSE:
        assert {"drivers", "best_expressions", "left_tail_risks"} <= set(tpl[country])


def test_compute_regime_scores_argentina():
    cfg = re_eng.load_regime_config()
    df = re_eng.load_regime_inputs()
    scores = re_eng.compute_regime_scores(df, cfg)
    arg = scores["Argentina"]
    assert arg["regime_score"] == pytest.approx(0.655, abs=1e-4)
    assert arg["narrative_gap"] == pytest.approx(0.605, abs=1e-4)
    assert arg["verdict"] == "Repricing"
    assert set(arg["buckets"]) == set(re_eng.STRUCTURAL_BUCKETS)
    assert set(arg["cross_asset_confirmation"]) == set(re_eng.CROSS_ASSET_CHANNELS)


def test_compute_regime_scores_china_deteriorating():
    cfg = re_eng.load_regime_config()
    df = re_eng.load_regime_inputs()
    scores = re_eng.compute_regime_scores(df, cfg)
    assert scores["China"]["verdict"] == "Deteriorating"
    assert -1.0 <= scores["China"]["confirmation_score"] <= 1.0


@pytest.fixture()
def snapshot(tmp_path):
    return re_eng.generate_regime_snapshot(tmp_path / "regime_snapshot.json", as_of="2026-06-03")


def test_top_level_schema(snapshot):
    assert set(snapshot) == {"as_of", "methodology_version", "regime_universe", "countries"}
    assert snapshot["regime_universe"] == EXPECTED_UNIVERSE
    assert list(snapshot["countries"]) == EXPECTED_UNIVERSE


def test_narrative_gap_identity(snapshot):
    for c in snapshot["countries"].values():
        assert c["narrative_gap"] == pytest.approx(c["regime_score"] - c["narrative_score"], abs=1e-4)


def test_templates_attached(snapshot):
    for c in snapshot["countries"].values():
        assert c["drivers"] and c["best_expressions"] and c["left_tail_risks"]


def test_deterministic(tmp_path):
    a = re_eng.generate_regime_snapshot(tmp_path / "a.json", as_of="2026-06-03")
    b = re_eng.generate_regime_snapshot(tmp_path / "b.json", as_of="2026-06-03")
    assert a == b
