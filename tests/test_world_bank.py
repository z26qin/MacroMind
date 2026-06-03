from data_sources import world_bank as wb


def _payload(rows):
    """Build a World Bank-shaped [metadata, observations] response."""
    return [{"page": 1, "pages": 1, "per_page": 20000, "total": len(rows)}, rows]


def _obs(iso3, year, value):
    return {
        "indicator": {"id": "X", "value": "X"},
        "countryiso3code": iso3,
        "date": str(year),
        "value": value,
    }


def test_fetch_indicator_groups_by_economy_and_drops_nulls():
    rows = [
        _obs("USA", 2024, 2.9),
        _obs("USA", 2023, 4.1),
        _obs("USA", 2025, None),   # null dropped
        _obs("EMU", 2024, None),   # whole-series null -> empty list
    ]
    captured = {}

    def fake_fetch(url):
        captured["url"] = url
        return _payload(rows)

    series = wb.fetch_indicator(
        "inflation_yoy", 2018, 2026, fetch_json=fake_fetch
    )

    assert series["United States of America"] == [(2024, 2.9), (2023, 4.1)]
    assert series["Euro Area"] == []
    assert series["Canada"] == []  # economy with no rows still present
    assert "FP.CPI.TOTL.ZG" in captured["url"]
    assert "date=2018:2026" in captured["url"]


def test_fetch_indicator_raises_on_error_payload():
    def fake_fetch(url):
        return [{"message": [{"id": "120", "value": "bad code"}]}]

    try:
        wb.fetch_indicator("gdp_growth", 2018, 2026, fetch_json=fake_fetch)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_latest_and_baseline_uses_latest_actual_and_prior_mean():
    history = [(2024, 2.9), (2023, 4.1), (2022, 8.0), (2021, 4.7)]
    actual, consensus, year = wb.latest_and_baseline(history, baseline_window=3)
    assert actual == 2.9
    assert year == 2024
    assert consensus == (4.1 + 8.0 + 4.7) / 3


def test_latest_and_baseline_single_point_consensus_equals_actual():
    actual, consensus, year = wb.latest_and_baseline([(2024, 2.9)])
    assert actual == 2.9
    assert consensus == 2.9
    assert year == 2024


def test_latest_and_baseline_empty_returns_none():
    assert wb.latest_and_baseline([]) is None
