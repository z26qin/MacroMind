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

import numpy as np
import pandas as pd
import yaml

from rag_signal import compute_rag_signal
from data_sources import world_bank as wb


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


def clip_signal(value: float) -> float:
    return float(np.clip(value, -1.0, 1.0))


def percentile_to_signal(series: pd.Series) -> pd.Series:
    """Map cross-sectional ranks to [-1, +1], including full endpoints."""
    if len(series) == 1:
        return pd.Series(0.0, index=series.index)

    pct = (series.rank(method="average") - 1.0) / (len(series) - 1.0)
    return (2.0 * pct - 1.0).clip(-1.0, 1.0)


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
) -> tuple[pd.DataFrame, dict[str, dict[str, str]]]:
    """Return (joined input frame, provenance).

    source="mock": the existing mock CSVs, every value tagged "mock".
    source="live": overlay World Bank values onto the mock frame for the
    live macro columns where available; everything else stays mock.
    """
    df = load_mock_data(data_dir)

    tracked_columns = sorted(REQUIRED_MACRO_COLUMNS - {"economy"})
    provenance = {
        economy: {column: "mock" for column in tracked_columns}
        for economy in df.index
    }

    if source == "mock":
        return df, provenance
    if source != "live":
        raise ValueError(f"Unknown data source: {source!r} (expected 'mock' or 'live')")

    from datetime import date

    end_year = date.today().year
    start_year = end_year - LIVE_HISTORY_YEARS
    kwargs = {} if fetch_json is None else {"fetch_json": fetch_json}
    macro, consensus, live_provenance = wb.load_world_bank_macro(
        tuple(df.index), start_year, end_year, **kwargs
    )

    consensus_column = {
        "inflation_yoy": "inflation_consensus",
        "gdp_growth": "gdp_consensus",
        "unemployment": "unemployment_consensus",
    }
    for economy in df.index:
        for column, value in macro[economy].items():
            df.loc[economy, column] = value
            df.loc[economy, consensus_column[column]] = consensus[economy][column]
            provenance[economy][column] = live_provenance[economy][column]

    if df.isna().any().any():
        raise ValueError("Macro inputs contain missing values after live overlay")
    return df, provenance


def add_surprises(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["inflation_surprise"] = out["inflation_yoy"] - out["inflation_consensus"]
    out["growth_surprise"] = out["gdp_growth"] - out["gdp_consensus"]
    out["unemployment_surprise"] = out["unemployment"] - out["unemployment_consensus"]
    out["policy_surprise"] = out["policy_rate"] - out["policy_rate_consensus"]
    out["pmi_surprise"] = out["pmi"] - out["pmi_consensus"]
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


def build_snapshot(
    df: pd.DataFrame,
    config: dict,
    provenance: dict[str, dict[str, str]],
    source: str = "mock",
    as_of: str | None = None,
) -> dict:
    weights = config["weights"]
    blend = config["signal_blend"]
    deterministic_weight = float(blend["deterministic_weight"])
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
            final = round(clip_signal(deterministic_weight * deterministic + rag_weight * rag_signal), 4)
            top_positive, top_negative = explain_contributions(row, asset_weights)

            deterministic_values.append(deterministic)
            rag_values.append(rag_signal)
            final_values.append(final)

            entry["signals"][asset_class] = {
                "deterministic": deterministic,
                "rag": rag_signal,
                "final": final,
                "driver": describe_driver(country, asset_class, row, asset_weights),
                "rag_summary": rag["summary"],
                "rag_confidence": round(float(rag["confidence"]), 4),
                "rag_sources": rag["sources"],
                "top_positive_drivers": top_positive,
                "top_negative_drivers": top_negative,
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
    df, provenance = load_macro_inputs(source=source)
    df = add_surprises(df)
    df = add_ranked_features(df, config["weights"])
    df = compute_deterministic_signals(df, config["weights"])
    snapshot = build_snapshot(df, config, provenance, source=source, as_of=as_of)
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return snapshot


if __name__ == "__main__":
    generate_snapshot()
    print(f"Wrote {SNAPSHOT_PATH}")
