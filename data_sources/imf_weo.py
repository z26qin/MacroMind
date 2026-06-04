"""IMF World Economic Outlook forecasts (free, no API key).

Fetches forward macro forecasts for the signal universe from the public IMF
DataMapper API and exposes them per economy/column as {year: value}. Used to
build a real "consensus" baseline: in live mode the macro 'surprise' becomes
the forecast-implied expected change, forecast(T+1) - actual(T).

All HTTP access goes through an injectable ``fetch_json`` callable so callers
(and tests) can run without network.

NOTE: the IMF WAF returns 403 for a 'Mozilla' User-Agent, so the default
fetch deliberately sends no custom User-Agent (httpx's own UA works).
"""
from __future__ import annotations

from typing import Callable

import httpx

IMF_BASE = "https://www.imf.org/external/datamapper/api/v1"

# Engine economy name -> IMF DataMapper code. Euro Area is "EURO" (Euro area).
# "EU" (European Union) and "EUQ" are intentionally NOT used.
IMF_CODE_BY_ECONOMY = {
    "United States of America": "USA",
    "Canada": "CAN",
    "China": "CHN",
    "Japan": "JPN",
    "Brazil": "BRA",
    "Euro Area": "EURO",
}

# Engine macro column -> IMF WEO indicator code (forecast columns only).
IMF_INDICATOR_BY_COLUMN = {
    "inflation_yoy": "PCPIPCH",   # Inflation, average consumer prices (% change)
    "gdp_growth": "NGDP_RPCH",    # Real GDP growth (% change)
    "unemployment": "LUR",        # Unemployment rate (% of total labor force)
}

FORECAST_COLUMNS = tuple(IMF_INDICATOR_BY_COLUMN)


def _default_fetch_json(url: str) -> dict:
    # No custom User-Agent: the IMF WAF rejects 'Mozilla' UAs with HTTP 403.
    response = httpx.get(url, timeout=20.0)
    response.raise_for_status()
    return response.json()


def fetch_indicator(
    column: str,
    fetch_json: Callable[[str], dict] = _default_fetch_json,
) -> dict[str, dict[int, float]]:
    """Return {economy: {year: value}} for the six mapped economies.

    The DataMapper returns every economy under values[indicator]; we keep only
    the engine universe. Economies/years with no value are simply absent.
    """
    indicator = IMF_INDICATOR_BY_COLUMN[column]
    url = f"{IMF_BASE}/{indicator}"
    payload = fetch_json(url)
    try:
        values = payload["values"][indicator]
    except (TypeError, KeyError) as exc:
        raise ValueError(
            f"Unexpected IMF response for {indicator}: {payload!r:.200}"
        ) from exc

    out: dict[str, dict[int, float]] = {}
    for economy, code in IMF_CODE_BY_ECONOMY.items():
        series = values.get(code)
        if not isinstance(series, dict):
            continue
        parsed = {
            int(year): float(value)
            for year, value in series.items()
            if value is not None
        }
        if parsed:
            out[economy] = parsed
    return out


def load_imf_forecasts(
    economies: tuple[str, ...],
    fetch_json: Callable[[str], dict] = _default_fetch_json,
) -> dict[str, dict[str, dict[int, float]]]:
    """Return {economy: {column: {year: value}}} for the forecast columns."""
    by_column = {
        column: fetch_indicator(column, fetch_json=fetch_json)
        for column in FORECAST_COLUMNS
    }
    out: dict[str, dict[str, dict[int, float]]] = {economy: {} for economy in economies}
    for column, series_by_economy in by_column.items():
        for economy in economies:
            if economy in series_by_economy:
                out[economy][column] = series_by_economy[economy]
    return out
