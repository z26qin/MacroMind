---
name: add-data-source
description: Use when adding a new live data adapter to MacroMind — triggers like "add a new data source", "wire in a live feed for X", "add an adapter for FRED / policy rates / PMI". Scaffolds data_sources/<name>.py, wires the live overlay and provenance in signal_engine.py, adds a no-network test, and updates the README.
---

# Add Data Source

Scaffold and wire a new live data adapter for MacroMind, following the four existing
ones (`world_bank`, `imf_weo`, `market`, `gdelt`). The repo's existing adapters are
the canonical template — mirror the closest one rather than inventing a new shape.

## Checklist

- [ ] Read the closest existing adapter (default exemplar: `data_sources/gdelt.py`)
      and `data_sources/http.py` for the fetch contract.
- [ ] Create `data_sources/<name>.py`:
  - module docstring naming the source and stating the explainable formula/level;
  - `from data_sources.http import fetch_json as http_fetch_json`;
  - a `_default_fetch_json(url)` wrapper that sets timeout/retries;
  - a public `load_<x>(economies, fetch_json=_default_fetch_json)` returning
    `dict[str, tuple[float, str]]` — i.e. `{economy: (value, as_of_date)}` — with an
    **injectable `fetch_json`** (the test seam), mirroring `gdelt.load_news_pressure`.
- [ ] Wire into `signal_engine.py`:
  - `from data_sources import <name>`;
  - an `overlay_<x>(df, provenance, source="mock", fetch_json=None)` mirroring
    `overlay_news_pressure` (~line 395): mock mode is a no-op; live mode resolves
    values via `resolve_all_or_none`, records provenance as `f"<source>:<asof>"`, and
    mutates `df`;
  - call it inside `generate_snapshot` alongside the other `overlay_*` calls;
  - **all-or-nothing:** `resolve_all_or_none` returns `None` (keep the mock column)
    unless *every* economy resolves a value;
  - if it is a new input column, add it to the matching `REQUIRED_*_COLUMNS` set
    (`REQUIRED_MACRO_COLUMNS` / `REQUIRED_CONSENSUS_COLUMNS` / `REQUIRED_MARKET_COLUMNS`
    / `REQUIRED_NEWS_COLUMNS`); `INPUT_PROVENANCE_COLUMNS` derives from their union, so
    provenance tracking propagates automatically.
- [ ] Add `tests/test_<name>.py` mirroring `tests/test_gdelt.py`: pass a `fake_fetch`
      callable as the `fetch_json` seam (**no network**); use `monkeypatch` only to stub
      module-level state such as `date.today()` if the loader stamps a date. Cover
      URL/query building, payload parsing, the level/score computation, and the loader.
- [ ] Update `README.md`: the Architecture bullet, Signal Methodology (if it is a new
      input), Current Limitations (what is now live vs still mock), and TODO / Future
      Data Sources.
- [ ] Run `pytest`. Hand off test discipline to tdd-workflow / verification-loop, and
      network-code review to security-review.

## Notes

- Never call the network in tests — always inject `fetch_json`.
- Loader return shape is `{economy: (value, as_of_date)}` (`dict[str, tuple[float, str]]`);
  the overlay records provenance per cell as `"<source>:<asof>"`.
- `gdelt` threads an optional `cache: TTLCache | None = None` param (its on-disk news
  cache) through `load_news_pressure` / `overlay_news_pressure`. That is source-specific
  — a new adapter can omit it unless the feed is slow or rate-limited.
