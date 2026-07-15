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

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from data_sources.http import fetch_json as http_fetch_json
from data_sources.normalization import (
    Clock,
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
SOURCE = "yahoo"
LIVE_COLUMNS = ("equity_3m_return", "fx_3m_return")


@dataclass(frozen=True)
class _ReturnWindow:
    value: float
    asof: str
    period_start: datetime
    period_end: datetime
    price_basis: str


def _default_fetch_json(url: str) -> dict:
    # Yahoo needs a browser UA (no-UA -> 429) and rate-limits bursts.
    return http_fetch_json(url, headers={"User-Agent": "Mozilla/5.0"})


def _fetch_3m_window(
    ticker: str,
    fetch_json: Callable[[str], dict] = _default_fetch_json,
) -> _ReturnWindow | None:
    url = f"{YAHOO_BASE}/{ticker}?range=1y&interval=1mo"
    payload = fetch_json(url)
    try:
        result = payload["chart"]["result"][0]
        timestamps = result["timestamp"]
        indicators = result["indicators"]
        adjclose = indicators.get("adjclose", [{}])[0].get("adjclose")
        closes = adjclose if adjclose is not None else indicators["quote"][0]["close"]
    except (TypeError, KeyError, IndexError):
        return None
    pairs = [(t, c) for t, c in zip(timestamps, closes) if c is not None]
    if len(pairs) < 4:
        return None
    period_start = datetime.fromtimestamp(pairs[-4][0], tz=timezone.utc)
    period_end = datetime.fromtimestamp(pairs[-1][0], tz=timezone.utc)
    pct = (pairs[-1][1] / pairs[-4][1] - 1.0) * 100.0
    return _ReturnWindow(
        value=round(pct, 4),
        asof=period_end.strftime("%Y-%m"),
        period_start=period_start,
        period_end=period_end,
        price_basis="adjusted_close" if adjclose is not None else "close",
    )


def fetch_3m_return(
    ticker: str,
    fetch_json: Callable[[str], dict] = _default_fetch_json,
) -> tuple[float, str] | None:
    """Return (trailing 3-month return in %, 'YYYY-MM' of latest bar) or None.

    Computed from monthly closes as close[-1] / close[-4] - 1. This compatibility
    shape is retained while the signal engine migrates to ``SourceBatch``.
    """
    window = _fetch_3m_window(ticker, fetch_json=fetch_json)
    return None if window is None else (window.value, window.asof)


def load_observations(
    context: PipelineRunContext,
    economies: tuple[str, ...],
    *,
    fetch_json: Callable[[str], dict] = _default_fetch_json,
    clock: Clock = utc_now,
) -> SourceBatch:
    """Load Yahoo equity and FX windows into the canonical source contract."""
    requested_economies = normalize_request(context, economies)
    requested_at = capture_utc(clock, "requested_at")
    windows: dict[tuple[str, str], _ReturnWindow] = {}
    errors: list[SourceError] = []

    def fetch_window(economy: str, metric: str, ticker: str | None) -> None:
        if ticker is None:
            errors.append(
                SourceError(
                    code="unsupported_economy",
                    message=f"No Yahoo ticker mapping for {metric}",
                    country=economy,
                    metric=metric,
                )
            )
            return
        try:
            window = _fetch_3m_window(ticker, fetch_json=fetch_json)
        except Exception as exc:
            errors.append(
                SourceError(
                    code="fetch_failed",
                    message=f"{type(exc).__name__}: {exc}",
                    retryable=True,
                    country=economy,
                    metric=metric,
                )
            )
            return
        if window is None:
            errors.append(
                SourceError(
                    code="insufficient_history",
                    message="Yahoo returned fewer than four usable monthly closes",
                    country=economy,
                    metric=metric,
                )
            )
            return
        windows[(economy, metric)] = window

    for economy in requested_economies:
        fetch_window(economy, "equity_3m_return", EQUITY_TICKER_BY_ECONOMY.get(economy))

    for economy in requested_economies:
        if economy == US_ECONOMY:
            equity_window = windows.get((economy, "equity_3m_return"))
            if equity_window is None:
                errors.append(
                    SourceError(
                        code="dependency_missing",
                        message="US FX numeraire requires the US equity bar date",
                        country=economy,
                        metric="fx_3m_return",
                    )
                )
            else:
                windows[(economy, "fx_3m_return")] = _ReturnWindow(
                    value=0.0,
                    asof=equity_window.asof,
                    period_start=equity_window.period_start,
                    period_end=equity_window.period_end,
                    price_basis="usd_numeraire",
                )
            continue
        fetch_window(economy, "fx_3m_return", FX_TICKER_BY_ECONOMY.get(economy))

    completed_at = capture_utc(clock, "completed_at")
    vintage = ingestion_vintage(completed_at)
    observations: list[Observation] = []
    for (economy, metric), window in windows.items():
        try:
            observations.append(
                Observation(
                    metric=metric,
                    value=window.value,
                    unit="percent_return",
                    country=economy,
                    frequency=DataFrequency.WINDOW,
                    period_start=window.period_start,
                    period_end=window.period_end,
                    event_time=window.period_end,
                    observed_at=completed_at,
                    source=SOURCE,
                    revision=window.price_basis,
                    vintage=vintage,
                )
            )
        except (TypeError, ValueError) as exc:
            errors.append(
                SourceError(
                    code="invalid_observation",
                    message=f"{type(exc).__name__}: {exc}",
                    country=economy,
                    metric=metric,
                )
            )

    return SourceBatch(
        run_id=context.run_id,
        source=SOURCE,
        expected_observation_count=len(requested_economies) * len(LIVE_COLUMNS),
        requested_at=requested_at,
        completed_at=completed_at,
        observations=tuple(observations),
        errors=tuple(errors),
    )


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
