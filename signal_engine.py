"""Generate a mock cross-asset macro signal snapshot.

The engine is deliberately small and inspectable:
1. read mock macro, market, and consensus inputs from CSV files
2. read deterministic signal weights from YAML config
3. validate the six-economy universe and required columns
4. compute surprises, cross-sectional ranks, raw scores, and final signals
5. write a versioned snapshot consumed by the FastAPI app and D3 frontend
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, TypeVar

import numpy as np
import pandas as pd
import yaml

from rag_signal import compute_rag_signal
from data_sources import world_bank as wb
from data_sources import imf_weo
from data_sources import market


SNAPSHOT_PATH = Path("snapshot.json")
CONFIG_PATH = Path("signal_config.yaml")
DATA_DIR = Path("data")
METHODOLOGY_VERSION = "v0.1"
LIVE_HISTORY_YEARS = 8

ASSET_CLASSES = ("fx", "rates", "equity", "real_estate")
UNIVERSE = (
    "United States of America",
    "Canada",
    "China",
    "Japan",
    "Brazil",
    "Euro Area",
)
ISO3_BY_ECONOMY = {
    "United States of America": "USA",
    "Canada": "CAN",
    "China": "CHN",
    "Japan": "JPN",
    "Brazil": "BRA",
    "Euro Area": "EUR",
}

REQUIRED_MACRO_COLUMNS = {
    "economy",
    "inflation_yoy",
    "gdp_growth",
    "unemployment",
    "policy_rate",
    "pmi",
}
REQUIRED_CONSENSUS_COLUMNS = {
    "economy",
    "inflation_consensus",
    "gdp_consensus",
    "unemployment_consensus",
    "policy_rate_consensus",
    "pmi_consensus",
}
REQUIRED_MARKET_COLUMNS = {
    "economy",
    "fx_3m_return",
    "fx_carry",
    "rate_3m_change",
    "curve_slope_2s10s",
    "equity_3m_return",
    "equity_forward_pe",
    "reit_3m_return",
    "house_price_yoy",
}

# Single source of truth for the (actual, consensus, surprise) column triples.
# add_surprises consumes all of these; the live overlay derives its consensus
# and surprise column maps from the LIVE_MACRO_COLUMNS subset below.
SURPRISE_SPECS = (
    ("inflation_yoy", "inflation_consensus", "inflation_surprise"),
    ("gdp_growth", "gdp_consensus", "growth_surprise"),
    ("unemployment", "unemployment_consensus", "unemployment_surprise"),
    ("policy_rate", "policy_rate_consensus", "policy_surprise"),
    ("pmi", "pmi_consensus", "pmi_surprise"),
)

# The macro columns that have a live World Bank actual and an IMF WEO forecast,
# i.e. the ones eligible for the expected-change consensus overlay.
LIVE_MACRO_COLUMNS = ("inflation_yoy", "gdp_growth", "unemployment")


def clip_signal(value: float) -> float:
    return float(np.clip(value, -1.0, 1.0))


def blend_signal(
    deterministic: float,
    rag_signal: float,
    rag_confidence: float,
    rag_weight: float,
) -> float:
    """Confidence-weighted convex blend of deterministic and RAG signals.

    effective_rag = rag_weight * rag_confidence
    final         = (1 - effective_rag) * deterministic + effective_rag * rag

    At confidence 1 this reduces to deterministic_weight*det + rag_weight*rag
    (since deterministic_weight == 1 - rag_weight); at confidence 0 the RAG view
    is ignored and the deterministic signal passes through unchanged.
    """
    effective_rag = rag_weight * rag_confidence
    return clip_signal((1.0 - effective_rag) * deterministic + effective_rag * rag_signal)


def percentile_to_signal(series: pd.Series) -> pd.Series:
    """Map cross-sectional ranks to [-1, +1], including full endpoints."""
    if len(series) == 1:
        return pd.Series(0.0, index=series.index)

    pct = (series.rank(method="average") - 1.0) / (len(series) - 1.0)
    return (2.0 * pct - 1.0).clip(-1.0, 1.0)


_T = TypeVar("_T")


def resolve_all_or_none(
    economies: Iterable[str],
    resolve: Callable[[str], _T | None],
) -> dict[str, _T] | None:
    """Resolve a value for every economy, or return None if any is missing.

    Encodes the live-overlay rule that a column goes live only when every
    economy has data: one ``None`` short-circuits and falls the whole column
    back to mock.
    """
    resolved: dict[str, _T] = {}
    for economy in economies:
        value = resolve(economy)
        if value is None:
            return None
        resolved[economy] = value
    return resolved


def load_signal_config(path: Path = CONFIG_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Missing signal config: {path}")

    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Malformed signal config {path}: {exc}") from exc

    if not isinstance(config, dict):
        raise ValueError(f"Malformed signal config {path}: expected a YAML object")

    weights = config.get("weights")
    blend = config.get("signal_blend")
    if not isinstance(weights, dict):
        raise ValueError(f"Malformed signal config {path}: missing weights")
    if not isinstance(blend, dict):
        raise ValueError(f"Malformed signal config {path}: missing signal_blend")

    for asset_class in ASSET_CLASSES:
        asset_weights = weights.get(asset_class)
        if not isinstance(asset_weights, dict) or not asset_weights:
            raise ValueError(f"Malformed signal config {path}: missing weights for {asset_class}")
        for feature, value in asset_weights.items():
            if not feature.endswith("_rank"):
                raise ValueError(f"Malformed signal config {path}: {feature} must be a ranked feature")
            if not isinstance(value, (int, float)):
                raise ValueError(f"Malformed signal config {path}: weight {asset_class}.{feature} must be numeric")

    for key in ("deterministic_weight", "rag_weight"):
        if not isinstance(blend.get(key), (int, float)):
            raise ValueError(f"Malformed signal config {path}: signal_blend.{key} must be numeric")
    if abs(float(blend["deterministic_weight"]) + float(blend["rag_weight"]) - 1.0) > 1e-9:
        raise ValueError(
            f"Malformed signal config {path}: signal_blend deterministic_weight + "
            f"rag_weight must sum to 1.0"
        )

    return config


def validate_input_frame(df: pd.DataFrame, path: Path, required_columns: set[str]) -> None:
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
    """Load and validate mock inputs from CSV files."""
    macro_path = data_dir / "mock_macro.csv"
    consensus_path = data_dir / "mock_consensus.csv"
    market_path = data_dir / "mock_market.csv"

    macro = pd.read_csv(macro_path)
    consensus = pd.read_csv(consensus_path)
    market = pd.read_csv(market_path)

    validate_input_frame(macro, macro_path, REQUIRED_MACRO_COLUMNS)
    validate_input_frame(consensus, consensus_path, REQUIRED_CONSENSUS_COLUMNS)
    validate_input_frame(market, market_path, REQUIRED_MARKET_COLUMNS)

    df = (
        macro.set_index("economy")
        .join(consensus.set_index("economy"), how="inner")
        .join(market.set_index("economy"), how="inner")
        .loc[list(UNIVERSE)]
    )
    if df.isna().any().any():
        raise ValueError("Joined mock data contains missing values")

    df["iso3"] = [ISO3_BY_ECONOMY[economy] for economy in df.index]
    return df


def load_macro_inputs(
    source: str = "mock",
    data_dir: Path = DATA_DIR,
    fetch_json=None,
    imf_fetch_json=None,
) -> tuple[pd.DataFrame, dict[str, dict[str, str]], frozenset[str]]:
    """Return (joined input frame, provenance, expected_change_columns).

    source="mock": the existing mock CSVs, every value tagged "mock"; the
    expected-change set is empty (surprises are classic beat/miss).
    source="live": World Bank realized values overlay the mock actuals, and the
    IMF WEO next-year forecast overlays the consensus for the three live macro
    columns where every economy has data. Those columns are returned in
    expected_change_columns so the surprise becomes forecast(T+1) - actual(T).
    """
    df = load_mock_data(data_dir)

    tracked_columns = sorted(REQUIRED_MACRO_COLUMNS - {"economy"})
    provenance = {
        economy: {column: "mock" for column in tracked_columns}
        for economy in df.index
    }

    if source == "mock":
        return df, provenance, frozenset()
    if source != "live":
        raise ValueError(f"Unknown data source: {source!r} (expected 'mock' or 'live')")

    end_year = date.today().year
    start_year = end_year - LIVE_HISTORY_YEARS
    wb_kwargs = {} if fetch_json is None else {"fetch_json": fetch_json}
    macro, _wb_consensus, live_provenance = wb.load_world_bank_macro(
        tuple(df.index), start_year, end_year, **wb_kwargs
    )

    # Overlay World Bank realized actuals (consensus is replaced by IMF below).
    for economy in df.index:
        for column, value in macro[economy].items():
            df.loc[economy, column] = value
            provenance[economy][column] = live_provenance[economy][column]

    imf_kwargs = {} if imf_fetch_json is None else {"fetch_json": imf_fetch_json}
    forecasts = imf_weo.load_imf_forecasts(tuple(df.index), **imf_kwargs)
    expected_change = _apply_imf_expected_change(df, provenance, forecasts)

    if df.isna().any().any():
        raise ValueError("Macro inputs contain missing values after live overlay")
    return df, provenance, frozenset(expected_change)


def _apply_imf_expected_change(
    df: pd.DataFrame,
    provenance: dict[str, dict[str, str]],
    forecasts: dict[str, dict[str, dict[int, float]]],
) -> set[str]:
    """Overlay IMF next-year forecasts as the consensus, all-or-nothing per column.

    A live macro column switches to expected-change mode only when every economy
    has a World Bank actual (year T) and an IMF forecast for T+1; then its
    consensus cell is replaced with that forecast and its surprise column is
    added to the returned set. Mutates df and provenance.
    """
    consensus_column = {a: c for a, c, _ in SURPRISE_SPECS if a in LIVE_MACRO_COLUMNS}
    surprise_column = {a: s for a, _, s in SURPRISE_SPECS if a in LIVE_MACRO_COLUMNS}

    def imf_forecast(economy: str, macro_col: str) -> tuple[float, int] | None:
        prov = provenance[economy][macro_col]
        if not prov.startswith("world_bank:"):
            return None
        forecast_year = int(prov.split(":")[1]) + 1
        forecast = forecasts.get(economy, {}).get(macro_col, {}).get(forecast_year)
        return None if forecast is None else (forecast, forecast_year)

    expected_change: set[str] = set()
    for macro_col, cons_col in consensus_column.items():
        resolved = resolve_all_or_none(
            df.index, lambda economy, col=macro_col: imf_forecast(economy, col)
        )
        if resolved is None:
            continue  # not fully IMF-backed -> keep mock consensus for this column
        for economy, (forecast, forecast_year) in resolved.items():
            df.loc[economy, cons_col] = forecast
            provenance[economy][cons_col] = f"imf_weo:{forecast_year}"
        expected_change.add(surprise_column[macro_col])
    return expected_change


MARKET_LIVE_COLUMNS = ("fx_3m_return", "equity_3m_return")


def overlay_market_inputs(
    df: pd.DataFrame,
    provenance: dict[str, dict[str, str]],
    source: str = "mock",
    fetch_json=None,
) -> pd.DataFrame:
    """Overlay live Yahoo 3-month FX and equity returns onto the frame.

    Mock mode is a no-op (the bundled CSV values stand). In live mode each
    market column is overlaid all-or-nothing: only when every economy resolves
    a value does the column go live (else it keeps its mock value). Provenance
    for overlaid cells is recorded as ``yahoo:<YYYY-MM>``. Mutates df/provenance
    and returns df.
    """
    if source != "live":
        return df

    kwargs = {} if fetch_json is None else {"fetch_json": fetch_json}
    returns = market.load_market_returns(tuple(df.index), **kwargs)

    for column in MARKET_LIVE_COLUMNS:
        resolved = resolve_all_or_none(
            df.index, lambda economy, col=column: returns.get(economy, {}).get(col)
        )
        if resolved is None:
            continue  # not fully live -> keep mock for this column
        for economy, (return_pct, asof) in resolved.items():
            df.loc[economy, column] = return_pct
            provenance[economy][column] = f"yahoo:{asof}"
    return df


def overlay_fx_carry(
    df: pd.DataFrame,
    provenance: dict[str, dict[str, str]],
    source: str = "mock",
) -> pd.DataFrame:
    """In live mode, derive fx_carry as the policy-rate differential vs the US.

    Carry = local short rate - USD short rate (positive = the currency
    out-yields USD; the US is the numeraire at 0.0). Mock mode is a no-op so
    the bundled snapshot is unchanged. Provenance is ``derived:policy_rate_diff``.
    This is *live-ready*: once policy_rate has a live source the carry becomes
    genuinely live with no further change.
    """
    if source != "live":
        return df
    us_rate = float(df.loc[UNIVERSE[0], "policy_rate"])  # United States of America
    for economy in df.index:
        df.loc[economy, "fx_carry"] = float(df.loc[economy, "policy_rate"]) - us_rate
        provenance[economy]["fx_carry"] = "derived:policy_rate_diff"
    return df


def add_surprises(
    df: pd.DataFrame,
    expected_change_columns: frozenset[str] = frozenset(),
) -> pd.DataFrame:
    """Compute per-feature surprises.

    Default (mock mode): surprise = actual - consensus  (beat/miss vs the
    period consensus; positive means the print ran hot).

    For columns named in ``expected_change_columns`` (live IMF mode), the
    consensus column holds the IMF next-year forecast, so the feature is the
    forecast-implied expected change = consensus - actual (positive means the
    series is expected to rise). The sign convention is identical either way
    ("higher/hotter => positive"), so the configured weight signs are unchanged.
    """
    out = df.copy()
    for actual_col, consensus_col, surprise_col in SURPRISE_SPECS:
        if surprise_col in expected_change_columns:
            out[surprise_col] = out[consensus_col] - out[actual_col]
        else:
            out[surprise_col] = out[actual_col] - out[consensus_col]
    return out


def add_ranked_features(df: pd.DataFrame, weights: dict) -> pd.DataFrame:
    out = df.copy()
    features = sorted(
        {
            feature.replace("_rank", "")
            for asset_weights in weights.values()
            for feature in asset_weights
        }
    )
    for feature in features:
        if feature not in out.columns:
            raise ValueError(f"Config references unknown feature: {feature}")
        out[f"{feature}_rank"] = percentile_to_signal(out[feature])
    return out


def compute_deterministic_signals(df: pd.DataFrame, weights: dict) -> pd.DataFrame:
    out = df.copy()

    for asset_class, asset_weights in weights.items():
        raw = pd.Series(0.0, index=out.index)
        for feature, weight in asset_weights.items():
            raw += float(weight) * out[feature]
        out[f"{asset_class}_raw_score"] = raw
        out[f"{asset_class}_deterministic_signal"] = percentile_to_signal(raw)

    return out


def strongest_feature(row: pd.Series, weights: dict[str, float]) -> tuple[str, float]:
    contributions = {
        feature: abs(float(weight) * row[feature])
        for feature, weight in weights.items()
    }
    feature = max(contributions, key=contributions.get)
    return feature.replace("_rank", ""), row[feature]


def describe_driver(country: str, asset_class: str, row: pd.Series, weights: dict[str, float]) -> str:
    feature, ranked_value = strongest_feature(row, weights)
    direction = "positive" if row[f"{asset_class}_deterministic_signal"] >= 0 else "negative"
    feature_label = feature.replace("_", " ")

    templates = {
        "fx": f"{direction.title()} FX signal led by {feature_label} and cross-sectional currency momentum/carry inputs.",
        "rates": f"{direction.title()} bond signal led by {feature_label}, with inflation, growth, policy, and labor surprises ranked across economies.",
        "equity": f"{direction.title()} equity signal led by {feature_label}, combining growth, PMI, momentum, and valuation ranks.",
        "real_estate": f"{direction.title()} real estate signal led by {feature_label}, balancing rates, policy, REIT momentum, housing, and labor ranks.",
    }
    if abs(ranked_value) < 0.35:
        return f"Balanced {asset_class.replace('_', ' ')} inputs leave {country} near the middle of the six-economy cross-section."
    return templates[asset_class]


def explain_contributions(row: pd.Series, weights: dict[str, float]) -> tuple[list[dict], list[dict]]:
    drivers = []
    for feature, weight in weights.items():
        value = float(row[feature])
        contribution = value * float(weight)
        drivers.append(
            {
                "feature": feature,
                "value": round(value, 3),
                "weight": round(float(weight), 3),
                "contribution": round(contribution, 3),
            }
        )

    positive = sorted(
        [item for item in drivers if item["contribution"] > 0],
        key=lambda item: item["contribution"],
        reverse=True,
    )[:3]
    negative = sorted(
        [item for item in drivers if item["contribution"] < 0],
        key=lambda item: item["contribution"],
    )[:3]
    return positive, negative


def _narrative_state(rag_signal: float, deterministic: float) -> str:
    """Whether the RAG narrative agrees with the deterministic call.

    Asymmetric by design: callers only ever let "disagrees" lower conviction;
    "agrees" never raises it, because the RAG overlay is still a hardcoded stub
    and must not be allowed to inflate confidence.
    """
    if rag_signal == 0 or deterministic == 0:
        return "no_view"
    return "agrees" if (rag_signal > 0) == (deterministic > 0) else "disagrees"


def _conviction_band(net_lean: float, top_driver_share: float, narrative: str) -> str:
    """Roll breadth + narrative agreement into a high/medium/low band."""
    if net_lean >= 0.60 and top_driver_share <= 0.50:
        base = "high"
    elif net_lean < 0.20 or top_driver_share > 0.60:
        base = "low"
    else:
        base = "medium"
    # Asymmetric: a "disagrees" view downgrades, but "agrees"/"no_view" never
    # upgrade — the RAG overlay is a stub and must not inflate conviction.
    if narrative == "disagrees":
        base = {"high": "medium", "medium": "low", "low": "low"}[base]
    return base


# Signals with |value| below this read as "Neutral" (matches the dashboard
# verdict threshold); a Neutral call has no direction to qualify, so conviction
# is reported as band "na".
_NEUTRAL_BAND = 0.10


def compute_conviction(
    row: pd.Series,
    asset_weights: dict[str, float],
    deterministic: float,
    rag_signal: float,
    final: float,
) -> dict:
    """Driver-breadth + narrative-agreement conviction for one asset signal.

    Breadth and narrative both reference the deterministic call direction.
    ``net_lean`` is in [-1, 1]; negative means the drivers point against the
    call, i.e. it survives only on the cross-sectional ranking. Breadth is
    computed over the full weight set, not the truncated driver lists stored
    in the snapshot.
    """
    narrative = _narrative_state(rag_signal, deterministic)
    contributions = {
        feature: float(row[feature]) * float(weight)
        for feature, weight in asset_weights.items()
    }
    gross = sum(abs(c) for c in contributions.values())

    if deterministic == 0 or abs(final) < _NEUTRAL_BAND or gross == 0:
        return {
            "band": "na",
            "net_lean": 0.0,
            "top_driver_share": 0.0,
            "top_driver": None,
            "narrative": narrative,
        }

    call_dir = 1.0 if deterministic > 0 else -1.0
    aligned = sum(abs(c) for c in contributions.values() if c * call_dir > 0)
    opposing = sum(abs(c) for c in contributions.values() if c * call_dir < 0)
    net_lean = (aligned - opposing) / gross

    top_feature = max(contributions, key=lambda f: abs(contributions[f]))
    top_driver_share = abs(contributions[top_feature]) / gross

    return {
        "band": _conviction_band(net_lean, top_driver_share, narrative),
        "net_lean": round(net_lean, 4),
        "top_driver_share": round(top_driver_share, 4),
        "top_driver": top_feature.removesuffix("_rank"),
        "narrative": narrative,
    }


def build_snapshot(
    df: pd.DataFrame,
    config: dict,
    provenance: dict[str, dict[str, str]],
    source: str = "mock",
    as_of: str | None = None,
) -> dict:
    weights = config["weights"]
    blend = config["signal_blend"]
    rag_weight = float(blend["rag_weight"])

    snapshot = {
        "as_of": as_of or date.today().isoformat(),
        "methodology_version": METHODOLOGY_VERSION,
        "data_source": source,
        "universe": list(UNIVERSE),
        "economies": {},
    }

    for country, row in df.iterrows():
        entry = {
            "country": country,
            "iso3": row["iso3"],
            "provenance": provenance[country],
            "signals": {},
            "composite": {},
        }

        deterministic_values = []
        rag_values = []
        final_values = []

        for asset_class in ASSET_CLASSES:
            asset_weights = weights[asset_class]
            deterministic = round(clip_signal(row[f"{asset_class}_deterministic_signal"]), 4)
            rag = compute_rag_signal(country, asset_class)
            rag_signal = round(clip_signal(rag["signal"]), 4)
            rag_confidence = float(rag["confidence"])
            final = round(blend_signal(deterministic, rag_signal, rag_confidence, rag_weight), 4)
            top_positive, top_negative = explain_contributions(row, asset_weights)
            conviction = compute_conviction(
                row, asset_weights, deterministic, rag_signal, final
            )

            deterministic_values.append(deterministic)
            rag_values.append(rag_signal)
            final_values.append(final)

            entry["signals"][asset_class] = {
                "deterministic": deterministic,
                "rag": rag_signal,
                "final": final,
                "driver": describe_driver(country, asset_class, row, asset_weights),
                "rag_summary": rag["summary"],
                "rag_confidence": round(rag_confidence, 4),
                "rag_effective_weight": round(rag_weight * rag_confidence, 4),
                "rag_sources": rag["sources"],
                "top_positive_drivers": top_positive,
                "top_negative_drivers": top_negative,
                "conviction": conviction,
            }

        entry["composite"] = {
            "deterministic": round(float(np.mean(deterministic_values)), 4),
            "rag": round(float(np.mean(rag_values)), 4),
            "final": round(float(np.mean(final_values)), 4),
        }
        snapshot["economies"][country] = entry

    return snapshot


def generate_snapshot(
    path: Path = SNAPSHOT_PATH,
    as_of: str | None = None,
    source: str = "mock",
) -> dict:
    config = load_signal_config()
    df, provenance, expected_change_columns = load_macro_inputs(source=source)
    df = overlay_market_inputs(df, provenance, source=source)
    df = overlay_fx_carry(df, provenance, source=source)
    df = add_surprises(df, expected_change_columns)
    df = add_ranked_features(df, config["weights"])
    df = compute_deterministic_signals(df, config["weights"])
    snapshot = build_snapshot(df, config, provenance, source=source, as_of=as_of)
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return snapshot


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate the macro signal snapshot.")
    parser.add_argument(
        "--source",
        choices=("mock", "live"),
        default="mock",
        help="Data source: 'mock' (bundled CSVs) or 'live' (World Bank API).",
    )
    args = parser.parse_args()
    generate_snapshot(source=args.source)
    print(f"Wrote {SNAPSHOT_PATH} (source={args.source})")
