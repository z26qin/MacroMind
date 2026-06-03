import pytest

import regime_engine as re_eng

EXPECTED_UNIVERSE = ["Argentina", "Greece", "Turkey", "Japan", "China", "Brazil"]


def test_regime_verdict_ladder():
    th = {"deteriorating_max": -0.10, "repricing_gap": 0.30, "active_min": 0.10}
    assert re_eng.regime_verdict(-0.5, 0.0, th) == "Deteriorating"
    assert re_eng.regime_verdict(0.6, 0.4, th) == "Repricing"
    assert re_eng.regime_verdict(0.5, 0.15, th) == "Early"
    assert re_eng.regime_verdict(0.5, 0.0, th) == "Priced in"
    assert re_eng.regime_verdict(0.05, 0.0, th) == "Neutral"


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
