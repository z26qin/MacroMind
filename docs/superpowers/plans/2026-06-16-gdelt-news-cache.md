# GDELT news-pressure cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cache GDELT news-pressure scores on disk with a 6-hour TTL so repeated live snapshot runs reuse scores instead of re-firing 12 GDELT requests.

**Architecture:** A new generic `TTLCache` (single JSON file, injectable clock, atomic write) lives in `data_sources/cache.py`. `gdelt.load_news_pressure` gains an optional `cache` and checks it per economy before fetching, keyed by `economy|lookback|terms_hash`. `signal_engine` threads an optional cache through `overlay_news_pressure` / `generate_snapshot`; the CLI constructs the real disk cache (at `.cache/gdelt_news.json`) only for `--source live`, so cache-free callers (the FastAPI mock path and all tests) are unaffected.

**Tech Stack:** Python 3, stdlib only (`json`, `os`, `tempfile`, `time`, `hashlib`, `pathlib`), pytest.

**Spec:** `docs/superpowers/specs/2026-06-16-gdelt-news-cache-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `data_sources/cache.py` (new) | Generic `TTLCache` JSON-file store |
| `tests/test_cache.py` (new) | `TTLCache` unit tests |
| `data_sources/gdelt.py` (modify) | `NEWS_CACHE_TTL_SECONDS`, `terms_version`, `cache_key`, `cache` param on `load_news_pressure` |
| `tests/test_gdelt.py` (modify) | cache helper + cache-behaviour tests |
| `signal_engine.py` (modify) | thread `cache` through overlay + generate; `default_news_cache`; CLI wiring |
| `tests/test_signal_engine.py` (modify) | cache-threading tests |
| `.gitignore` (modify) | ignore `.cache/` |
| `README.md` (modify) | document the on-disk cache |

---

## Task 1: `TTLCache` generic on-disk cache

**Files:**
- Create: `data_sources/cache.py`
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cache.py`:

```python
import json

from data_sources.cache import TTLCache


def _clock(value):
    box = {"t": value}
    return box, (lambda: box["t"])


def test_get_returns_none_on_miss(tmp_path):
    cache = TTLCache(tmp_path / "c.json", ttl_seconds=100)
    assert cache.get("absent") is None


def test_set_then_get_returns_value(tmp_path):
    cache = TTLCache(tmp_path / "c.json", ttl_seconds=100)
    cache.set("k", {"a": 1})
    assert cache.get("k") == {"a": 1}


def test_entry_expires_at_ttl_boundary(tmp_path):
    box, now = _clock(1000.0)
    cache = TTLCache(tmp_path / "c.json", ttl_seconds=100, now=now)
    cache.set("k", "v")
    box["t"] = 1099.0
    assert cache.get("k") == "v"      # still inside TTL
    box["t"] = 1100.0
    assert cache.get("k") is None     # now - stored_at >= ttl -> expired


def test_corrupt_file_reads_as_empty(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json", encoding="utf-8")
    cache = TTLCache(path, ttl_seconds=100)
    assert cache.get("k") is None


def test_persists_across_instances(tmp_path):
    path = tmp_path / "c.json"
    TTLCache(path, ttl_seconds=100).set("k", [1, "x"])
    reopened = TTLCache(path, ttl_seconds=100)
    assert reopened.get("k") == [1, "x"]


def test_set_writes_valid_json(tmp_path):
    path = tmp_path / "c.json"
    cache = TTLCache(path, ttl_seconds=100)
    cache.set("k", "v")
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["k"]["value"] == "v"
    assert "stored_at" in on_disk["k"]


def test_creates_parent_directory(tmp_path):
    path = tmp_path / "nested" / "dir" / "c.json"
    cache = TTLCache(path, ttl_seconds=100)
    cache.set("k", "v")
    assert path.exists()


def test_exposes_path_and_ttl(tmp_path):
    path = tmp_path / "c.json"
    cache = TTLCache(path, ttl_seconds=42)
    assert cache.path == path
    assert cache.ttl_seconds == 42
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'data_sources.cache'`

- [ ] **Step 3: Write the implementation**

Create `data_sources/cache.py`:

