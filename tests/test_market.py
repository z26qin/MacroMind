from data_sources import market


def _chart_payload(timestamps, closes, with_adjclose=True):
    indicators = {"quote": [{"close": closes}]}
    if with_adjclose:
        indicators["adjclose"] = [{"adjclose": closes}]
    return {"chart": {"result": [{"timestamp": timestamps, "indicators": indicators}], "error": None}}


# Jan, Feb, Mar, Apr 2024 (UTC month starts)
TS = [1704067200, 1706745600, 1709251200, 1711929600]


def test_fetch_3m_return_computes_from_monthly_closes():
    payload = _chart_payload(TS, [100.0, 105.0, 108.0, 110.0])  # 110/100 - 1 = 10%
    captured = {}

    def fake(url):
        captured["url"] = url
        return payload

    ret, asof = market.fetch_3m_return("SPY", fetch_json=fake)
    assert ret == 10.0
    assert asof == "2024-04"
    assert "SPY" in captured["url"]


def test_fetch_3m_return_drops_nulls_and_needs_four_bars():
    payload = _chart_payload([1, 2, 3], [100.0, None, 103.0])  # only 2 usable bars
    assert market.fetch_3m_return("X", fetch_json=lambda u: payload) is None


def test_fetch_3m_return_falls_back_to_quote_close():
    payload = _chart_payload(TS, [10.0, 11.0, 12.0, 13.0], with_adjclose=False)
    ret, asof = market.fetch_3m_return("EZU", fetch_json=lambda u: payload)
    assert ret == 30.0  # 13/10 - 1
    assert asof == "2024-04"


def test_load_market_returns_assembles_equity_fx_and_us_numeraire():
    equity = _chart_payload(TS, [100.0, 105.0, 108.0, 110.0])  # +10%
    fx = _chart_payload(TS, [1.00, 1.02, 1.04, 1.05])          # +5%

    def fake(url):
        return fx if "=X" in url else equity

    economies = tuple(market.EQUITY_TICKER_BY_ECONOMY)  # all six
    out = market.load_market_returns(economies, fetch_json=fake)

    us = "United States of America"
    assert out[us]["equity_3m_return"] == (10.0, "2024-04")
    assert out[us]["fx_3m_return"] == (0.0, "2024-04")          # numeraire, dated to US equity
    assert out["Canada"]["equity_3m_return"][0] == 10.0
    assert out["Canada"]["fx_3m_return"][0] == 5.0
    assert out["Euro Area"]["fx_3m_return"][0] == 5.0
