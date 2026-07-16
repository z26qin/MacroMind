import snapshot_diff


def make_regime_country(name, verdict="Neutral", regime=0.0, gap=0.0, conf=0.0):
    return {
        "country": name,
        "verdict": verdict,
        "regime_score": regime,
        "narrative_gap": gap,
        "confirmation_score": conf,
    }


def make_economy(cells=None, composite=0.0, provenance=None):
    """cells: {"equity": 0.3} -> full signal blocks with defaults."""
    signals = {}
    for asset in ("fx", "rates", "equity", "real_estate"):
        value = (cells or {}).get(asset, 0.0)
        signals[asset] = {
            "final": value,
            "rag_confidence": 0.0,
            "rag_analysis": {"evidence_count": 0, "citations": []},
        }
    return {
        "signals": signals,
        "composite": {"final": composite},
        "provenance": provenance or {},
    }


def make_side(snapshot_id="base", methodology="1.0", economies=None, countries=None):
    return {
        "id": snapshot_id,
        "signal": {
            "as_of": "2026-07-08",
            "methodology_version": methodology,
            "economies": economies or {},
        },
        "regime": {
            "methodology_version": methodology,
            "countries": countries or [],
        },
    }


def kinds(diff):
    return [(c["kind"], c["country"]) for c in diff["changes"]]


def test_verdict_flip_is_level_1():
    base = make_side(countries=[make_regime_country("Brazil", "Unconfirmed", conf=0.19)])
    target = make_side("t", countries=[make_regime_country("Brazil", "Repricing", conf=0.31)])
    diff = snapshot_diff.compute_diff(base, target)
    flips = [c for c in diff["changes"] if c["kind"] == "verdict_flip"]
    assert len(flips) == 1
    assert flips[0]["level"] == 1
    assert flips[0]["from"] == "Unconfirmed" and flips[0]["to"] == "Repricing"
    assert flips[0]["detail"]["confirmation_score"] == {"from": 0.19, "to": 0.31}


def test_direction_flip_requires_crossing_band():
    base = make_side(economies={"Brazil": make_economy({"equity": -0.20})})
    flipped = make_side("t", economies={"Brazil": make_economy({"equity": 0.20})})
    diff = snapshot_diff.compute_diff(base, flipped)
    assert ("direction_flip", "Brazil") in kinds(diff)
    # 0.14 未到 +0.15 带 -> 不算翻转,按漂移处理
    not_crossed = make_side("t", economies={"Brazil": make_economy({"equity": 0.14})})
    diff2 = snapshot_diff.compute_diff(base, not_crossed)
    assert ("direction_flip", "Brazil") not in kinds(diff2)
    assert ("signal_drift", "Brazil") in kinds(diff2)


def test_drift_threshold_boundary():
    base = make_side(economies={"Japan": make_economy({"fx": 0.0})})
    below = make_side("t", economies={"Japan": make_economy({"fx": 0.099})})
    at = make_side("t", economies={"Japan": make_economy({"fx": 0.101})})
    assert ("signal_drift", "Japan") not in kinds(snapshot_diff.compute_diff(base, below))
    assert snapshot_diff.compute_diff(base, below)["minor_count"] >= 1
    assert ("signal_drift", "Japan") in kinds(snapshot_diff.compute_diff(base, at))


def test_regime_drift():
    base = make_side(countries=[make_regime_country("Turkey", gap=0.10)])
    target = make_side("t", countries=[make_regime_country("Turkey", gap=0.45)])
    entries = [c for c in snapshot_diff.compute_diff(base, target)["changes"]
               if c["kind"] == "regime_drift"]
    assert entries and entries[0]["field"] == "narrative_gap"
    assert entries[0]["level"] == 3


def test_methodology_change_suppresses_diff():
    base = make_side(methodology="1.0",
                     economies={"Brazil": make_economy({"equity": -0.5})})
    target = make_side("t", methodology="2.0",
                       economies={"Brazil": make_economy({"equity": 0.5})})
    diff = snapshot_diff.compute_diff(base, target)
    assert diff["changes"] == []
    assert any("methodology" in n for n in diff["notes"])


def test_missing_country_reports_coverage_change():
    base = make_side(countries=[make_regime_country("Greece")])
    target = make_side("t", countries=[])
    diff = snapshot_diff.compute_diff(base, target)
    assert ("coverage_change", "Greece") in kinds(diff)


def test_unchanged_count():
    econ = {"Japan": make_economy(), "Canada": make_economy()}
    diff = snapshot_diff.compute_diff(make_side(economies=econ), make_side("t", economies=econ))
    assert diff["changes"] == []
    assert diff["minor_count"] == 0
    assert diff["unchanged_count"] == 2


def test_regime_countries_accepts_dict_shape():
    # 真实 regime_snapshot.json 以国名为 key;list 形状也要兼容
    base = make_side(countries=[make_regime_country("Brazil", "Unconfirmed")])
    target = make_side("t")
    target["regime"]["countries"] = {
        "Brazil": make_regime_country("Brazil", "Repricing"),
    }
    diff = snapshot_diff.compute_diff(base, target)
    assert ("verdict_flip", "Brazil") in kinds(diff)