```python
"""Generic TTL cache backed by a single JSON file.

Stores ``{key: {"value": <json>, "stored_at": <epoch>}}``. A missing or corrupt
file reads as an empty cache, and a write failure is swallowed so a caching
problem can never break the caller. The clock is injectable for testing.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable


class TTLCache:
    def __init__(
        self,
        path: str | os.PathLike[str],
        ttl_seconds: float,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._path = Path(path)
        self._ttl = float(ttl_seconds)
        self._now = now
        self._store = self._load()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def _load(self) -> dict[str, Any]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if not isinstance(entry, dict):
            return None
        stored_at = entry.get("stored_at")
        if not isinstance(stored_at, (int, float)):
            return None
        if self._now() - stored_at >= self._ttl:
            return None
        return entry.get("value")

    def set(self, key: str, value: Any) -> None:
        self._store[key] = {"value": value, "stored_at": self._now()}
        self._flush()

    def _flush(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self._store, fh)
                os.replace(tmp, self._path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except Exception:
            pass  # a cache write must never break the caller
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cache.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add data_sources/cache.py tests/test_cache.py
git commit -m "feat(cache): generic TTLCache JSON-file store with injectable clock"
```

---

## Task 2: GDELT cache-key helpers

**Files:**
- Modify: `data_sources/gdelt.py`
- Test: `tests/test_gdelt.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gdelt.py`:

```python
def test_terms_version_is_stable_8_char_hex():
    version = gdelt.terms_version()
    assert version == gdelt.terms_version()
    assert len(version) == 8
    int(version, 16)  # hex-parseable


def test_terms_version_changes_when_terms_change(monkeypatch):
    before = gdelt.terms_version()
    monkeypatch.setattr(gdelt, "STRESS_TERMS", gdelt.STRESS_TERMS + ("newterm",))
    assert gdelt.terms_version() != before


def test_cache_key_includes_economy_lookback_and_terms():
    key = gdelt.cache_key("Brazil")
    assert key.startswith("Brazil|")
    assert gdelt.LOOKBACK in key
    assert key.endswith(gdelt.terms_version())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_gdelt.py -k "terms_version or cache_key" -v`
Expected: FAIL with `AttributeError: module 'data_sources.gdelt' has no attribute 'terms_version'`

- [ ] **Step 3: Write the implementation**

In `data_sources/gdelt.py`, add `import hashlib` to the import block (alongside the existing `from math import sqrt`):

```python
import hashlib
```

Add a constant next to `MAX_RECORDS` near the top:

```python
NEWS_CACHE_TTL_SECONDS = 21600  # 6 hours
```

Add these two functions (place them just above `def article_count`). `terms_version` reads the module-level term tuples at call time so monkeypatching them in tests changes the result:

```python
def terms_version() -> str:
    """Short stable hash of the query term lists.

    Folded into the cache key so changing STRESS_TERMS / RELIEF_TERMS
    invalidates affected entries instead of reusing a stale score.
    """
    payload = "|".join((*STRESS_TERMS, "::", *RELIEF_TERMS)).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:8]


def cache_key(economy: str) -> str:
    return f"{economy}|{LOOKBACK}|{terms_version()}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_gdelt.py -k "terms_version or cache_key" -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add data_sources/gdelt.py tests/test_gdelt.py
git commit -m "feat(gdelt): cache-key helpers (terms_version, cache_key) and TTL constant"
```

---

## Task 3: Cache-aware `load_news_pressure`

**Files:**
- Modify: `data_sources/gdelt.py:110-126` (the `load_news_pressure` function)
- Test: `tests/test_gdelt.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gdelt.py` (note the imports needed at the top of the file — add `import pytest` only if not already present; `from data_sources.cache import TTLCache` is new):

