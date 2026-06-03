"""Generate a deterministic macro regime-detection snapshot.

Mirrors signal_engine.py: read mock per-country regime inputs, apply
YAML-configured weights, compute regime / narrative-gap / cross-asset
confirmation scores, attach curated templates, and write a versioned
snapshot consumed by the FastAPI app and the dashboard's Regime tab.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

REGIME_SNAPSHOT_PATH = Path("regime_snapshot.json")
REGIME_CONFIG_PATH = Path("regime_config.yaml")
REGIME_TEMPLATES_PATH = Path("regime_templates.yaml")
REGIME_DATA_PATH = Path("data/mock_regime.csv")
METHODOLOGY_VERSION = "v0.1"

REGIME_UNIVERSE = ("Argentina", "Greece", "Turkey", "Japan", "China", "Brazil")
STRUCTURAL_BUCKETS = ("policy", "liquidity", "foreign_access", "rating_momentum", "index_catalyst")
CROSS_ASSET_CHANNELS = ("equity", "sovereign_credit", "fx", "cds", "etf_flows", "local_banks", "rates")
REQUIRED_REGIME_COLUMNS = {"country", "narrative_score", *STRUCTURAL_BUCKETS, *CROSS_ASSET_CHANNELS}


def clip_unit(value: float) -> float:
    return float(max(-1.0, min(1.0, value)))


def regime_verdict(regime_score: float, narrative_gap: float, thresholds: dict) -> str:
    if regime_score <= thresholds["deteriorating_max"]:
        return "Deteriorating"
    if narrative_gap >= thresholds["repricing_gap"]:
        return "Repricing"
    active = thresholds["active_min"]
    if regime_score >= active and narrative_gap >= active:
        return "Early"
    if regime_score >= active:
        return "Priced in"
    return "Neutral"


def load_regime_config(path: Path = REGIME_CONFIG_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Missing regime config: {path}")
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError(f"Malformed regime config {path}: expected a YAML object")
    weights = config.get("regime_weights")
    verdict = config.get("verdict")
    if not isinstance(weights, dict) or not weights:
        raise ValueError(f"Malformed regime config {path}: missing regime_weights")
    for bucket in STRUCTURAL_BUCKETS:
        if not isinstance(weights.get(bucket), (int, float)):
            raise ValueError(f"Malformed regime config {path}: weight {bucket} must be numeric")
    if not isinstance(verdict, dict):
        raise ValueError(f"Malformed regime config {path}: missing verdict thresholds")
    for key in ("deteriorating_max", "repricing_gap", "active_min"):
        if not isinstance(verdict.get(key), (int, float)):
            raise ValueError(f"Malformed regime config {path}: verdict.{key} must be numeric")
    return config


def load_regime_templates(path: Path = REGIME_TEMPLATES_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Missing regime templates: {path}")
    templates = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(templates, dict):
        raise ValueError(f"Malformed regime templates {path}: expected a YAML object")
    for country in REGIME_UNIVERSE:
        entry = templates.get(country)
        if not isinstance(entry, dict):
            raise ValueError(f"Malformed regime templates {path}: missing {country}")
        for key in ("drivers", "best_expressions", "left_tail_risks"):
            if not isinstance(entry.get(key), list) or not entry[key]:
                raise ValueError(f"Malformed regime templates {path}: {country}.{key} must be a non-empty list")
    return templates


def load_regime_inputs(path: Path = REGIME_DATA_PATH) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing_columns = sorted(REQUIRED_REGIME_COLUMNS - set(df.columns))
    if missing_columns:
        raise ValueError(f"{path} is missing required columns: {missing_columns}")
    duplicates = df["country"][df["country"].duplicated()].tolist()
    if duplicates:
        raise ValueError(f"{path} has duplicate countries: {duplicates}")
    missing_countries = sorted(set(REGIME_UNIVERSE) - set(df["country"]))
    if missing_countries:
        raise ValueError(f"{path} is missing required countries: {missing_countries}")
    numeric_columns = sorted(REQUIRED_REGIME_COLUMNS - {"country"})
    if df[numeric_columns].isna().any().any():
        raise ValueError(f"{path} contains missing values in required columns")
    return df.set_index("country").loc[list(REGIME_UNIVERSE)]


def compute_regime_scores(df: pd.DataFrame, config: dict) -> dict:
    weights = config["regime_weights"]
    thresholds = config["verdict"]
    results: dict[str, dict] = {}
    for country, row in df.iterrows():
        regime_score = clip_unit(sum(float(weights[b]) * float(row[b]) for b in STRUCTURAL_BUCKETS))
        narrative_score = float(row["narrative_score"])
        narrative_gap = regime_score - narrative_score
        confirmation = sum(float(row[c]) for c in CROSS_ASSET_CHANNELS) / len(CROSS_ASSET_CHANNELS)
        results[country] = {
            "regime_score": round(regime_score, 4),
            "narrative_score": round(narrative_score, 4),
            "narrative_gap": round(narrative_gap, 4),
            "confirmation_score": round(confirmation, 4),
            "verdict": regime_verdict(regime_score, narrative_gap, thresholds),
            "buckets": {b: round(float(row[b]), 4) for b in STRUCTURAL_BUCKETS},
            "cross_asset_confirmation": {c: round(float(row[c]), 4) for c in CROSS_ASSET_CHANNELS},
        }
    return results


def build_regime_snapshot(df: pd.DataFrame, config: dict, templates: dict, as_of: str | None = None) -> dict:
    scores = compute_regime_scores(df, config)
    countries: dict[str, dict] = {}
    for country in df.index:
        entry = {"country": country, **scores[country]}
        tpl = templates[country]
        entry["drivers"] = list(tpl["drivers"])
        entry["best_expressions"] = list(tpl["best_expressions"])
        entry["left_tail_risks"] = list(tpl["left_tail_risks"])
        countries[country] = entry
    return {
        "as_of": as_of or date.today().isoformat(),
        "methodology_version": METHODOLOGY_VERSION,
        "regime_universe": list(REGIME_UNIVERSE),
        "countries": countries,
    }


def generate_regime_snapshot(path: Path = REGIME_SNAPSHOT_PATH, as_of: str | None = None) -> dict:
    config = load_regime_config()
    templates = load_regime_templates()
    df = load_regime_inputs()
    snapshot = build_regime_snapshot(df, config, templates, as_of=as_of)
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return snapshot


if __name__ == "__main__":
    generate_regime_snapshot()
    print(f"Wrote {REGIME_SNAPSHOT_PATH}")
