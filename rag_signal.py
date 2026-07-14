"""Point-in-time, cited, structured narrative signal extraction.

The default extractor is deterministic so local development and CI remain
offline. A production LLM extractor can implement ``NarrativeExtractor`` and is
still required to return the same validated structure and verbatim citations.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Protocol, Sequence

from evidence_store import EvidenceRecord, EvidenceStore, open_default_evidence_store


ASSET_CLASSES = ("fx", "rates", "equity", "real_estate")
DEFAULT_HORIZON = "3m"

POSITIVE_TERMS = (
    "resilient",
    "strong",
    "improving",
    "supportive",
    "stimulus",
    "high carry",
    "investor demand",
    "rebound",
    "easing",
    "disinflation",
)
NEGATIVE_TERMS = (
    "uncertain",
    "vulnerable",
    "cautious",
    "weakness",
    "weak",
    "subdued",
    "downside risk",
    "stress",
    "weigh",
    "recession risk",
    "stretched",
)


def decision_timestamp(as_of: str | None) -> str:
    """Turn a snapshot date into an inclusive end-of-day UTC timestamp."""
    value = as_of or date.today().isoformat()
    if "T" not in value:
        return f"{value}T23:59:59Z"
    return value


@dataclass(frozen=True)
class NarrativeCitation:
    evidence_id: str
    source: str
    title: str
    source_uri: str
    event_time: str
    observed_at: str
    revision: str
    vintage: str
    excerpt: str


@dataclass(frozen=True)
class StructuredNarrative:
    direction: str
    signal: float
    confidence: float
    horizon: str
    as_of: str
    evidence_count: int
    positive_factors: tuple[str, ...]
    negative_factors: tuple[str, ...]
    citations: tuple[NarrativeCitation, ...]

    def to_dict(self) -> dict:
        return asdict(self)


class NarrativeExtractor(Protocol):
    def extract(
        self,
        records: Sequence[EvidenceRecord],
        *,
        country: str,
        asset: str,
        horizon: str,
        as_of: str,
    ) -> StructuredNarrative: ...


def _matches(content: str, terms: tuple[str, ...]) -> list[str]:
    lowered = content.lower()
    return [term for term in terms if re.search(rf"\b{re.escape(term)}\b", lowered)]


def _excerpt(content: str, limit: int = 280) -> str:
    normalized = " ".join(content.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1].rstrip() + "…"


class KeywordNarrativeExtractor:
    """Offline structured baseline operating on retrieved evidence, not country maps."""

    def extract(
        self,
        records: Sequence[EvidenceRecord],
        *,
        country: str,
        asset: str,
        horizon: str,
        as_of: str,
    ) -> StructuredNarrative:
        if not records:
            return StructuredNarrative(
                direction="no_view",
                signal=0.0,
                confidence=0.0,
                horizon=horizon,
                as_of=as_of,
                evidence_count=0,
                positive_factors=(),
                negative_factors=(),
                citations=(),
            )

        positive: set[str] = set()
        negative: set[str] = set()
        citations: list[NarrativeCitation] = []
        for record in records:
            positive.update(_matches(record.content, POSITIVE_TERMS))
            negative.update(_matches(record.content, NEGATIVE_TERMS))
            citations.append(
                NarrativeCitation(
                    evidence_id=record.evidence_id,
                    source=record.source,
                    title=record.title,
                    source_uri=record.source_uri,
                    event_time=record.event_time,
                    observed_at=record.observed_at,
                    revision=record.revision,
                    vintage=record.vintage,
                    excerpt=_excerpt(record.content),
                )
            )

        positive_factors = tuple(sorted(positive))
        negative_factors = tuple(sorted(negative))
        matched = len(positive_factors) + len(negative_factors)
        tilt = 0.0 if matched == 0 else (len(positive_factors) - len(negative_factors)) / matched
        signal = round(max(-0.5, min(0.5, tilt * 0.5)), 4)
        if signal > 0.05:
            direction = "bullish"
        elif signal < -0.05:
            direction = "bearish"
        else:
            direction = "neutral"

        # Confidence reflects coverage, source diversity, and directional
        # separation. It is zero only when there is genuinely no evidence.
        source_diversity = len({record.source for record in records})
        confidence = min(
            0.9,
            0.35
            + 0.10 * min(len(records), 3)
            + 0.05 * min(source_diversity, 2)
            + 0.20 * abs(tilt),
        )
        return StructuredNarrative(
            direction=direction,
            signal=signal,
            confidence=round(confidence, 4),
            horizon=horizon,
            as_of=as_of,
            evidence_count=len(records),
            positive_factors=positive_factors,
            negative_factors=negative_factors,
            citations=tuple(citations),
        )


def extract_narrative(
    country: str,
    asset_class: str,
    *,
    store: EvidenceStore,
    as_of: str | None = None,
    horizon: str = DEFAULT_HORIZON,
    extractor: NarrativeExtractor | None = None,
) -> StructuredNarrative:
    if asset_class not in ASSET_CLASSES:
        raise ValueError(f"Unsupported asset class: {asset_class}")
    cutoff = decision_timestamp(as_of)
    records = store.query(
        country=country,
        asset=asset_class,
        horizon=horizon,
        as_of=cutoff,
    )
    return (extractor or KeywordNarrativeExtractor()).extract(
        records,
        country=country,
        asset=asset_class,
        horizon=horizon,
        as_of=cutoff,
    )


def compute_rag_signal(
    country: str,
    asset_class: str,
    *,
    store: EvidenceStore | None = None,
    as_of: str | None = None,
    horizon: str = DEFAULT_HORIZON,
    extractor: NarrativeExtractor | None = None,
) -> dict:
    """Return the compatibility signal plus a cited structured analysis block."""
    owns_store = store is None
    evidence_store = store or open_default_evidence_store()
    try:
        analysis = extract_narrative(
            country,
            asset_class,
            store=evidence_store,
            as_of=as_of,
            horizon=horizon,
            extractor=extractor,
        )
    finally:
        if owns_store:
            evidence_store.close()

    if analysis.direction == "no_view":
        summary = "No point-in-time evidence is available for this country, asset, and horizon."
    else:
        summary = (
            f"{analysis.direction.title()} {analysis.horizon} narrative from "
            f"{analysis.evidence_count} cited evidence item(s)."
        )
    return {
        "signal": analysis.signal,
        "confidence": analysis.confidence,
        "summary": summary,
        "sources": [citation.source_uri for citation in analysis.citations],
        "analysis": analysis.to_dict(),
    }
