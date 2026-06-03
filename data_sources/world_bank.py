"""World Bank macro data source (free, no API key).

Fetches annual macro indicators for the signal universe from the public
World Bank API and shapes them to the engine's mock CSV schema, with
per-value provenance. All HTTP access goes through an injectable
``fetch_json`` callable so callers (and tests) can run without network.
"""
from __future__ import annotations

from typing import Callable

import httpx

WB_BASE = "https://api.worldbank.org/v2"

# Engine economy name -> World Bank country code. Euro Area is "EMU".
WB_CODE_BY_ECONOMY = {
    "United States of America": "USA",
    "Canada": "CAN",
    "China": "CHN",
    "Japan": "JPN",
    "Brazil": "BRA",
    "Euro Area": "EMU",
}

# Engine macro column -> World Bank indicator code (live columns only).
WB_INDICATOR_BY_COLUMN = {
    "inflation_yoy": "FP.CPI.TOTL.ZG",
    "gdp_growth": "NY.GDP.MKTP.KD.ZG",
    "unemployment": "SL.UEM.TOTL.ZS",
}

LIVE_COLUMNS = tuple(WB_INDICATOR_BY_COLUMN)


def _default_fetch_json(url: str) -> list:
    response = httpx.get(url, timeout=20.0)
    response.raise_for_status()
    return response.json()


def fetch_indicator(
    column: str,
    start_year: int,
    end_year: int,
    fetch_json: Callable[[str], list] = _default_fetch_json,
) -> dict[str, list[tuple[int, float]]]:
    """Return {economy: [(year, value), ...]} sorted newest-first, nulls dropped."""
    indicator = WB_INDICATOR_BY_COLUMN[column]
    codes = ";".join(WB_CODE_BY_ECONOMY.values())
    url = (
        f"{WB_BASE}/country/{codes}/indicator/{indicator}"
        f"?format=json&per_page=20000&date={start_year}:{end_year}"
    )
    payload = fetch_json(url)
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
        raise ValueError(f"Unexpected World Bank response for {indicator}: {payload!r:.200}")

    economy_by_code = {code: economy for economy, code in WB_CODE_BY_ECONOMY.items()}
    series: dict[str, list[tuple[int, float]]] = {economy: [] for economy in WB_CODE_BY_ECONOMY}
    for obs in payload[1]:
        economy = economy_by_code.get(obs.get("countryiso3code"))
        value = obs.get("value")
        if economy is None or value is None:
            continue
        series[economy].append((int(obs["date"]), float(value)))

    for economy in series:
        series[economy].sort(key=lambda pair: pair[0], reverse=True)
    return series


def latest_and_baseline(
    history: list[tuple[int, float]],
    baseline_window: int = 3,
) -> tuple[float, float, int] | None:
    """From newest-first (year, value) pairs return (actual, consensus, year).

    actual   = most recent observation
    consensus = mean of up to ``baseline_window`` prior observations (naive forecast);
                falls back to the actual value when no prior observations exist.
    Returns None when there is no data.
    """
    if not history:
        return None
    actual_year, actual = history[0]
    prior = [value for _, value in history[1 : 1 + baseline_window]]
    consensus = sum(prior) / len(prior) if prior else actual
    return actual, consensus, actual_year


def load_world_bank_macro(
    economies: tuple[str, ...],
    start_year: int,
    end_year: int,
    baseline_window: int = 3,
    fetch_json: Callable[[str], list] = _default_fetch_json,
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]], dict[str, dict[str, str]]]:
    """Return (macro, consensus, provenance) for the LIVE columns only.

    Each dict is keyed by economy then column. Economies/columns with no
    available observation are simply absent (the caller falls back to mock).
    """
    macro: dict[str, dict[str, float]] = {economy: {} for economy in economies}
    consensus: dict[str, dict[str, float]] = {economy: {} for economy in economies}
    provenance: dict[str, dict[str, str]] = {economy: {} for economy in economies}

    for column in LIVE_COLUMNS:
        series = fetch_indicator(column, start_year, end_year, fetch_json=fetch_json)
        for economy in economies:
            result = latest_and_baseline(series.get(economy, []), baseline_window)
            if result is None:
                continue
            actual, baseline, year = result
            macro[economy][column] = actual
            consensus[economy][column] = baseline
            provenance[economy][column] = f"world_bank:{year}"

    return macro, consensus, provenance
