"""Deterministic citation-grounding checks."""
from __future__ import annotations

import re
from collections.abc import Sequence


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def phrase_is_grounded(phrase: str, source_text: str) -> bool:
    normalized = _normalize(phrase)
    return bool(normalized) and normalized in _normalize(source_text)


def grounding_rate(key_phrases: Sequence[str], source_text: str) -> float:
    phrases = [phrase for phrase in key_phrases if phrase and phrase.strip()]
    if not phrases:
        return 0.0
    return sum(phrase_is_grounded(phrase, source_text) for phrase in phrases) / len(phrases)
