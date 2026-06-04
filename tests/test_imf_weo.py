from data_sources import imf_weo as imf


def test_fetch_indicator_maps_codes_parses_years_and_drops_nulls():
    payload = {"values": {"PCPIPCH": {
        "USA": {"2024": 2.9, "2025": 2.3, "2026": None},  # null year dropped
        "EURO": {"2024": 2.4, "2025": 2.1},
        "ZZZ": {"2024": 9.9},                              # unmapped code ignored
    }}}
    captured = {}

    def fake(url):
        captured["url"] = url
        return payload

    series = imf.fetch_indicator("inflation_yoy", fetch_json=fake)

    assert series["United States of America"] == {2024: 2.9, 2025: 2.3}
    assert series["Euro Area"] == {2024: 2.4, 2025: 2.1}
    assert "Canada" not in series          # economy with no data is absent
    assert captured["url"].endswith("/PCPIPCH")


def test_fetch_indicator_raises_on_bad_payload():
    def fake(url):
        return {"unexpected": True}
    try:
        imf.fetch_indicator("gdp_growth", fetch_json=fake)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_load_imf_forecasts_nests_by_economy_and_column():
    payloads = {
        "PCPIPCH": {"values": {"PCPIPCH": {"USA": {"2024": 2.9, "2025": 2.3}}}},
        "NGDP_RPCH": {"values": {"NGDP_RPCH": {"USA": {"2024": 2.8, "2025": 2.0}}}},
        "LUR": {"values": {"LUR": {"USA": {"2024": 4.0, "2025": 4.2}}}},
    }

    def fake(url):
        for ind, p in payloads.items():
            if url.endswith("/" + ind):
                return p
        raise AssertionError(url)

    out = imf.load_imf_forecasts(
        ("United States of America", "Canada"), fetch_json=fake
    )
    assert out["United States of America"]["inflation_yoy"] == {2024: 2.9, 2025: 2.3}
    assert out["United States of America"]["gdp_growth"] == {2024: 2.8, 2025: 2.0}
    assert out["Canada"] == {}             # present but empty -> caller falls back
