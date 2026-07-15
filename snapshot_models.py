"""Pydantic models for the signal snapshot JSON contract."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContributionModel(SnapshotModel):
    feature: str
    value: float
    weight: float
    contribution: float


class ConvictionModel(SnapshotModel):
    band: str
    net_lean: float = Field(ge=-1.0, le=1.0)
    top_driver_share: float = Field(ge=0.0, le=1.0)
    top_driver: str | None
    narrative: str


class NarrativeCitationModel(SnapshotModel):
    evidence_id: str
    source: str
    title: str
    source_uri: str
    event_time: str
    observed_at: str
    revision: str
    vintage: str
    excerpt: str


class StructuredNarrativeModel(SnapshotModel):
    direction: str
    signal: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    horizon: str
    as_of: str
    evidence_count: int = Field(ge=0)
    positive_factors: list[str]
    negative_factors: list[str]
    citations: list[NarrativeCitationModel]


class AssetSignalModel(SnapshotModel):
    deterministic: float = Field(ge=-1.0, le=1.0)
    rag: float = Field(ge=-1.0, le=1.0)
    final: float = Field(ge=-1.0, le=1.0)
    driver: str
    rag_summary: str
    rag_confidence: float = Field(ge=0.0, le=1.0)
    rag_effective_weight: float = Field(ge=0.0, le=1.0)
    rag_sources: list[str]
    rag_analysis: StructuredNarrativeModel
    top_positive_drivers: list[ContributionModel]
    top_negative_drivers: list[ContributionModel]
    conviction: ConvictionModel


class CompositeSignalModel(SnapshotModel):
    deterministic: float = Field(ge=-1.0, le=1.0)
    rag: float = Field(ge=-1.0, le=1.0)
    final: float = Field(ge=-1.0, le=1.0)


class EconomySnapshotModel(SnapshotModel):
    country: str
    iso3: str
    provenance: dict[str, str]
    signals: dict[str, AssetSignalModel]
    composite: CompositeSignalModel


class SignalSnapshotModel(SnapshotModel):
    as_of: str
    methodology_version: str
    data_source: str
    universe: list[str]
    economies: dict[str, EconomySnapshotModel]


def model_to_dict(model: BaseModel) -> dict[str, Any]:
    """Return a plain dict across Pydantic versions."""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()
