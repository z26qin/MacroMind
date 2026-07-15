"""Single coordinator for the staged macro signal pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from data_sources import gdelt, imf_weo, market, world_bank
from data_sources.cache import TTLCache
from data_sources.normalization import Clock, capture_utc, utc_now
from evidence_store import EvidenceStore, open_default_evidence_store
from pipeline.contracts import PipelineRunContext, RunMode, SourceBatch
from pipeline.coverage import (
    PipelineCoverageReport,
    build_coverage_report,
    combine_coverage_reports,
)
from pipeline.quality import DataQualityReport, run_quality_gates
from pipeline.signal_definition import (
    CONFIG_PATH,
    LIVE_HISTORY_YEARS,
    METHODOLOGY_VERSION,
    NEWS_CACHE_PATH,
    SNAPSHOT_PATH,
    UNIVERSE,
)
from pipeline.stages.config import config_hash, load_signal_config
from pipeline.stages.features import run_feature_stages
from pipeline.stages.inputs import (
    apply_live_observations,
    initialize_provenance,
    load_mock_data,
)
from pipeline.stages.snapshot import build_snapshot
from pipeline.store import ObservationStore, StoreWriteResult


FetchJSON = Callable[[str], object]


@dataclass(frozen=True)
class PipelineResult:
    context: PipelineRunContext
    snapshot: dict
    batches: tuple[SourceBatch, ...]
    coverage: PipelineCoverageReport | None
    quality: DataQualityReport | None
    store_write: StoreWriteResult | None
    completed_stages: tuple[str, ...]


def _decision_boundary(
    as_of: str | None,
    started_at: datetime,
) -> tuple[str, datetime]:
    label = as_of or started_at.date().isoformat()
    if "T" not in label:
        try:
            decision_date = date.fromisoformat(label)
        except ValueError as exc:
            raise ValueError(f"as_of must be an ISO-8601 date or timestamp: {label!r}") from exc
        return label, datetime.combine(
            decision_date,
            time.max,
            tzinfo=timezone.utc,
        )
    try:
        parsed = datetime.fromisoformat(label.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"as_of must be an ISO-8601 date or timestamp: {label!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("as_of timestamps must include a timezone")
    return label, parsed.astimezone(timezone.utc)


def create_run_context(
    *,
    as_of: str | None,
    source: str,
    config_path: Path,
    clock: Clock = utc_now,
    run_id: str | None = None,
) -> tuple[PipelineRunContext, str]:
    started_at = capture_utc(clock, "started_at")
    as_of_label, decision_time = _decision_boundary(as_of, started_at)
    if source == "mock":
        mode = RunMode.MOCK
    elif source == "live":
        mode = RunMode.HISTORICAL if decision_time < started_at else RunMode.LIVE
    else:
        raise ValueError(f"Unknown data source: {source!r} (expected 'mock' or 'live')")
    context = PipelineRunContext(
        run_id=run_id or f"signal-{started_at:%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:8]}",
        as_of=decision_time,
        started_at=started_at,
        mode=mode,
        methodology_version=METHODOLOGY_VERSION,
        config_hash=config_hash(config_path),
    )
    return context, as_of_label


def collect_live_batches(
    context: PipelineRunContext,
    *,
    history_years: int = LIVE_HISTORY_YEARS,
    world_bank_fetch_json: FetchJSON | None = None,
    imf_fetch_json: FetchJSON | None = None,
    market_fetch_json: FetchJSON | None = None,
    gdelt_fetch_json: FetchJSON | None = None,
    news_cache: TTLCache | None = None,
    clock: Clock = utc_now,
) -> tuple[SourceBatch, ...]:
    """Acquire the four sources sequentially; concurrency is deliberately step 7."""
    economies = tuple(UNIVERSE)
    wb_kwargs = {"clock": clock}
    if world_bank_fetch_json is not None:
        wb_kwargs["fetch_json"] = world_bank_fetch_json
    imf_kwargs = {"clock": clock}
    if imf_fetch_json is not None:
        imf_kwargs["fetch_json"] = imf_fetch_json
    market_kwargs = {"clock": clock}
    if market_fetch_json is not None:
        market_kwargs["fetch_json"] = market_fetch_json
    gdelt_kwargs = {"clock": clock}
    if gdelt_fetch_json is not None:
        gdelt_kwargs["fetch_json"] = gdelt_fetch_json
    if news_cache is not None:
        gdelt_kwargs["cache"] = news_cache

    end_year = context.as_of.year
    return (
        world_bank.load_observations(
            context,
            economies,
            start_year=end_year - int(history_years),
            end_year=end_year,
            **wb_kwargs,
        ),
        imf_weo.load_observations(context, economies, **imf_kwargs),
        market.load_observations(context, economies, **market_kwargs),
        gdelt.load_observations(context, economies, **gdelt_kwargs),
    )


def build_pipeline_coverage(batches: tuple[SourceBatch, ...]) -> PipelineCoverageReport:
    expected_metrics = {
        world_bank.SOURCE: world_bank.LIVE_COLUMNS,
        imf_weo.SOURCE: imf_weo.FORECAST_COLUMNS,
        market.SOURCE: market.LIVE_COLUMNS,
        gdelt.SOURCE: ("news_pressure",),
    }
    reports = tuple(
        build_coverage_report(
            batch,
            expected_economies=UNIVERSE,
            expected_metrics=expected_metrics[batch.source],
        )
        for batch in batches
    )
    return combine_coverage_reports(reports)


def run_signal_pipeline(
    path: Path = SNAPSHOT_PATH,
    *,
    as_of: str | None = None,
    source: str = "mock",
    config_path: Path = CONFIG_PATH,
    world_bank_fetch_json: FetchJSON | None = None,
    imf_fetch_json: FetchJSON | None = None,
    market_fetch_json: FetchJSON | None = None,
    gdelt_fetch_json: FetchJSON | None = None,
    news_cache: TTLCache | None = None,
    evidence_store: EvidenceStore | None = None,
    observation_store: ObservationStore | None = None,
    clock: Clock = utc_now,
    run_id: str | None = None,
) -> PipelineResult:
    """Execute the explicit stages and return all auditable run artifacts."""
    path = Path(path)
    config_path = Path(config_path)
    completed = ["context"]
    context, as_of_label = create_run_context(
        as_of=as_of,
        source=source,
        config_path=config_path,
        clock=clock,
        run_id=run_id,
    )
    config = load_signal_config(config_path)
    completed.append("config")
    frame = load_mock_data()
    provenance = initialize_provenance(frame.index)
    expected_change_columns = frozenset()
    batches: tuple[SourceBatch, ...] = ()
    coverage: PipelineCoverageReport | None = None
    quality: DataQualityReport | None = None
    store_write: StoreWriteResult | None = None
    completed.append("baseline")

    if source == "live":
        history_years = int(config.get("data_source", {}).get("history_years", LIVE_HISTORY_YEARS))
        batches = collect_live_batches(
            context,
            history_years=history_years,
            world_bank_fetch_json=world_bank_fetch_json,
            imf_fetch_json=imf_fetch_json,
            market_fetch_json=market_fetch_json,
            gdelt_fetch_json=gdelt_fetch_json,
            news_cache=news_cache,
            clock=clock,
        )
        completed.append("acquire")
        coverage = build_pipeline_coverage(batches)
        completed.append("coverage")

        owns_observation_store = observation_store is None
        raw_store = observation_store or ObservationStore()
        try:
            store_write = raw_store.write_run(context, batches)
            completed.append("persist")
            observations_by_source = {
                batch.source: tuple(
                    record.observation
                    for record in raw_store.query_as_of(context.as_of, source=batch.source)
                )
                for batch in batches
            }
            completed.append("pit_select")
            quality_result = run_quality_gates(
                observations_by_source,
                as_of=context.as_of,
                economies=UNIVERSE,
            )
            quality = quality_result.report
            completed.append("quality_gates")
            expected_change_columns = apply_live_observations(
                frame,
                provenance,
                quality_result.by_source(),
            )
            completed.append("overlay")
        finally:
            if owns_observation_store:
                raw_store.close()

    frame = run_feature_stages(frame, config["weights"], expected_change_columns)
    completed.append("features")
    owns_evidence_store = evidence_store is None
    narrative_store = evidence_store or open_default_evidence_store()
    try:
        snapshot = build_snapshot(
            frame,
            config,
            provenance,
            source=source,
            as_of=as_of_label,
            evidence_store=narrative_store,
        )
    finally:
        if owns_evidence_store:
            narrative_store.close()
    completed.append("snapshot")
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    completed.append("output")
    return PipelineResult(
        context=context,
        snapshot=snapshot,
        batches=batches,
        coverage=coverage,
        quality=quality,
        store_write=store_write,
        completed_stages=tuple(completed),
    )


def generate_snapshot(
    path: Path = SNAPSHOT_PATH,
    as_of: str | None = None,
    source: str = "mock",
    gdelt_fetch_json=None,
    news_cache: TTLCache | None = None,
    evidence_store: EvidenceStore | None = None,
    observation_store: ObservationStore | None = None,
    world_bank_fetch_json=None,
    imf_fetch_json=None,
    market_fetch_json=None,
    clock: Clock = utc_now,
    run_id: str | None = None,
) -> dict:
    return run_signal_pipeline(
        path,
        as_of=as_of,
        source=source,
        world_bank_fetch_json=world_bank_fetch_json,
        imf_fetch_json=imf_fetch_json,
        market_fetch_json=market_fetch_json,
        gdelt_fetch_json=gdelt_fetch_json,
        news_cache=news_cache,
        evidence_store=evidence_store,
        observation_store=observation_store,
        clock=clock,
        run_id=run_id,
    ).snapshot


def default_news_cache() -> TTLCache:
    return TTLCache(NEWS_CACHE_PATH, gdelt.NEWS_CACHE_TTL_SECONDS)
