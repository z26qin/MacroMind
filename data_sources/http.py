"""Shared HTTP helpers for no-key public data sources."""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_RETRIES = 3
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def fetch_json(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    retries: int = DEFAULT_RETRIES,
    backoff_seconds: float = 1.5,
    retry_statuses: frozenset[int] = RETRY_STATUSES,
    follow_redirects: bool = True,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Fetch JSON with consistent timeout, retry, and HTTP error behavior."""
    if retries < 0:
        raise ValueError("retries must be non-negative")

    response: httpx.Response | None = None
    for attempt in range(retries + 1):
        response = httpx.get(
            url,
            timeout=timeout,
            headers=dict(headers or {}),
            follow_redirects=follow_redirects,
        )
        if response.status_code not in retry_statuses:
            response.raise_for_status()
            return response.json()
        if attempt < retries:
            sleep(backoff_seconds * (attempt + 1))

    assert response is not None
    response.raise_for_status()
    return response.json()
