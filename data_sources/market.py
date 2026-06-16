"""Live market-return data from Yahoo Finance (free, no API key).

Sources trailing 3-month price returns for the signal universe from Yahoo's
public chart endpoint and shapes them to the engine's market columns:
- equity_3m_return : USD country-equity ETF return
- fx_3m_return     : local currency vs USD (positive = local appreciation);
                     the US is the numeraire, so its FX return is 0.0

We use Yahoo's chart JSON over httpx (not the `yfinance` package) to match the
World Bank / IMF adapters: an injectable ``fetch_json`` keeps tests offline.
NOTE: Yahoo requires a browser User-Agent (no-UA requests get HTTP 429) and
rate-limits bursts, so the default fetch sends a UA and retries on 429.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from data_sources.http import fetch_json as http_fetch_json

YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"

EQUITY_TICKER_BY_ECONOMY = {
    "United States of America": "SPY",
    "Canada": "EWC",
    "China": "MCHI",
    "Japan": "EWJ",
    "Brazil": "EWZ",
    "Euro Area": "EZU",
}

# US is the numeraire (no pair); every other economy quotes local-per-USD.
FX_TICKER_BY_ECONOMY = {
    "Canada": "CADUSD=X",
    "China": "CNYUSD=X",
    "Japan": "JPYUSD=X",
    "Brazil": "BRLUSD=X",
    "Euro Area": "EURUSD=X",
}

US_ECONOMY = "United States of America"


def _default_fetch_json(url: str) -> dict:
    # Yahoo needs a browser UA (no-UA -> 429) and rate-limits bursts.
    return http_fetch_json(url, headers={"User-Agent": "Mozilla/5.0"})


def fetch_3m_return(
    ticker: str,
    fetch_json: Callable[[str], dict] = _default_fetch_json,
) -> tuple[float, str] | None:
    """Return (trailing 3-month return in %, 'YYYY-MM' of latest bar) or None.

    Computed from monthly closes as close[-1] / close[-4] - 1.
    """
    url = f"{YAHOO_BASE}/{ticker}?range=1y&interval=1mo"
    payload = fetch_json(url)
    try:
        result = payload["chart"]["result"][0]
        timestamps = result["timestamp"]
        indicators = result["indicators"]
    except (TypeError, KeyError, IndexError):
        return None
    adjclose = indicators.get("adjclose", [{}])[0].get("adjclose")
    closes = adjclose if adjclose is not None else indicators["quote"][0]["close"]
    pairs = [(t, c) for t, c in zip(timestamps, closes) if c is not None]
    if len(pairs) < 4:
        return None
    asof = datetime.fromtimestamp(pairs[-1][0], tz=timezone.utc).strftime("%Y-%m")
    pct = (pairs[-1][1] / pairs[-4][1] - 1.0) * 100.0
    return round(pct, 4), asof


def load_market_returns(
    economies: tuple[str, ...],
    fetch_json: Callable[[str], dict] = _default_fetch_json,
) -> dict[str, dict[str, tuple[float, str]]]:
    """Return {economy: {column: (return_pct, asof)}} for live market columns.

    equity_3m_return for every economy with an ETF; fx_3m_return for every
    non-US economy with a pair, plus the US numeraire at 0.0 (dated to the US
    equity bar). Economies/columns with no data are omitted (caller falls back).
    """
    out: dict[str, dict[str, tuple[float, str]]] = {economy: {} for economy in economies}

    for economy in economies:
        ticker = EQUITY_TICKER_BY_ECONOMY.get(economy)
        if ticker is not None:
            result = fetch_3m_return(ticker, fetch_json=fetch_json)
            if result is not None:
                out[economy]["equity_3m_return"] = result

    for economy in economies:
        ticker = FX_TICKER_BY_ECONOMY.get(economy)
        if ticker is not None:
            result = fetch_3m_return(ticker, fetch_json=fetch_json)
            if result is not None:
                out[economy]["fx_3m_return"] = result

    # US is the FX numeraire: 0.0, dated to its own equity bar when available.
    if US_ECONOMY in economies and "equity_3m_return" in out.get(US_ECONOMY, {}):
        out[US_ECONOMY]["fx_3m_return"] = (0.0, out[US_ECONOMY]["equity_3m_return"][1])

    return out
