"""Placeholder qualitative RAG signal interface.

This module intentionally avoids external API calls. It gives the dashboard a
stable interface that can later be backed by real retrieval and LLM logic.
"""

from __future__ import annotations


ASSET_CLASSES = ("fx", "rates", "equity", "real_estate")


MOCK_RAG_SIGNALS = {
    ("United States of America", "equity"): {
        "signal": 0.30,
        "confidence": 0.75,
        "summary": "News tone is mildly positive due to resilient AI capex and earnings strength.",
        "sources": ["documents/us_equity.txt"],
    },
    ("Japan", "fx"): {
        "signal": -0.20,
        "confidence": 0.60,
        "summary": "Qualitative tone is cautious due to policy normalization uncertainty.",
        "sources": ["documents/japan_fx.txt"],
    },
    ("China", "equity"): {
        "signal": -0.20,
        "confidence": 0.70,
        "summary": "Narrative remains cautious due to property-sector weakness and soft confidence.",
        "sources": ["documents/china_equity.txt"],
    },
    ("Canada", "real_estate"): {
        "signal": -0.20,
        "confidence": 0.70,
        "summary": "Qualitative tone is cautious due to mortgage reset and affordability pressure.",
        "sources": ["documents/canada_real_estate.txt"],
    },
    ("Brazil", "fx"): {
        "signal": 0.20,
        "confidence": 0.65,
        "summary": "News narrative is supportive due to carry demand and resilient external balances.",
        "sources": ["documents/brazil_fx.txt"],
    },
    ("Euro Area", "equity"): {
        "signal": -0.20,
        "confidence": 0.65,
        "summary": "Narrative is cautious due to weak manufacturing data and subdued earnings tone.",
        "sources": ["documents/euro_area_equity.txt"],
    },
}


DEFAULT_RAG_SIGNAL = {
    "signal": 0.0,
    "confidence": 0.35,
    "summary": "No strong mock qualitative narrative is assigned for this economy and asset class.",
    "sources": [],
}


def compute_rag_signal(country: str, asset_class: str) -> dict:
    """Return a mock qualitative signal in [-1, +1].

    TODO: Replace hardcoded RAG signal with:
    1. ingest macro news / central bank speeches / broker notes
    2. chunk documents
    3. embed chunks
    4. retrieve country + asset-class relevant context
    5. ask LLM to classify qualitative tilt: bullish / neutral / bearish
    6. map classification + confidence to [-1, +1]
    """
    if asset_class not in ASSET_CLASSES:
        raise ValueError(f"Unsupported asset class: {asset_class}")

    signal = MOCK_RAG_SIGNALS.get((country, asset_class), DEFAULT_RAG_SIGNAL)
    return {
        "signal": float(max(-1.0, min(1.0, signal["signal"]))),
        "confidence": float(max(0.0, min(1.0, signal["confidence"]))),
        "summary": signal["summary"],
        "sources": list(signal["sources"]),
    }
