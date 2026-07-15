"""Configuration loading and hashing stage."""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from pipeline.signal_definition import ASSET_CLASSES, CONFIG_PATH


def config_hash(path: Path = CONFIG_PATH) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing signal config: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
                raise ValueError(
                    f"Malformed signal config {path}: {feature} must be a ranked feature"
                )
            if not isinstance(value, (int, float)):
                raise ValueError(
                    f"Malformed signal config {path}: weight {asset_class}.{feature} must be numeric"
                )

    for key in ("deterministic_weight", "rag_weight"):
        if not isinstance(blend.get(key), (int, float)):
            raise ValueError(f"Malformed signal config {path}: signal_blend.{key} must be numeric")
    if abs(float(blend["deterministic_weight"]) + float(blend["rag_weight"]) - 1.0) > 1e-9:
        raise ValueError(
            f"Malformed signal config {path}: signal_blend deterministic_weight + "
            f"rag_weight must sum to 1.0"
        )

    return config
