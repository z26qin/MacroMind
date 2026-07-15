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

__all__ = [
    "DataFrequency",
    "CoverageReport",
    "FallbackAction",
    "FallbackDecision",
    "FallbackPolicy",
    "FallbackResolution",
    "FallbackScope",
    "LIVE_FALLBACK_POLICIES",
    "LiveAdapter",
    "MetricCoverage",
    "Observation",
    "PipelineCoverageReport",
    "PipelineRunContext",
    "RunMode",
    "SourceBatch",
    "SourceError",
    "SourceStatus",
    "build_coverage_report",
    "combine_coverage_reports",
    "decide_fallback",
    "policy_for_source",
    "resolve_with_policy",
]
