"""Baseline loading, PIT observation selection, and fallback overlay stages."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from uuid import uuid4

import pandas as pd

from data_sources import gdelt, imf_weo, market, world_bank
from data_sources.cache import TTLCache
from pipeline.contracts import Observation, PipelineRunContext, RunMode
from pipeline.fallback import FallbackAction, policy_for_source, resolve_with_policy
from pipeline.signal_definition import (
    DATA_DIR,
    INPUT_PROVENANCE_COLUMNS,
    ISO3_BY_ECONOMY,
    LIVE_HISTORY_YEARS,
    LIVE_MACRO_COLUMNS,
    MARKET_LIVE_COLUMNS,
    METHODOLOGY_VERSION,
    REQUIRED_CONSENSUS_COLUMNS,
    REQUIRED_MACRO_COLUMNS,
    REQUIRED_MARKET_COLUMNS,
    REQUIRED_NEWS_COLUMNS,
    SURPRISE_SPECS,
    UNIVERSE,
)


def validate_input_frame(
    df: pd.DataFrame,
    path: Path,
    required_columns: set[str],
) -> None:
    missing_columns = sorted(required_columns - set(df.columns))
    if missing_columns:
        raise ValueError(f"{path} is missing required columns: {missing_columns}")
    duplicate_economies = df["economy"][df["economy"].duplicated()].tolist()
    if duplicate_economies:
        raise ValueError(f"{path} has duplicate economies: {duplicate_economies}")
    missing_economies = sorted(set(UNIVERSE) - set(df["economy"]))
    if missing_economies:
        raise ValueError(f"{path} is missing required economies: {missing_economies}")
    if df[list(required_columns)].isna().any().any():
        raise ValueError(f"{path} contains missing values in required columns")


def load_mock_data(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    """Load and validate the complete fallback input frame."""
    macro_path = data_dir / "mock_macro.csv"
    consensus_path = data_dir / "mock_consensus.csv"
    market_path = data_dir / "mock_market.csv"
    news_path = data_dir / "mock_news.csv"
    macro = pd.read_csv(macro_path)
    consensus = pd.read_csv(consensus_path)
    market_frame = pd.read_csv(market_path)
    news = pd.read_csv(news_path)
    validate_input_frame(macro, macro_path, REQUIRED_MACRO_COLUMNS)
    validate_input_frame(consensus, consensus_path, REQUIRED_CONSENSUS_COLUMNS)
    validate_input_frame(market_frame, market_path, REQUIRED_MARKET_COLUMNS)
    validate_input_frame(news, news_path, REQUIRED_NEWS_COLUMNS)
    frame = (
        macro.set_index("economy")
        .join(consensus.set_index("economy"), how="inner")
        .join(market_frame.set_index("economy"), how="inner")
        .join(news.set_index("economy"), how="inner")
        .loc[list(UNIVERSE)]
    )
    if frame.isna().any().any():
        raise ValueError("Joined mock data contains missing values")
    frame["iso3"] = [ISO3_BY_ECONOMY[economy] for economy in frame.index]
    return frame


def initialize_provenance(economies: Iterable[str]) -> dict[str, dict[str, str]]:
    return {
        economy: {column: "mock" for column in INPUT_PROVENANCE_COLUMNS}
        for economy in economies
    }


def _latest_by_series(
    observations: Iterable[Observation],
    source: str,
) -> dict[tuple[str, str], Observation]:
    latest: dict[tuple[str, str], Observation] = {}
    for observation in observations:
        if observation.source != source:
            continue
        key = (observation.country, observation.metric)
        current = latest.get(key)
        order = (
            observation.period_end,
            observation.observed_at,
            observation.vintage,
            observation.revision,
        )
        if current is None or order > (
            current.period_end,
            current.observed_at,
            current.vintage,
            current.revision,
        ):
            latest[key] = observation
    return latest


def _latest_by_year(
    observations: Iterable[Observation],
    source: str,
) -> dict[tuple[str, str, int], Observation]:
    latest: dict[tuple[str, str, int], Observation] = {}
    for observation in observations:
        if observation.source != source:
            continue
        key = (observation.country, observation.metric, observation.period_start.year)
        current = latest.get(key)
        if current is None or (
            observation.observed_at,
            observation.vintage,
            observation.revision,
        ) > (current.observed_at, current.vintage, current.revision):
            latest[key] = observation
    return latest


def overlay_world_bank_observations(
    df: pd.DataFrame,
    provenance: dict[str, dict[str, str]],
    observations: Iterable[Observation],
) -> pd.DataFrame:
    latest = _latest_by_series(observations, world_bank.SOURCE)
    policy = policy_for_source(world_bank.SOURCE)
    for metric in LIVE_MACRO_COLUMNS:
        resolution = resolve_with_policy(
            policy,
            metric,
            df.index,
            lambda economy, selected_metric=metric: latest.get((economy, selected_metric)),
        )
        for economy, observation in resolution.selected_values:
            df.loc[economy, metric] = observation.value
            provenance[economy][metric] = f"world_bank:{observation.period_start.year}"
    return df


def overlay_imf_observations(
    df: pd.DataFrame,
    provenance: dict[str, dict[str, str]],
    observations: Iterable[Observation],
) -> set[str]:
    by_year = _latest_by_year(observations, imf_weo.SOURCE)
    consensus_column = {a: c for a, c, _ in SURPRISE_SPECS if a in LIVE_MACRO_COLUMNS}
    surprise_column = {a: s for a, _, s in SURPRISE_SPECS if a in LIVE_MACRO_COLUMNS}

    def forecast(economy: str, metric: str) -> Observation | None:
        actual_source = provenance[economy][metric]
        if not actual_source.startswith("world_bank:"):
            return None
        target_year = int(actual_source.rsplit(":", 1)[-1]) + 1
        return by_year.get((economy, metric, target_year))

    expected_change: set[str] = set()
    policy = policy_for_source(imf_weo.SOURCE)
    for metric, consensus_metric in consensus_column.items():
        resolution = resolve_with_policy(
            policy,
            metric,
            df.index,
            lambda economy, selected_metric=metric: forecast(economy, selected_metric),
        )
        if resolution.decision.action is not FallbackAction.LIVE:
            continue
        for economy, observation in resolution.selected_values:
            df.loc[economy, consensus_metric] = observation.value
            provenance[economy][consensus_metric] = (
                f"imf_weo:{observation.period_start.year}"
            )
        expected_change.add(surprise_column[metric])
    return expected_change


def overlay_market_observations(
    df: pd.DataFrame,
    provenance: dict[str, dict[str, str]],
    observations: Iterable[Observation],
) -> pd.DataFrame:
    latest = _latest_by_series(observations, market.SOURCE)
    policy = policy_for_source(market.SOURCE)
    for metric in MARKET_LIVE_COLUMNS:
        resolution = resolve_with_policy(
            policy,
            metric,
            df.index,
            lambda economy, selected_metric=metric: latest.get((economy, selected_metric)),
        )
        if resolution.decision.action is not FallbackAction.LIVE:
            continue
        for economy, observation in resolution.selected_values:
            df.loc[economy, metric] = observation.value
            provenance[economy][metric] = f"yahoo:{observation.period_end:%Y-%m}"
    return df


def overlay_gdelt_observations(
    df: pd.DataFrame,
    provenance: dict[str, dict[str, str]],
    observations: Iterable[Observation],
) -> pd.DataFrame:
    latest = _latest_by_series(observations, gdelt.SOURCE)
    resolution = resolve_with_policy(
        policy_for_source(gdelt.SOURCE),
        "news_pressure",
        df.index,
        lambda economy: latest.get((economy, "news_pressure")),
    )
    if resolution.decision.action is not FallbackAction.LIVE:
        return df
    for economy, observation in resolution.selected_values:
        df.loc[economy, "news_pressure"] = observation.value
        provenance[economy]["news_pressure"] = f"gdelt:{observation.period_end:%Y-%m-%d}"
    return df


def overlay_fx_carry(
    df: pd.DataFrame,
    provenance: dict[str, dict[str, str]],
    source: str = "mock",
) -> pd.DataFrame:
    if source != "live":
        return df
    us_rate = float(df.loc[UNIVERSE[0], "policy_rate"])
    for economy in df.index:
        df.loc[economy, "fx_carry"] = float(df.loc[economy, "policy_rate"]) - us_rate
        provenance[economy]["fx_carry"] = "derived:policy_rate_diff"
    return df


def apply_live_observations(
    df: pd.DataFrame,
    provenance: dict[str, dict[str, str]],
    observations_by_source: dict[str, Iterable[Observation]],
) -> frozenset[str]:
    """Apply PIT-selected observations in dependency order."""
    overlay_world_bank_observations(
        df,
        provenance,
        observations_by_source.get(world_bank.SOURCE, ()),
    )
    expected_change = overlay_imf_observations(
        df,
        provenance,
        observations_by_source.get(imf_weo.SOURCE, ()),
    )
    overlay_market_observations(
        df,
        provenance,
        observations_by_source.get(market.SOURCE, ()),
    )
    overlay_gdelt_observations(
        df,
        provenance,
        observations_by_source.get(gdelt.SOURCE, ()),
    )
    overlay_fx_carry(df, provenance, source="live")
    if df.isna().any().any():
        raise ValueError("Inputs contain missing values after live overlay")
    return frozenset(expected_change)


def _compat_context() -> PipelineRunContext:
    now = datetime.now(timezone.utc)
    return PipelineRunContext(
        run_id=f"compat-{uuid4().hex}",
        as_of=now,
        started_at=now,
        mode=RunMode.LIVE,
        methodology_version=METHODOLOGY_VERSION,
        config_hash="compat",
    )


def overlay_world_bank_actuals(
    df: pd.DataFrame,
    provenance: dict[str, dict[str, str]],
    fetch_json=None,
) -> pd.DataFrame:
    context = _compat_context()
    kwargs = {} if fetch_json is None else {"fetch_json": fetch_json}
    batch = world_bank.load_observations(
        context,
        tuple(df.index),
        start_year=context.as_of.year - LIVE_HISTORY_YEARS,
        end_year=context.as_of.year,
        **kwargs,
    )
    return overlay_world_bank_observations(df, provenance, batch.observations)


def overlay_imf_expected_change(
    df: pd.DataFrame,
    provenance: dict[str, dict[str, str]],
    fetch_json=None,
) -> set[str]:
    context = _compat_context()
    kwargs = {} if fetch_json is None else {"fetch_json": fetch_json}
    batch = imf_weo.load_observations(context, tuple(df.index), **kwargs)
    return overlay_imf_observations(df, provenance, batch.observations)


def load_macro_inputs(
    source: str = "mock",
    data_dir: Path = DATA_DIR,
    fetch_json=None,
    imf_fetch_json=None,
) -> tuple[pd.DataFrame, dict[str, dict[str, str]], frozenset[str]]:
    df = load_mock_data(data_dir)
    provenance = initialize_provenance(df.index)
    if source == "mock":
        return df, provenance, frozenset()
    if source != "live":
        raise ValueError(f"Unknown data source: {source!r} (expected 'mock' or 'live')")
    overlay_world_bank_actuals(df, provenance, fetch_json=fetch_json)
    expected_change = overlay_imf_expected_change(
        df,
        provenance,
        fetch_json=imf_fetch_json,
    )
    if df.isna().any().any():
        raise ValueError("Macro inputs contain missing values after live overlay")
    return df, provenance, frozenset(expected_change)


def overlay_market_inputs(
    df: pd.DataFrame,
    provenance: dict[str, dict[str, str]],
    source: str = "mock",
    fetch_json=None,
) -> pd.DataFrame:
    if source != "live":
        return df
    context = _compat_context()
    kwargs = {} if fetch_json is None else {"fetch_json": fetch_json}
    batch = market.load_observations(context, tuple(df.index), **kwargs)
    return overlay_market_observations(df, provenance, batch.observations)


def overlay_news_pressure(
    df: pd.DataFrame,
    provenance: dict[str, dict[str, str]],
    source: str = "mock",
    fetch_json=None,
    cache: TTLCache | None = None,
) -> pd.DataFrame:
    if source != "live":
        return df
    context = _compat_context()
    kwargs = {}
    if fetch_json is not None:
        kwargs["fetch_json"] = fetch_json
    if cache is not None:
        kwargs["cache"] = cache
    batch = gdelt.load_observations(context, tuple(df.index), **kwargs)
    return overlay_gdelt_observations(df, provenance, batch.observations)
