"""Shared contracts for MacroMind's modular data pipeline."""

from pipeline.contracts import (
    DataFrequency,
    Observation,
    PipelineRunContext,
    RunMode,
    SourceBatch,
    SourceError,
    SourceStatus,
)
from pipeline.adapters import LiveAdapter
from pipeline.coverage import (
    CoverageReport,
    MetricCoverage,
    PipelineCoverageReport,
    build_coverage_report,
    combine_coverage_reports,
)
from pipeline.fallback import (
    FallbackAction,
    FallbackDecision,
    FallbackPolicy,
    FallbackResolution,
    FallbackScope,
    LIVE_FALLBACK_POLICIES,
    decide_fallback,
    policy_for_source,
    resolve_with_policy,
)
from pipeline.store import (
    DEFAULT_OBSERVATION_DB,
    ImmutableStoreConflict,
    ObservationStore,
    StoredObservation,
    StoreWriteResult,
)

__all__ = [
    "DataFrequency",
    "DEFAULT_OBSERVATION_DB",
    "CoverageReport",
    "FallbackAction",
    "FallbackDecision",
    "FallbackPolicy",
    "FallbackResolution",
    "FallbackScope",
    "LIVE_FALLBACK_POLICIES",
    "ImmutableStoreConflict",
    "LiveAdapter",
    "MetricCoverage",
    "Observation",
    "ObservationStore",
    "PipelineCoverageReport",
    "PipelineRunContext",
    "RunMode",
    "SourceBatch",
    "SourceError",
    "SourceStatus",
    "StoredObservation",
    "StoreWriteResult",
    "build_coverage_report",
    "combine_coverage_reports",
    "decide_fallback",
    "policy_for_source",
    "resolve_with_policy",
]
