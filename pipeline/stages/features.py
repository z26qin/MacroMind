"""Pure feature-engineering and deterministic scoring stages."""

from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.signal_definition import SURPRISE_SPECS


def clip_signal(value: float) -> float:
    return float(np.clip(value, -1.0, 1.0))


def blend_signal(
    deterministic: float,
    rag_signal: float,
    rag_confidence: float,
    rag_weight: float,
) -> float:
    """Blend a deterministic score with a confidence-scaled narrative score."""
    effective_rag = rag_weight * rag_confidence
    return clip_signal((1.0 - effective_rag) * deterministic + effective_rag * rag_signal)


def percentile_to_signal(series: pd.Series) -> pd.Series:
    """Map cross-sectional ranks to [-1, +1], including full endpoints."""
    if len(series) == 1:
        return pd.Series(0.0, index=series.index)
    pct = (series.rank(method="average") - 1.0) / (len(series) - 1.0)
    return (2.0 * pct - 1.0).clip(-1.0, 1.0)


def add_surprises(
    df: pd.DataFrame,
    expected_change_columns: frozenset[str] = frozenset(),
) -> pd.DataFrame:
    """Compute actual-minus-consensus or forecast-implied expected changes."""
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


def run_feature_stages(
    df: pd.DataFrame,
    weights: dict,
    expected_change_columns: frozenset[str],
) -> pd.DataFrame:
    surprised = add_surprises(df, expected_change_columns)
    ranked = add_ranked_features(surprised, weights)
    return compute_deterministic_signals(ranked, weights)
