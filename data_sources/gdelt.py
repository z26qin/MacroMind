"""GDELT news-pressure adapter (free, no API key).

The adapter queries GDELT DOC 2.0 article lists for each economy over a short
lookback window. A simple pressure score compares stress-related article flow
against constructive/relief article flow:

    news_pressure = (stress_articles - relief_articles) / sqrt(total_articles)

The level is intentionally simple and explainable; the signal engine ranks it
cross-sectionally as ``news_pressure_rank`` before applying asset-class weights.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import hashlib
from math import sqrt
from typing import Callable
from urllib.parse import urlencode

from data_sources.http import fetch_json as http_fetch_json

GDELT_DOC_BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
LOOKBACK = "7d"
MAX_RECORDS = 250
NEWS_CACHE_TTL_SECONDS = 21600  # 6 hours

ECONOMY_QUERY = {
    "United States of America": '("United States" OR USA OR America)',
    "Canada": "(Canada OR Canadian)",
    "China": "(China OR Chinese)",
    "Japan": "(Japan OR Japanese)",
    "Brazil": "(Brazil OR Brazilian)",
    "Euro Area": '("Euro Area" OR eurozone OR "European Central Bank" OR ECB)',
}

STRESS_TERMS = (
    '"policy uncertainty"',
    "inflation",
    "recession",
    "protest",
    "sanctions",
    '"currency crisis"',
    "default",
    '"capital controls"',
)
RELIEF_TERMS = (
    '"soft landing"',
    "disinflation",
    '"rate cuts"',
    "reform",
    "stimulus",
    '"growth rebound"',
)


def _default_fetch_json(url: str) -> dict:
    return http_fetch_json(url, timeout=3.0, retries=0)


def build_query(economy: str, terms: tuple[str, ...]) -> str:
    economy_clause = ECONOMY_QUERY[economy]
    terms_clause = "(" + " OR ".join(terms) + ")"
    return f"{economy_clause} {terms_clause}"


def build_url(query: str, lookback: str = LOOKBACK, max_records: int = MAX_RECORDS) -> str:
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "timespan": lookback,
        "maxrecords": str(max_records),
        "sort": "datedesc",
    }
    return f"{GDELT_DOC_BASE}?{urlencode(params)}"


def terms_version() -> str:
    """Short stable hash of the query term lists.

    Folded into the cache key so changing STRESS_TERMS / RELIEF_TERMS
    invalidates affected entries instead of reusing a stale score.
    """
    payload = "|".join((*STRESS_TERMS, "::", *RELIEF_TERMS)).encode("utf-8")
    return hashlib.sha1(payload, usedforsecurity=False).hexdigest()[:8]


def cache_key(economy: str) -> str:
    return f"{economy}|{LOOKBACK}|{terms_version()}"


def article_count(query: str, fetch_json: Callable[[str], dict] = _default_fetch_json) -> int:
    """Return the number of matching GDELT articles in the configured window."""
    payload = fetch_json(build_url(query))
    articles = payload.get("articles")
    if isinstance(articles, list):
        return len(articles)
    # Some GDELT modes return totals; keep this tolerant for future API shapes.
    if isinstance(payload.get("count"), int):
        return int(payload["count"])
    if isinstance(payload.get("totalArticles"), int):
        return int(payload["totalArticles"])
    return 0


def pressure_score(stress_count: int, relief_count: int) -> float:
    total = stress_count + relief_count
    if total <= 0:
        return 0.0
    return round((stress_count - relief_count) / sqrt(total), 4)


def _load_one_economy(
    economy: str,
    fetch_json: Callable[[str], dict],
) -> tuple[str, tuple[float, str] | None]:
    try:
        stress = article_count(build_query(economy, STRESS_TERMS), fetch_json=fetch_json)
        relief = article_count(build_query(economy, RELIEF_TERMS), fetch_json=fetch_json)
    except Exception:
        return economy, None
    return economy, (pressure_score(stress, relief), date.today().isoformat())


def load_news_pressure(
    economies: tuple[str, ...],
    fetch_json: Callable[[str], dict] = _default_fetch_json,
) -> dict[str, tuple[float, str]]:
    """Return {economy: (news_pressure, as_of_date)} for mapped economies."""
    out: dict[str, tuple[float, str]] = {}
    mapped = [economy for economy in economies if economy in ECONOMY_QUERY]
    with ThreadPoolExecutor(max_workers=min(6, len(mapped) or 1)) as executor:
        futures = {
            executor.submit(_load_one_economy, economy, fetch_json): economy
            for economy in mapped
        }
        for future in as_completed(futures):
            economy, result = future.result()
            if result is not None:
                out[economy] = result
    return out
