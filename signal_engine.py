"""Compatibility facade and CLI for the staged macro signal pipeline.

Implementation lives under ``pipeline.stages`` and ``pipeline.orchestrator``;
imports are re-exported here so existing API and notebook callers keep working.
"""

from __future__ import annotations

from data_sources import gdelt, imf_weo, market
from pipeline.orchestrator import (
    PipelineResult,
    collect_live_batches,
    create_run_context,
    default_news_cache,
    generate_snapshot,
    run_signal_pipeline,
)
from pipeline.signal_definition import (
    ASSET_CLASSES,
    CONFIG_PATH,
    DATA_DIR,
    INPUT_PROVENANCE_COLUMNS,
    ISO3_BY_ECONOMY,
    LIVE_HISTORY_YEARS,
    LIVE_MACRO_COLUMNS,
    MARKET_LIVE_COLUMNS,
    METHODOLOGY_VERSION,
    NEWS_CACHE_PATH,
    REQUIRED_CONSENSUS_COLUMNS,
    REQUIRED_MACRO_COLUMNS,
    REQUIRED_MARKET_COLUMNS,
    REQUIRED_NEWS_COLUMNS,
    SNAPSHOT_PATH,
    SURPRISE_SPECS,
    UNIVERSE,
)
from pipeline.stages.config import config_hash, load_signal_config
from pipeline.stages.features import (
    add_ranked_features,
    add_surprises,
    blend_signal,
    clip_signal,
    compute_deterministic_signals,
    percentile_to_signal,
    run_feature_stages,
)
from pipeline.stages.inputs import (
    apply_live_observations,
    initialize_provenance,
    load_macro_inputs,
    load_mock_data,
    overlay_fx_carry,
    overlay_gdelt_observations,
    overlay_imf_expected_change,
    overlay_imf_observations,
    overlay_market_inputs,
    overlay_market_observations,
    overlay_news_pressure,
    overlay_world_bank_actuals,
    overlay_world_bank_observations,
    validate_input_frame,
)
from pipeline.stages.snapshot import (
    _conviction_band,
    _narrative_state,
    build_snapshot,
    compute_conviction,
    describe_driver,
    explain_contributions,
    strongest_feature,
)


__all__ = [
    "ASSET_CLASSES",
    "CONFIG_PATH",
    "DATA_DIR",
    "INPUT_PROVENANCE_COLUMNS",
    "ISO3_BY_ECONOMY",
    "LIVE_HISTORY_YEARS",
    "LIVE_MACRO_COLUMNS",
    "MARKET_LIVE_COLUMNS",
    "METHODOLOGY_VERSION",
    "NEWS_CACHE_PATH",
    "PipelineResult",
    "REQUIRED_CONSENSUS_COLUMNS",
    "REQUIRED_MACRO_COLUMNS",
    "REQUIRED_MARKET_COLUMNS",
    "REQUIRED_NEWS_COLUMNS",
    "SNAPSHOT_PATH",
    "SURPRISE_SPECS",
    "UNIVERSE",
    "add_ranked_features",
    "add_surprises",
    "apply_live_observations",
    "blend_signal",
    "build_snapshot",
    "clip_signal",
    "collect_live_batches",
    "compute_conviction",
    "compute_deterministic_signals",
    "config_hash",
    "create_run_context",
    "default_news_cache",
    "describe_driver",
    "explain_contributions",
    "generate_snapshot",
    "initialize_provenance",
    "load_macro_inputs",
    "load_mock_data",
    "load_signal_config",
    "overlay_fx_carry",
    "overlay_gdelt_observations",
    "overlay_imf_expected_change",
    "overlay_imf_observations",
    "overlay_market_inputs",
    "overlay_market_observations",
    "overlay_news_pressure",
    "overlay_world_bank_actuals",
    "overlay_world_bank_observations",
    "percentile_to_signal",
    "run_feature_stages",
    "run_signal_pipeline",
    "strongest_feature",
    "validate_input_frame",
]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate the macro signal snapshot.")
    parser.add_argument(
        "--source",
        choices=("mock", "live"),
        default="mock",
        help="Data source: mock fallback or live staged adapters.",
    )
    args = parser.parse_args()
    cache = default_news_cache() if args.source == "live" else None
    generate_snapshot(source=args.source, news_cache=cache)
    print(f"Wrote {SNAPSHOT_PATH} (source={args.source})")
