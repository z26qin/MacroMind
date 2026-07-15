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

SOURCE = "imf_weo"
UNIT_BY_COLUMN = {
    "inflation_yoy": "percent_yoy",
    "gdp_growth": "percent_yoy",
    "unemployment": "percent_labor_force",
}


def _default_fetch_json(url: str) -> dict:
    # No custom User-Agent: the IMF WAF rejects 'Mozilla' UAs with HTTP 403.
    return http_fetch_json(url, headers=None)


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


def load_observations(
    context: PipelineRunContext,
    economies: tuple[str, ...],
    *,
    fetch_json: Callable[[str], dict] = _default_fetch_json,
    clock: Clock = utc_now,
) -> SourceBatch:
    """Load IMF WEO annual forecasts into the canonical source contract.

    DataMapper exposes a varying set of years per indicator. Expected rows are
    therefore calculated from each indicator's returned year domain, while a
    completely failed indicator still expects one row per requested economy.
    """
    requested_economies = normalize_request(context, economies)
    requested_at = capture_utc(clock, "requested_at")
    records: list[tuple[str, str, int, float]] = []
    errors: list[SourceError] = []
    expected_count = 0

    for column in FORECAST_COLUMNS:
        try:
            series_by_economy = fetch_indicator(column, fetch_json=fetch_json)
        except Exception as exc:
            expected_count += len(requested_economies)
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

        years = {
            year
            for economy in requested_economies
            for year in series_by_economy.get(economy, {})
        }
        expected_count += len(requested_economies) * max(1, len(years))
        for economy in requested_economies:
            series = series_by_economy.get(economy, {})
            if not series:
                errors.append(
                    SourceError(
                        code="missing_series",
                        message="IMF DataMapper returned no forecast series",
                        country=economy,
                        metric=column,
                    )
                )
                continue
            records.extend(
                (economy, column, year, value)
                for year, value in sorted(series.items())
            )

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
        expected_observation_count=expected_count,
        requested_at=requested_at,
        completed_at=completed_at,
        observations=tuple(observations),
        errors=tuple(errors),
    )


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
