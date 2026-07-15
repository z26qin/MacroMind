"""Snapshot assembly stage for deterministic and cited narrative signals."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from evidence_store import EvidenceStore
from pipeline.signal_definition import ASSET_CLASSES, METHODOLOGY_VERSION, UNIVERSE
from pipeline.stages.features import blend_signal, clip_signal
from rag_signal import compute_rag_signal
from snapshot_models import (
    AssetSignalModel,
    CompositeSignalModel,
    EconomySnapshotModel,
    SignalSnapshotModel,
    model_to_dict,
)


def strongest_feature(row: pd.Series, weights: dict[str, float]) -> tuple[str, float]:
    contributions = {
        feature: abs(float(weight) * row[feature])
        for feature, weight in weights.items()
    }
    feature = max(contributions, key=contributions.get)
    return feature.replace("_rank", ""), row[feature]


def describe_driver(
    country: str,
    asset_class: str,
    row: pd.Series,
    weights: dict[str, float],
) -> str:
    feature, ranked_value = strongest_feature(row, weights)
    direction = "positive" if row[f"{asset_class}_deterministic_signal"] >= 0 else "negative"
    feature_label = feature.replace("_", " ")
    templates = {
        "fx": (
            f"{direction.title()} FX signal led by {feature_label} and "
            "cross-sectional currency momentum/carry inputs."
        ),
        "rates": (
            f"{direction.title()} bond signal led by {feature_label}, with inflation, "
            "growth, policy, and labor surprises ranked across economies."
        ),
        "equity": (
            f"{direction.title()} equity signal led by {feature_label}, combining "
            "growth, PMI, momentum, and valuation ranks."
        ),
        "real_estate": (
            f"{direction.title()} real estate signal led by {feature_label}, balancing "
            "rates, policy, REIT momentum, housing, and labor ranks."
        ),
    }
    if abs(ranked_value) < 0.35:
        return (
            f"Balanced {asset_class.replace('_', ' ')} inputs leave {country} "
            "near the middle of the six-economy cross-section."
        )
    return templates[asset_class]


def explain_contributions(
    row: pd.Series,
    weights: dict[str, float],
) -> tuple[list[dict], list[dict]]:
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
    if rag_signal == 0 or deterministic == 0:
        return "no_view"
    return "agrees" if (rag_signal > 0) == (deterministic > 0) else "disagrees"


def _conviction_band(net_lean: float, top_driver_share: float, narrative: str) -> str:
    if net_lean >= 0.60 and top_driver_share <= 0.50:
        base = "high"
    elif net_lean < 0.20 or top_driver_share > 0.60:
        base = "low"
    else:
        base = "medium"
    if narrative == "disagrees":
        base = {"high": "medium", "medium": "low", "low": "low"}[base]
    return base


_NEUTRAL_BAND = 0.10


def compute_conviction(
    row: pd.Series,
    asset_weights: dict[str, float],
    deterministic: float,
    rag_signal: float,
    final: float,
) -> dict:
    narrative = _narrative_state(rag_signal, deterministic)
    contributions = {
        feature: float(row[feature]) * float(weight)
        for feature, weight in asset_weights.items()
    }
    gross = sum(abs(contribution) for contribution in contributions.values())
    if deterministic == 0 or abs(final) < _NEUTRAL_BAND or gross == 0:
        return {
            "band": "na",
            "net_lean": 0.0,
            "top_driver_share": 0.0,
            "top_driver": None,
            "narrative": narrative,
        }

    call_direction = 1.0 if deterministic > 0 else -1.0
    aligned = sum(
        abs(contribution)
        for contribution in contributions.values()
        if contribution * call_direction > 0
    )
    opposing = sum(
        abs(contribution)
        for contribution in contributions.values()
        if contribution * call_direction < 0
    )
    net_lean = (aligned - opposing) / gross
    top_feature = max(contributions, key=lambda feature: abs(contributions[feature]))
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
    evidence_store: EvidenceStore | None = None,
) -> dict:
    weights = config["weights"]
    rag_weight = float(config["signal_blend"]["rag_weight"])
    economies: dict[str, EconomySnapshotModel] = {}

    for country, row in df.iterrows():
        signals: dict[str, AssetSignalModel] = {}
        deterministic_values = []
        rag_values = []
        final_values = []
        for asset_class in ASSET_CLASSES:
            asset_weights = weights[asset_class]
            deterministic = round(clip_signal(row[f"{asset_class}_deterministic_signal"]), 4)
            rag = compute_rag_signal(country, asset_class, store=evidence_store, as_of=as_of)
            rag_signal = round(clip_signal(rag["signal"]), 4)
            rag_confidence = float(rag["confidence"])
            final = round(
                blend_signal(deterministic, rag_signal, rag_confidence, rag_weight),
                4,
            )
            top_positive, top_negative = explain_contributions(row, asset_weights)
            conviction = compute_conviction(
                row,
                asset_weights,
                deterministic,
                rag_signal,
                final,
            )
            deterministic_values.append(deterministic)
            rag_values.append(rag_signal)
            final_values.append(final)
            signals[asset_class] = AssetSignalModel(
                deterministic=deterministic,
                rag=rag_signal,
                final=final,
                driver=describe_driver(country, asset_class, row, asset_weights),
                rag_summary=rag["summary"],
                rag_confidence=round(rag_confidence, 4),
                rag_effective_weight=round(rag_weight * rag_confidence, 4),
                rag_sources=rag["sources"],
                rag_analysis=rag["analysis"],
                top_positive_drivers=top_positive,
                top_negative_drivers=top_negative,
                conviction=conviction,
            )

        economies[country] = EconomySnapshotModel(
            country=country,
            iso3=row["iso3"],
            provenance=provenance[country],
            signals=signals,
            composite=CompositeSignalModel(
                deterministic=round(float(np.mean(deterministic_values)), 4),
                rag=round(float(np.mean(rag_values)), 4),
                final=round(float(np.mean(final_values)), 4),
            ),
        )

    snapshot = SignalSnapshotModel(
        as_of=as_of or date.today().isoformat(),
        methodology_version=METHODOLOGY_VERSION,
        data_source=source,
        universe=list(UNIVERSE),
        economies=economies,
    )
    return model_to_dict(snapshot)
