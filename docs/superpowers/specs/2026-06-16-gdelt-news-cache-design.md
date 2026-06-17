# GDELT news-pressure cache — design

**Date:** 2026-06-16
**Status:** Approved (design); ready for implementation plan

## Problem

Every live snapshot generation (`python signal_engine.py --source live`) fires
12 GDELT DOC 2.0 requests (2 per economy × 6 economies) inside
`gdelt.load_news_pressure`. Each generation is a separate process, so the
existing in-process memoization pattern (see `history.py`) does **not** help
across runs. Repeated runs within a short window — common during development —
re-hit the GDELT API needlessly.

## Goal

Avoid repeated GDELT requests across separate generation runs by caching the
computed news-pressure scores on disk with a time-to-live (TTL). A run within
the TTL window reuses cached scores and makes **zero** GDELT requests for
already-cached economies.

## Non-goals (YAGNI)

- No LRU / size-bounded eviction.
- No pluggable cache backends (single JSON-file backend only).
- TTL is **not** surfaced in `signal_config.yaml` yet — a module constant is
  sufficient; extract to config only if a need appears.
- No change to the all-or-nothing semantics of the live news overlay.

## Decisions

- **Persistence:** on-disk, persists across runs (chosen over in-process-only,
  which cannot reduce requests across separate CLI invocations).
- **TTL:** 6 hours (`21600` seconds). Covers repeated manual reruns within a
  dev session; the daily CI run (interval > 6h) always refetches fresh data.
- **Cache granularity:** per-economy **score**, not per-URL raw payload. The
  semantic unit worth caching is "this economy's pressure score today" — 6
  small entries rather than 12 large article-list payloads.

## Architecture

### New module: `data_sources/cache.py`

A generic TTL cache backed by a single JSON file.

```python
class TTLCache:
    def __init__(self, path: str | Path, ttl_seconds: float,
                 now: Callable[[], float] = time.time) -> None: ...
    def get(self, key: str): ...   # value, or None if missing/expired
    def set(self, key: str, value) -> None: ...   # persist to disk
```

- **On-disk format:**
  `{ "<key>": {"value": <json-serializable>, "stored_at": <epoch_seconds>} }`
- **Load:** read the file on init; a missing or corrupt (non-JSON) file is
  treated as an empty cache — never raises.
- **`get`:** returns `None` if the key is absent or
  `now() - stored_at >= ttl_seconds`. Expired entries are simply ignored on
  read (and overwritten on the next `set`).
- **`set`:** updates the in-memory map and writes the whole file atomically
  (write to a temp file in the same directory, then `os.replace`). The parent
  directory is created if absent. A write failure is swallowed (logged at most)
  — the network fetch already succeeded, so a cache-write error must not fail
  generation.
- **Clock injection:** `now` is injectable so tests can simulate TTL expiry
  without sleeping.

### Wiring: `data_sources/gdelt.py`

`load_news_pressure` gains an optional `cache` parameter:

```python
def load_news_pressure(
    economies: tuple[str, ...],
    fetch_json: Callable[[str], dict] = _default_fetch_json,
    cache: TTLCache | None = None,
) -> dict[str, tuple[float, str]]: ...
```

For each mapped economy:
1. Compute `key = cache_key(economy)` (see below).
2. If `cache` is not None and `cache.get(key)` returns a hit, use it (no fetch).
   The cached value is a JSON list `[score, as_of]`; coerce it back to the
   `(float, str)` tuple the function returns.
3. On miss, run the existing fetch + `pressure_score` path
   (`_load_one_economy`), then `cache.set(key, [score, as_of])`.

Only successful fetches are cached; a per-economy failure still returns `None`
for that economy (unchanged) and is **not** cached, so it is retried next run.

**Cache key:**
`f"{economy}|{LOOKBACK}|{terms_version()}"` where `terms_version()` is a short
stable hash (e.g. first 8 hex chars of a sha1) of `STRESS_TERMS + RELIEF_TERMS`.
Changing the lookback window or the term lists therefore invalidates affected
keys automatically — a stale score is never reused against new query
definitions.

### Wiring: `signal_engine.py`

`overlay_news_pressure` constructs the cache and passes it through:

- Cache file path: `.cache/gdelt_news.json` (under the repo root).
- TTL: `NEWS_CACHE_TTL_SECONDS = 21600` (6h), defined in `gdelt.py`.
- The injectable `fetch_json` seam is preserved; when the engine is called from
  tests with a fake `fetch_json`, the cache is either disabled (`cache=None`)
  or pointed at a temp path so tests stay offline and deterministic.

### `.gitignore`

Add `.cache/` so the cache file is never committed.

## Interaction with all-or-nothing overlay

`overlay_news_pressure` still applies `resolve_all_or_none` **after**
`load_news_pressure` returns. Behaviour is unchanged: if any economy lacks a
live score, the whole overlay falls back to mock for that run. Because the cache
stores per-economy successes, a subsequent run only needs to refetch the
previously-failed economy — so over successive runs the cache mitigates the
fragility of the all-or-nothing gate without altering its semantics.

## Error handling summary

| Failure | Behaviour |
|---|---|
| Cache file missing | Treat as empty cache |
| Cache file corrupt / non-JSON | Treat as empty cache, do not raise |
| Cache entry expired | `get` returns `None`, entry refetched and overwritten |
| Cache write fails | Swallow; generation continues (network already succeeded) |
| Per-economy GDELT fetch fails | Return `None` for that economy (unchanged); not cached |

## Testing

**`tests/test_cache.py` (new) — `TTLCache` unit tests:**
- miss returns `None`
- `set` then `get` returns the stored value
- entry past TTL returns `None` (fake clock advanced beyond `ttl_seconds`)
- entry exactly at boundary: `>= ttl` is expired (boundary asserted)
- corrupt file is tolerated (write garbage, construct cache, `get` → `None`)
- persistence across instances: `set` on one instance, new instance `get` hits
- atomic write leaves valid JSON (no partial file on normal completion)

**`tests/test_gdelt.py` (extend) — cache behaviour:**
- with a counting fake `fetch_json` and a temp-path cache: first
  `load_news_pressure` makes N network calls; a second call within TTL makes
  **0**; after advancing the injected clock past TTL, it refetches.
- changing the term lists (or a different `terms_version`) produces a different
  key → cache miss → refetch.
- a per-economy fetch failure is not cached (next call retries it).

**Existing suite:** must stay green and offline. Engine-level tests continue to
inject a fake `fetch_json` and avoid real network; the cache is disabled or
temp-pathed in those tests.

## Files touched

| File | Change |
|---|---|
| `data_sources/cache.py` | new — `TTLCache` |
| `data_sources/gdelt.py` | add `cache` param + `cache_key`/`terms_version`, `NEWS_CACHE_TTL_SECONDS` |
| `signal_engine.py` | construct cache in `overlay_news_pressure` |
| `.gitignore` | add `.cache/` |
| `tests/test_cache.py` | new |
| `tests/test_gdelt.py` | extend with cache tests |
| `README.md` | note the on-disk GDELT cache + TTL under live mode |
