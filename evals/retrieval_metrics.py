"""Small dependency-free information-retrieval metrics."""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence


def hit_rate_at_k(retrieved_ids: Sequence[str], gold_ids: set[str], k: int) -> float:
    return 1.0 if any(item in gold_ids for item in list(retrieved_ids)[:k]) else 0.0


def recall_at_k(retrieved_ids: Sequence[str], gold_ids: set[str], k: int) -> float:
    if not gold_ids:
        return 0.0
    return len(set(list(retrieved_ids)[:k]) & gold_ids) / len(gold_ids)


def reciprocal_rank(retrieved_ids: Sequence[str], gold_ids: set[str]) -> float:
    for rank, item in enumerate(retrieved_ids, start=1):
        if item in gold_ids:
            return 1.0 / rank
    return 0.0


def mrr(rankings: Iterable[tuple[Sequence[str], set[str]]]) -> float:
    values = [reciprocal_rank(retrieved, gold) for retrieved, gold in rankings]
    return sum(values) / len(values) if values else 0.0


def ndcg_at_k(retrieved_ids: Sequence[str], gold_ids: set[str], k: int) -> float:
    top_k = list(retrieved_ids)[:k]
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, item in enumerate(top_k, start=1)
        if item in gold_ids
    )
    ideal_hits = min(len(gold_ids), k)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / ideal if ideal else 0.0