```python
from data_sources.cache import TTLCache


def _fake_date(monkeypatch):
    real_date = gdelt.date

    class FakeDate:
        @staticmethod
        def today():
            return real_date(2026, 6, 16)

    monkeypatch.setattr(gdelt, "date", FakeDate)


def _counting_fetch(calls):
    def fetch(url):
        calls["n"] += 1
        query = parse_qs(urlparse(url).query)["query"][0]
        if "policy uncertainty" in query:
            return {"articles": [{}, {}, {}, {}]}
        return {"articles": [{}]}

    return fetch


def test_load_news_pressure_serves_from_cache_within_ttl(monkeypatch, tmp_path):
    _fake_date(monkeypatch)
    calls = {"n": 0}
    fetch = _counting_fetch(calls)
    clock = {"t": 1000.0}
    cache = TTLCache(tmp_path / "news.json", ttl_seconds=100, now=lambda: clock["t"])

    first = gdelt.load_news_pressure(("Canada",), fetch_json=fetch, cache=cache)
    assert first["Canada"] == (1.3416, "2026-06-16")
    assert calls["n"] == 2  # stress + relief

    second = gdelt.load_news_pressure(("Canada",), fetch_json=fetch, cache=cache)
    assert second["Canada"] == (1.3416, "2026-06-16")
    assert calls["n"] == 2  # served from cache, no new requests


def test_load_news_pressure_refetches_after_ttl(monkeypatch, tmp_path):
    _fake_date(monkeypatch)
    calls = {"n": 0}
    fetch = _counting_fetch(calls)
    clock = {"t": 1000.0}
    cache = TTLCache(tmp_path / "news.json", ttl_seconds=100, now=lambda: clock["t"])

    gdelt.load_news_pressure(("Canada",), fetch_json=fetch, cache=cache)
    assert calls["n"] == 2
    clock["t"] = 1200.0  # past the 100s TTL
    gdelt.load_news_pressure(("Canada",), fetch_json=fetch, cache=cache)
    assert calls["n"] == 4  # expired -> refetched


def test_load_news_pressure_does_not_cache_failures(monkeypatch, tmp_path):
    _fake_date(monkeypatch)

    def failing_fetch(url):
        raise RuntimeError("boom")

    cache = TTLCache(tmp_path / "news.json", ttl_seconds=1000, now=lambda: 0.0)
    out = gdelt.load_news_pressure(("Canada",), fetch_json=failing_fetch, cache=cache)
    assert "Canada" not in out
    assert cache.get(gdelt.cache_key("Canada")) is None


def test_load_news_pressure_misses_when_terms_change(monkeypatch, tmp_path):
    _fake_date(monkeypatch)
    calls = {"n": 0}
    fetch = _counting_fetch(calls)
    cache = TTLCache(tmp_path / "news.json", ttl_seconds=1000, now=lambda: 0.0)

    gdelt.load_news_pressure(("Canada",), fetch_json=fetch, cache=cache)
    assert calls["n"] == 2
    monkeypatch.setattr(gdelt, "STRESS_TERMS", gdelt.STRESS_TERMS + ("newterm",))
    gdelt.load_news_pressure(("Canada",), fetch_json=fetch, cache=cache)
    assert calls["n"] == 4  # new terms_version -> new key -> refetch
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_gdelt.py -k "cache or ttl or terms_change or failures" -v`
Expected: FAIL — `load_news_pressure` does not yet accept a `cache` keyword (`TypeError: load_news_pressure() got an unexpected keyword argument 'cache'`)

- [ ] **Step 3: Write the implementation**

Replace the body of `load_news_pressure` in `data_sources/gdelt.py` (currently lines ~110-126). New version:

```python
def load_news_pressure(
    economies: tuple[str, ...],
    fetch_json: Callable[[str], dict] = _default_fetch_json,
    cache: "TTLCache | None" = None,
) -> dict[str, tuple[float, str]]:
    """Return {economy: (news_pressure, as_of_date)} for mapped economies.

    When a ``cache`` is supplied, each economy's score is served from it if a
    non-expired entry exists; only successful fetches are written back, so a
    failed economy is retried on the next call.
    """
    out: dict[str, tuple[float, str]] = {}
    mapped = [economy for economy in economies if economy in ECONOMY_QUERY]

    to_fetch: list[str] = []
    for economy in mapped:
        if cache is not None:
            hit = cache.get(cache_key(economy))
            if hit is not None:
                score, asof = hit  # cached JSON list -> tuple
                out[economy] = (float(score), str(asof))
                continue
        to_fetch.append(economy)

    if to_fetch:
        with ThreadPoolExecutor(max_workers=min(6, len(to_fetch))) as executor:
            futures = {
                executor.submit(_load_one_economy, economy, fetch_json): economy
                for economy in to_fetch
            }
            for future in as_completed(futures):
                economy, result = future.result()
                if result is not None:
                    out[economy] = result
                    if cache is not None:
                        cache.set(cache_key(economy), list(result))
    return out
```

