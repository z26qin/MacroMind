"""World Bank macro data source (free, no API key).

Fetches annual macro indicators for the signal universe from the public
World Bank API and shapes them to the engine's mock CSV schema, with
per-value provenance. All HTTP access goes through an injectable
``fetch_json`` callable so callers (and tests) can run without network.
"""
from __future__ import annotations

from typing import Callable

from data_sources.http import fetch_json as http_fetch_json
from data_sources.normalization import (
    Clock,
    annual_period,
    capture_utc,
    ingestion_vintage,
    normalize_request,
    utc_now,
)
from pipeline.contracts import (
    DataFrequency,
    Observation,
    PipelineRunContext,
    SourceBatch,
    SourceError,
)

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
DEFAULT_HISTORY_YEARS = 8

SOURCE = "world_bank"
UNIT_BY_COLUMN = {
    "inflation_yoy": "percent_yoy",
    "gdp_growth": "percent_yoy",
    "unemployment": "percent_labor_force",
}


def _default_fetch_json(url: str) -> list:
    return http_fetch_json(url)


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


def load_observations(
    context: PipelineRunContext,
    economies: tuple[str, ...],
    *,
    start_year: int | None = None,
    end_year: int | None = None,
    fetch_json: Callable[[str], list] = _default_fetch_json,
    clock: Clock = utc_now,
) -> SourceBatch:
    """Load World Bank history into the canonical live-adapter contract.

    The API does not expose a reliable release timestamp for each value, so
    ``event_time`` remains unknown. Acquisition time is retained as the PIT
    vintage instead of inventing a publication time.
    """
    requested_economies = normalize_request(context, economies)
    end_year = context.as_of.year if end_year is None else end_year
    start_year = end_year - DEFAULT_HISTORY_YEARS if start_year is None else start_year
    if end_year < start_year:
        raise ValueError("end_year cannot be earlier than start_year")

    requested_at = capture_utc(clock, "requested_at")
    records: list[tuple[str, str, int, float]] = []
    errors: list[SourceError] = []
    for column in LIVE_COLUMNS:
        try:
            series = fetch_indicator(column, start_year, end_year, fetch_json=fetch_json)
        except Exception as exc:
            errors.extend(
                SourceError(
                    code="fetch_failed",
                    message=f"{type(exc).__name__}: {exc}",
                    retryable=not isinstance(exc, ValueError),
                    country=economy,
                    metric=column,
                )
                for economy in requested_economies
            )
            continue

        for economy in requested_economies:
            history = [
                (year, value)
                for year, value in series.get(economy, [])
                if start_year <= year <= end_year
            ]
            if not history:
                errors.append(
                    SourceError(
                        code="missing_series",
                        message="World Bank returned no non-null observations in the requested window",
                        country=economy,
                        metric=column,
                    )
                )
                continue
            records.extend((economy, column, year, value) for year, value in history)

    completed_at = capture_utc(clock, "completed_at")
    vintage = ingestion_vintage(completed_at)
    observations = []
    for economy, column, year, value in records:
        try:
            period_start, period_end = annual_period(year)
            observations.append(
                Observation(
                    metric=column,
                    value=value,
                    unit=UNIT_BY_COLUMN[column],
                    country=economy,
                    frequency=DataFrequency.ANNUAL,
                    period_start=period_start,
                    period_end=period_end,
                    event_time=None,
                    observed_at=completed_at,
                    source=SOURCE,
                    revision="unreported",
                    vintage=vintage,
                )
            )
        except (TypeError, ValueError) as exc:
            errors.append(
                SourceError(
                    code="invalid_observation",
                    message=f"{type(exc).__name__}: {exc}",
                    country=economy,
                    metric=column,
                )
            )

    return SourceBatch(
        run_id=context.run_id,
        source=SOURCE,
        expected_observation_count=(
            len(requested_economies) * len(LIVE_COLUMNS) * (end_year - start_year + 1)
        ),
        requested_at=requested_at,
        completed_at=completed_at,
        observations=tuple(observations),
        errors=tuple(errors),
    )


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
