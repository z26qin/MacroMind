"""Judge-agnostic generation metrics retained for the analysis notebook."""
from __future__ import annotations

import re
from typing import Protocol


class Judge(Protocol):
    def entails(self, claim: str, context: str) -> bool: ...

    def relevance(self, question: str, answer: str) -> float: ...


def decompose_claims(answer: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", answer.strip())
    return [part.strip() for part in parts if part.strip()]


def faithfulness(answer: str, context: str, judge: Judge) -> float:
    claims = decompose_claims(answer)
    if not claims:
        return 0.0
    return sum(judge.entails(claim, context) for claim in claims) / len(claims)


def answer_relevancy(question: str, answer: str, judge: Judge) -> float:
    if not answer.strip():
        return 0.0
    return max(0.0, min(1.0, float(judge.relevance(question, answer))))