Add the type-only import at the top of `data_sources/gdelt.py` so the annotation resolves without a runtime import cycle (place under the existing `from typing import Callable` line):

```python
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from data_sources.cache import TTLCache
```

(Replace the existing `from typing import Callable` import with the two lines above.)

Note: cache writes happen in the main thread inside the `as_completed` loop (not inside the worker), so no concurrent writes to the cache file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_gdelt.py -v`
Expected: PASS (all gdelt tests, including the original `test_load_news_pressure_returns_score_and_date` which passes `cache=None` implicitly)

- [ ] **Step 5: Commit**

```bash
git add data_sources/gdelt.py tests/test_gdelt.py
git commit -m "feat(gdelt): serve news pressure from optional per-economy TTL cache"
```

---

## Task 4: Wire the cache into the engine and CLI

**Files:**
- Modify: `signal_engine.py` (imports, constants, `overlay_news_pressure`, `generate_snapshot`, `default_news_cache`, `__main__`)
- Modify: `.gitignore`
- Test: `tests/test_signal_engine.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_signal_engine.py` (it already imports `signal_engine` and `pandas as pd`; if not, add `import pandas as pd` and `import signal_engine`):

```python
from data_sources.cache import TTLCache


def test_overlay_news_pressure_threads_cache(monkeypatch):
    captured = {}

    def fake_load(economies, fetch_json=None, cache=None):
        captured["cache"] = cache
        return {}

    monkeypatch.setattr(signal_engine.gdelt, "load_news_pressure", fake_load)
    sentinel = object()
    df = pd.DataFrame({"news_pressure": [0.0]}, index=["United States of America"])
    signal_engine.overlay_news_pressure(
        df, {"United States of America": {}}, source="live", cache=sentinel
    )
    assert captured["cache"] is sentinel


def test_generate_snapshot_threads_news_cache(monkeypatch, tmp_path):
    captured = {}

    def spy(df, provenance, source="mock", fetch_json=None, cache=None):
        captured["cache"] = cache
        return df

    monkeypatch.setattr(signal_engine, "overlay_news_pressure", spy)
    sentinel = object()
    signal_engine.generate_snapshot(path=tmp_path / "snap.json", news_cache=sentinel)
    assert captured["cache"] is sentinel


def test_default_news_cache_uses_repo_path_and_ttl():
    cache = signal_engine.default_news_cache()
    assert isinstance(cache, TTLCache)
    assert cache.path == signal_engine.NEWS_CACHE_PATH
    assert cache.ttl_seconds == signal_engine.gdelt.NEWS_CACHE_TTL_SECONDS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_signal_engine.py -k "cache" -v`
Expected: FAIL — `overlay_news_pressure` has no `cache` kwarg / `generate_snapshot` has no `news_cache` kwarg / `default_news_cache` undefined.

- [ ] **Step 3: Write the implementation**

3a. In `signal_engine.py`, add the import under the existing `from data_sources import gdelt` line:

```python
from data_sources.cache import TTLCache
```

3b. Add a constant next to `SNAPSHOT_PATH = Path("snapshot.json")`:

```python
NEWS_CACHE_PATH = Path(".cache") / "gdelt_news.json"
```

3c. Replace the `overlay_news_pressure` signature and its `kwargs` construction. Current:

```python
def overlay_news_pressure(
    df: pd.DataFrame,
    provenance: dict[str, dict[str, str]],
    source: str = "mock",
    fetch_json=None,
) -> pd.DataFrame:
    """Overlay live GDELT news-pressure scores all-or-nothing across economies."""
    if source != "live":
        return df

    kwargs = {} if fetch_json is None else {"fetch_json": fetch_json}
    pressure = gdelt.load_news_pressure(tuple(df.index), **kwargs)
```

New:

```python
def overlay_news_pressure(
    df: pd.DataFrame,
    provenance: dict[str, dict[str, str]],
    source: str = "mock",
    fetch_json=None,
    cache: TTLCache | None = None,
) -> pd.DataFrame:
    """Overlay live GDELT news-pressure scores all-or-nothing across economies."""
    if source != "live":
        return df

    kwargs = {}
    if fetch_json is not None:
        kwargs["fetch_json"] = fetch_json
    if cache is not None:
        kwargs["cache"] = cache
    pressure = gdelt.load_news_pressure(tuple(df.index), **kwargs)
```

(Leave the rest of the function body — the `resolve_all_or_none` block and the loop writing `news_pressure` / provenance — unchanged.)

3d. Add a `news_cache` parameter to `generate_snapshot` and pass it through. Current signature + news line:

```python
def generate_snapshot(
    path: Path = SNAPSHOT_PATH,
    as_of: str | None = None,
    source: str = "mock",
    gdelt_fetch_json=None,
) -> dict:
    config = load_signal_config()
    df, provenance, expected_change_columns = load_macro_inputs(source=source)
    df = overlay_market_inputs(df, provenance, source=source)
    df = overlay_news_pressure(df, provenance, source=source, fetch_json=gdelt_fetch_json)
```

New:

```python
def generate_snapshot(
    path: Path = SNAPSHOT_PATH,
    as_of: str | None = None,
    source: str = "mock",
    gdelt_fetch_json=None,
    news_cache: TTLCache | None = None,
) -> dict:
    config = load_signal_config()
    df, provenance, expected_change_columns = load_macro_inputs(source=source)
    df = overlay_market_inputs(df, provenance, source=source)
    df = overlay_news_pressure(
        df, provenance, source=source, fetch_json=gdelt_fetch_json, cache=news_cache
    )
```

(Leave the remaining lines of `generate_snapshot` unchanged.)

3e. Add the factory just above the `if __name__ == "__main__":` block:

```python
def default_news_cache() -> TTLCache:
    """The on-disk GDELT cache used by the live CLI path."""
    return TTLCache(NEWS_CACHE_PATH, gdelt.NEWS_CACHE_TTL_SECONDS)
```

3f. Update the `__main__` block to build the cache for live only. Current:

```python
    args = parser.parse_args()
    generate_snapshot(source=args.source)
    print(f"Wrote {SNAPSHOT_PATH} (source={args.source})")
```

New:

```python
    args = parser.parse_args()
    news_cache = default_news_cache() if args.source == "live" else None
    generate_snapshot(source=args.source, news_cache=news_cache)
    print(f"Wrote {SNAPSHOT_PATH} (source={args.source})")
```

3g. Add `.cache/` to `.gitignore` (append a line):

```
.cache/
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_signal_engine.py -k "cache" -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add signal_engine.py tests/test_signal_engine.py .gitignore
git commit -m "feat(signal): thread optional GDELT cache through engine, build it for live CLI"
```

---

## Task 5: Document the cache

**Files:**
- Modify: `README.md:12` and `README.md:115`

- [ ] **Step 1: Extend the gdelt architecture bullet**

In `README.md`, the `data_sources/gdelt.py` bullet (line 12) currently ends with `...minus constructive/relief article flow`. Append:

```
; live results are cached on disk at `.cache/gdelt_news.json` (per-economy, 6-hour TTL, git-ignored) so repeated live runs within the window skip the GDELT requests
```

- [ ] **Step 2: Add a Current Limitations note**

Under the `## Current Limitations` section, add a new bullet after the GDELT/`news_pressure` line (line ~115):

```
- The GDELT news-pressure overlay caches scores per economy on disk (`.cache/gdelt_news.json`, 6-hour TTL). The cache stores only successful fetches, so a partially-failed run refetches just the missing economies next time; delete the file to force a full refresh.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document the on-disk GDELT news-pressure cache"
```

---

## Final verification

- [ ] **Run the full suite**

Run: `python -m pytest -q`
Expected: PASS — previous total (93) plus the new cache tests, all green.

- [ ] **Smoke-test the live CLI cache (optional, needs network)**

Run: `python signal_engine.py --source live && ls -la .cache/`
Expected: `.cache/gdelt_news.json` exists; a second immediate `python signal_engine.py --source live` reuses it (no GDELT latency for cached economies).
```
