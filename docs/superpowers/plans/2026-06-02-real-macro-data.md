# Real Macro Data (World Bank) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mocked macro inputs (inflation, GDP growth, unemployment) with live data from the free, key-free World Bank API, while keeping the engine deterministic, offline-testable, and able to fall back to mock data per-value.

**Architecture:** Add a new `data_sources/world_bank.py` module that fetches annual macro indicators for the six-economy universe, selects the latest non-null observation per series, and derives a naive "consensus" baseline from each series' recent history (so surprises remain meaningful even though analyst-consensus data stays mocked). `signal_engine.py` gains a `source` switch (`"mock"` default, `"live"` opt-in) that overlays live values onto the existing mock frame and records per-value provenance in the snapshot. All HTTP access goes through an injectable `fetch_json` callable so tests run with no network and the suite stays deterministic.

**Tech Stack:** Python 3.11, FastAPI, pandas, numpy, PyYAML, pytest, **httpx** (new — HTTP client + future FastAPI TestClient).

---

## Why this stage (context for the engineer)

The current app is a runnable prototype: `signal_engine.py` reads three mock CSVs (`data/mock_*.csv`), computes cross-sectional ranked signals, blends in a hardcoded RAG stub, and writes `snapshot.json`. The README's top limitation is "Mock data only." This plan makes the **macro** domain real using only the World Bank public API (no API key, no paid vendor).

**Constraints locked in with the user:**
- Live domain: **macro only** (market, real estate, consensus stay mocked this stage).
- Sources: **free, zero credentials** → World Bank API (FRED is excluded because it needs a key).
- RAG: **keep the stub** unchanged.
- History/persistence: **single live snapshot** (no time-series DB this stage).

**Three facts verified against the live API (do not re-derive — build to these):**
1. Response shape is a 2-element JSON array: `[metadata_obj, [observation_objs]]`. Each observation has `countryiso3code`, `date` (string year), `value` (float or `null`), `indicator.id`.
2. The Euro Area aggregate uses World Bank code **`EMU`** (not `EUR`). All six economies resolve: `USA, CAN, CHN, JPN, BRA, EMU`.
3. Coverage is uneven: the latest year is often `null`, and **Euro-area CPI (`FP.CPI.TOTL.ZG`) is entirely `null`**. The adapter MUST skip nulls, pick the latest available year, and fall back to the mock value when a whole series is empty.

**Indicator mapping (engine column → World Bank indicator code):**
| Engine column | World Bank code | Live? |
|---|---|---|
| `inflation_yoy` | `FP.CPI.TOTL.ZG` | yes |
| `gdp_growth` | `NY.GDP.MKTP.KD.ZG` | yes |
| `unemployment` | `SL.UEM.TOTL.ZS` | yes |
| `policy_rate` | — (no key-free source) | no → mock |
| `pmi` | — (proprietary) | no → mock |

---

## File Structure

**Create:**
- `data_sources/__init__.py` — package marker (empty).
- `data_sources/world_bank.py` — fetch, parse, latest-non-null selection, naive consensus baseline, assembler. One responsibility: turn World Bank JSON into engine-shaped macro/consensus/provenance dicts.
- `tests/test_world_bank.py` — hermetic unit tests using an injected fake `fetch_json` (no network).

**Modify:**
- `requirements.txt` — add `httpx`.
- `.gitignore` — new file; stop tracking `.venv/`, `__pycache__/`.
- `signal_engine.py` — add `load_macro_inputs(source=...)`, overlay + provenance, World Bank code map import; thread `source` through `generate_snapshot`; add `argparse` to `__main__`.
- `tests/test_signal_engine.py` — update top-level schema test for the new `data_source` key; add provenance assertions.
- `signal_config.yaml` — add a `data_source` block.
- `.github/workflows/refresh.yml` — run the daily refresh with `--source live`.
- `README.md` — update Architecture, Limitations, Run sections.

**Naming contract (used across tasks — keep identical):**
- Module constants: `WB_BASE`, `WB_CODE_BY_ECONOMY`, `WB_INDICATOR_BY_COLUMN`, `LIVE_COLUMNS`.
- Functions: `_default_fetch_json(url)`, `fetch_indicator(column, start_year, end_year, fetch_json=...)`, `latest_and_baseline(history, baseline_window=3)`, `load_world_bank_macro(economies, start_year, end_year, baseline_window=3, fetch_json=...)`.
- Engine function: `load_macro_inputs(source="mock", data_dir=DATA_DIR, fetch_json=None)` returning `(df, provenance)`.
- Snapshot additions: top-level key `"data_source"` (string); per-economy key `"provenance"` (dict of `column -> str`). Provenance values are `"world_bank:<year>"` for live, `"mock"` otherwise.

---

## Task 1: Project prep — add httpx and .gitignore

**Files:**
- Modify: `requirements.txt`
- Create: `.gitignore`

- [ ] **Step 1: Add httpx to requirements**

Edit `requirements.txt` to read exactly:

```text
fastapi
uvicorn
pandas
numpy
PyYAML
pytest
httpx
```

- [ ] **Step 2: Create `.gitignore`**

Create `.gitignore` with:

```gitignore
__pycache__/
*.pyc
.venv/
venv/
.pytest_cache/
.DS_Store
```

- [ ] **Step 3: Install the new dependency**

Run: `python -m pip install -r requirements.txt`
Expected: `httpx` installs successfully (others already satisfied).

- [ ] **Step 4: Verify the existing suite still passes**

Run: `python -m pytest -q`
Expected: `8 passed`.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt .gitignore
git commit -m "chore: add httpx dependency and .gitignore"
```

---

## Task 2: World Bank fetch + parse (`fetch_indicator`)

**Files:**
- Create: `data_sources/__init__.py`
- Create: `data_sources/world_bank.py`
- Test: `tests/test_world_bank.py`

- [ ] **Step 1: Create the package marker**

Create `data_sources/__init__.py` as an empty file (0 bytes).

- [ ] **Step 2: Write the failing test**

Create `tests/test_world_bank.py`:

```python
from data_sources import world_bank as wb


def _payload(rows):
    """Build a World Bank-shaped [metadata, observations] response."""
    return [{"page": 1, "pages": 1, "per_page": 20000, "total": len(rows)}, rows]


def _obs(iso3, year, value):
    return {
        "indicator": {"id": "X", "value": "X"},
        "countryiso3code": iso3,
        "date": str(year),
        "value": value,
    }


def test_fetch_indicator_groups_by_economy_and_drops_nulls():
    rows = [
        _obs("USA", 2024, 2.9),
        _obs("USA", 2023, 4.1),
        _obs("USA", 2025, None),   # null dropped
        _obs("EMU", 2024, None),   # whole-series null -> empty list
    ]
    captured = {}

    def fake_fetch(url):
        captured["url"] = url
        return _payload(rows)

    series = wb.fetch_indicator(
        "inflation_yoy", 2018, 2026, fetch_json=fake_fetch
    )

    assert series["United States of America"] == [(2024, 2.9), (2023, 4.1)]
    assert series["Euro Area"] == []
    assert series["Canada"] == []  # economy with no rows still present
    assert "FP.CPI.TOTL.ZG" in captured["url"]
    assert "date=2018:2026" in captured["url"]


def test_fetch_indicator_raises_on_error_payload():
    def fake_fetch(url):
        return [{"message": [{"id": "120", "value": "bad code"}]}]

    try:
        wb.fetch_indicator("gdp_growth", 2018, 2026, fetch_json=fake_fetch)
        assert False, "expected ValueError"
    except ValueError:
        pass
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_world_bank.py -v`
Expected: FAIL with `ModuleNotFoundError`/`AttributeError` (module/function not defined).

- [ ] **Step 4: Write minimal implementation**

Create `data_sources/world_bank.py`:

```python
"""World Bank macro data source (free, no API key).

Fetches annual macro indicators for the signal universe from the public
World Bank API and shapes them to the engine's mock CSV schema, with
per-value provenance. All HTTP access goes through an injectable
``fetch_json`` callable so callers (and tests) can run without network.
"""
from __future__ import annotations

from typing import Callable

import httpx

WB_BASE = "https://api.worldbank.org/v2"

# Engine economy name -> World Bank country code. Euro Area is "EMU".
WB_CODE_BY_ECONOMY = {
    "United States of America": "USA",
    "Canada": "CAN",
    "China": "CHN",
    "Japan": "JPN",
    "Brazil": "BRA",
    "Euro Area": "EMU",
}

# Engine macro column -> World Bank indicator code (live columns only).
WB_INDICATOR_BY_COLUMN = {
    "inflation_yoy": "FP.CPI.TOTL.ZG",
    "gdp_growth": "NY.GDP.MKTP.KD.ZG",
    "unemployment": "SL.UEM.TOTL.ZS",
}

LIVE_COLUMNS = tuple(WB_INDICATOR_BY_COLUMN)


def _default_fetch_json(url: str) -> list:
    response = httpx.get(url, timeout=20.0)
    response.raise_for_status()
    return response.json()


def fetch_indicator(
    column: str,
    start_year: int,
    end_year: int,
    fetch_json: Callable[[str], list] = _default_fetch_json,
) -> dict[str, list[tuple[int, float]]]:
    """Return {economy: [(year, value), ...]} sorted newest-first, nulls dropped."""
    indicator = WB_INDICATOR_BY_COLUMN[column]
    codes = ";".join(WB_CODE_BY_ECONOMY.values())
    url = (
        f"{WB_BASE}/country/{codes}/indicator/{indicator}"
        f"?format=json&per_page=20000&date={start_year}:{end_year}"
    )
    payload = fetch_json(url)
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
        raise ValueError(f"Unexpected World Bank response for {indicator}: {payload!r:.200}")

    economy_by_code = {code: economy for economy, code in WB_CODE_BY_ECONOMY.items()}
    series: dict[str, list[tuple[int, float]]] = {economy: [] for economy in WB_CODE_BY_ECONOMY}
    for obs in payload[1]:
        economy = economy_by_code.get(obs.get("countryiso3code"))
        value = obs.get("value")
        if economy is None or value is None:
            continue
        series[economy].append((int(obs["date"]), float(value)))

    for economy in series:
        series[economy].sort(key=lambda pair: pair[0], reverse=True)
    return series
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_world_bank.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add data_sources/__init__.py data_sources/world_bank.py tests/test_world_bank.py
git commit -m "feat: add World Bank indicator fetch and parse"
```

---

## Task 3: Naive consensus baseline (`latest_and_baseline`)

**Files:**
- Modify: `data_sources/world_bank.py`
- Test: `tests/test_world_bank.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_world_bank.py`:

```python
def test_latest_and_baseline_uses_latest_actual_and_prior_mean():
    history = [(2024, 2.9), (2023, 4.1), (2022, 8.0), (2021, 4.7)]
    actual, consensus, year = wb.latest_and_baseline(history, baseline_window=3)
    assert actual == 2.9
    assert year == 2024
    assert consensus == (4.1 + 8.0 + 4.7) / 3


def test_latest_and_baseline_single_point_consensus_equals_actual():
    actual, consensus, year = wb.latest_and_baseline([(2024, 2.9)])
    assert actual == 2.9
    assert consensus == 2.9
    assert year == 2024


def test_latest_and_baseline_empty_returns_none():
    assert wb.latest_and_baseline([]) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_world_bank.py -k latest_and_baseline -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'latest_and_baseline'`.

- [ ] **Step 3: Write minimal implementation**

Add to `data_sources/world_bank.py` (after `fetch_indicator`):

```python
def latest_and_baseline(
    history: list[tuple[int, float]],
    baseline_window: int = 3,
) -> tuple[float, float, int] | None:
    """From newest-first (year, value) pairs return (actual, consensus, year).

    actual   = most recent observation
    consensus = mean of up to ``baseline_window`` prior observations (naive forecast);
                falls back to the actual value when no prior observations exist.
    Returns None when there is no data.
    """
    if not history:
        return None
    actual_year, actual = history[0]
    prior = [value for _, value in history[1 : 1 + baseline_window]]
    consensus = sum(prior) / len(prior) if prior else actual
    return actual, consensus, actual_year
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_world_bank.py -k latest_and_baseline -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add data_sources/world_bank.py tests/test_world_bank.py
git commit -m "feat: derive naive consensus baseline from indicator history"
```

---

## Task 4: Assembler (`load_world_bank_macro`)

**Files:**
- Modify: `data_sources/world_bank.py`
- Test: `tests/test_world_bank.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_world_bank.py`:

```python
def test_load_world_bank_macro_assembles_values_and_provenance():
    # One fake response per live column, keyed by indicator code in the URL.
    responses = {
        "FP.CPI.TOTL.ZG": _payload([
            _obs("USA", 2024, 2.9), _obs("USA", 2023, 4.1),
            # EMU CPI entirely null -> missing for Euro Area
            _obs("EMU", 2024, None),
        ]),
        "NY.GDP.MKTP.KD.ZG": _payload([
            _obs("USA", 2023, 2.88), _obs("USA", 2022, 1.9),
        ]),
        "SL.UEM.TOTL.ZS": _payload([
            _obs("USA", 2025, 4.2), _obs("USA", 2024, 4.0),
        ]),
    }

    def fake_fetch(url):
        for code, payload in responses.items():
            if code in url:
                return payload
        raise AssertionError(f"unexpected url {url}")

    macro, consensus, provenance = wb.load_world_bank_macro(
        ("United States of America", "Euro Area"),
        2018, 2026, baseline_window=3, fetch_json=fake_fetch,
    )

    assert macro["United States of America"]["inflation_yoy"] == 2.9
    assert provenance["United States of America"]["inflation_yoy"] == "world_bank:2024"
    assert provenance["United States of America"]["unemployment"] == "world_bank:2025"
    # Euro Area CPI was all-null -> no live value, no provenance entry
    assert "inflation_yoy" not in macro["Euro Area"]
    assert "inflation_yoy" not in provenance["Euro Area"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_world_bank.py -k load_world_bank_macro -v`
Expected: FAIL with `AttributeError: ... 'load_world_bank_macro'`.

- [ ] **Step 3: Write minimal implementation**

Add to `data_sources/world_bank.py`:

```python
def load_world_bank_macro(
    economies: tuple[str, ...],
    start_year: int,
    end_year: int,
    baseline_window: int = 3,
    fetch_json: Callable[[str], list] = _default_fetch_json,
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]], dict[str, dict[str, str]]]:
    """Return (macro, consensus, provenance) for the LIVE columns only.

    Each dict is keyed by economy then column. Economies/columns with no
    available observation are simply absent (the caller falls back to mock).
    """
    macro: dict[str, dict[str, float]] = {economy: {} for economy in economies}
    consensus: dict[str, dict[str, float]] = {economy: {} for economy in economies}
    provenance: dict[str, dict[str, str]] = {economy: {} for economy in economies}

    for column in LIVE_COLUMNS:
        series = fetch_indicator(column, start_year, end_year, fetch_json=fetch_json)
        for economy in economies:
            result = latest_and_baseline(series.get(economy, []), baseline_window)
            if result is None:
                continue
            actual, baseline, year = result
            macro[economy][column] = actual
            consensus[economy][column] = baseline
            provenance[economy][column] = f"world_bank:{year}"

    return macro, consensus, provenance
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_world_bank.py -k load_world_bank_macro -v`
Expected: PASS.

- [ ] **Step 5: Run the whole file**

Run: `python -m pytest tests/test_world_bank.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add data_sources/world_bank.py tests/test_world_bank.py
git commit -m "feat: assemble World Bank macro/consensus/provenance dicts"
```

---

## Task 5: Engine integration — `load_macro_inputs(source=...)` with mock fallback

**Files:**
- Modify: `signal_engine.py` (rename/extend the loader around lines 142-166; add provenance)
- Test: `tests/test_signal_engine.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_signal_engine.py`:

```python
from data_sources import world_bank as wb
import signal_engine as se


def test_load_macro_inputs_mock_marks_all_provenance_mock():
    df, provenance = se.load_macro_inputs(source="mock")
    assert len(df) == 6
    for economy in EXPECTED_UNIVERSE:
        assert provenance[economy]["inflation_yoy"] == "mock"
        assert provenance[economy]["policy_rate"] == "mock"


def test_load_macro_inputs_live_overlays_world_bank_values():
    def fake_fetch(url):
        # Return one USA observation for whichever indicator is requested.
        for code in wb.WB_INDICATOR_BY_COLUMN.values():
            if code in url:
                return [
                    {"page": 1, "pages": 1, "per_page": 20000, "total": 2},
                    [
                        {"countryiso3code": "USA", "date": "2024", "value": 9.99},
                        {"countryiso3code": "USA", "date": "2023", "value": 1.11},
                    ],
                ]
        raise AssertionError(url)

    df, provenance = se.load_macro_inputs(source="live", fetch_json=fake_fetch)
    # USA live columns overlaid with the fake actual (9.99)
    assert df.loc["United States of America", "inflation_yoy"] == 9.99
    assert provenance["United States of America"]["inflation_yoy"] == "world_bank:2024"
    # Non-live columns stay mock
    assert provenance["United States of America"]["pmi"] == "mock"
    # Economy with no live rows (e.g. Japan) falls back to mock for everything
    assert provenance["Japan"]["inflation_yoy"] == "mock"
    # Frame is still complete (no NaNs) so downstream validation holds
    assert not df.isna().any().any()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_signal_engine.py -k load_macro_inputs -v`
Expected: FAIL with `AttributeError: module 'signal_engine' has no attribute 'load_macro_inputs'`.

- [ ] **Step 3: Write minimal implementation**

In `signal_engine.py`, add the import near the top (after `from rag_signal import compute_rag_signal`):

```python
from data_sources import world_bank as wb
```

Then add a `LIVE_HISTORY_YEARS` constant near the other constants (after `METHODOLOGY_VERSION`):

```python
LIVE_HISTORY_YEARS = 8
```

Then add this function immediately AFTER the existing `load_mock_data` function (keep `load_mock_data` unchanged):

```python
def load_macro_inputs(
    source: str = "mock",
    data_dir: Path = DATA_DIR,
    fetch_json=None,
) -> tuple[pd.DataFrame, dict[str, dict[str, str]]]:
    """Return (joined input frame, provenance).

    source="mock": the existing mock CSVs, every value tagged "mock".
    source="live": overlay World Bank values onto the mock frame for the
    live macro columns where available; everything else stays mock.
    """
    df = load_mock_data(data_dir)

    tracked_columns = sorted(REQUIRED_MACRO_COLUMNS - {"economy"})
    provenance = {
        economy: {column: "mock" for column in tracked_columns}
        for economy in df.index
    }

    if source == "mock":
        return df, provenance
    if source != "live":
        raise ValueError(f"Unknown data source: {source!r} (expected 'mock' or 'live')")

    from datetime import date

    end_year = date.today().year
    start_year = end_year - LIVE_HISTORY_YEARS
    kwargs = {} if fetch_json is None else {"fetch_json": fetch_json}
    macro, consensus, live_provenance = wb.load_world_bank_macro(
        tuple(df.index), start_year, end_year, **kwargs
    )

    consensus_column = {
        "inflation_yoy": "inflation_consensus",
        "gdp_growth": "gdp_consensus",
        "unemployment": "unemployment_consensus",
    }
    for economy in df.index:
        for column, value in macro[economy].items():
            df.loc[economy, column] = value
            df.loc[economy, consensus_column[column]] = consensus[economy][column]
            provenance[economy][column] = live_provenance[economy][column]

    if df.isna().any().any():
        raise ValueError("Macro inputs contain missing values after live overlay")
    return df, provenance
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_signal_engine.py -k load_macro_inputs -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: all existing tests still PASS (the snapshot path still uses `load_mock_data` until Task 6).

- [ ] **Step 6: Commit**

```bash
git add signal_engine.py tests/test_signal_engine.py
git commit -m "feat: load_macro_inputs with live World Bank overlay and provenance"
```

---

## Task 6: Thread source + provenance into the snapshot

**Files:**
- Modify: `signal_engine.py` (`build_snapshot` ~line 259, `generate_snapshot` ~line 318)
- Test: `tests/test_signal_engine.py` (update schema test ~line 21)

- [ ] **Step 1: Update the failing tests**

In `tests/test_signal_engine.py`, replace `test_snapshot_has_stable_top_level_schema` with:

```python
def test_snapshot_has_stable_top_level_schema(snapshot):
    assert set(snapshot) == {
        "as_of",
        "methodology_version",
        "data_source",
        "universe",
        "economies",
    }
    assert snapshot["as_of"] == "2026-06-02"
    assert snapshot["methodology_version"] == "v0.1"
    assert snapshot["data_source"] == "mock"
```

And append a provenance test:

```python
def test_each_economy_reports_provenance(snapshot):
    for economy in snapshot["economies"].values():
        provenance = economy["provenance"]
        assert provenance["inflation_yoy"] == "mock"
        assert set(provenance) >= {
            "inflation_yoy", "gdp_growth", "unemployment", "policy_rate", "pmi",
        }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_signal_engine.py -k "schema or provenance" -v`
Expected: FAIL — `data_source`/`provenance` keys missing.

- [ ] **Step 3: Update `build_snapshot` and `generate_snapshot`**

In `signal_engine.py`, change the `build_snapshot` signature and the snapshot dict header. Replace:

```python
def build_snapshot(df: pd.DataFrame, config: dict, as_of: str | None = None) -> dict:
    weights = config["weights"]
    blend = config["signal_blend"]
    deterministic_weight = float(blend["deterministic_weight"])
    rag_weight = float(blend["rag_weight"])

    snapshot = {
        "as_of": as_of or date.today().isoformat(),
        "methodology_version": METHODOLOGY_VERSION,
        "universe": list(UNIVERSE),
        "economies": {},
    }
```

with:

```python
def build_snapshot(
    df: pd.DataFrame,
    config: dict,
    provenance: dict[str, dict[str, str]],
    source: str = "mock",
    as_of: str | None = None,
) -> dict:
    weights = config["weights"]
    blend = config["signal_blend"]
    deterministic_weight = float(blend["deterministic_weight"])
    rag_weight = float(blend["rag_weight"])

    snapshot = {
        "as_of": as_of or date.today().isoformat(),
        "methodology_version": METHODOLOGY_VERSION,
        "data_source": source,
        "universe": list(UNIVERSE),
        "economies": {},
    }
```

Then, inside the `for country, row in df.iterrows():` loop, change the `entry` initialization. Replace:

```python
        entry = {
            "country": country,
            "iso3": row["iso3"],
            "signals": {},
            "composite": {},
        }
```

with:

```python
        entry = {
            "country": country,
            "iso3": row["iso3"],
            "provenance": provenance[country],
            "signals": {},
            "composite": {},
        }
```

Now update `generate_snapshot`. Replace the whole function:

```python
def generate_snapshot(path: Path = SNAPSHOT_PATH, as_of: str | None = None) -> dict:
    config = load_signal_config()
    df = load_mock_data()
    df = add_surprises(df)
    df = add_ranked_features(df, config["weights"])
    df = compute_deterministic_signals(df, config["weights"])
    snapshot = build_snapshot(df, config, as_of=as_of)
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return snapshot
```

with:

```python
def generate_snapshot(
    path: Path = SNAPSHOT_PATH,
    as_of: str | None = None,
    source: str = "mock",
) -> dict:
    config = load_signal_config()
    df, provenance = load_macro_inputs(source=source)
    df = add_surprises(df)
    df = add_ranked_features(df, config["weights"])
    df = compute_deterministic_signals(df, config["weights"])
    snapshot = build_snapshot(df, config, provenance, source=source, as_of=as_of)
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return snapshot
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_signal_engine.py -v`
Expected: all PASS, including the updated schema and new provenance tests.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: all PASS.

- [ ] **Step 6: Regenerate the committed mock snapshot (schema changed)**

Run: `python signal_engine.py`
Expected: prints `Wrote snapshot.json`; the file now contains `"data_source": "mock"` and a `"provenance"` block per economy.

- [ ] **Step 7: Commit**

```bash
git add signal_engine.py tests/test_signal_engine.py snapshot.json
git commit -m "feat: record data_source and per-value provenance in snapshot"
```

---

## Task 7: Source selection — config, CLI, and live CI refresh

**Files:**
- Modify: `signal_config.yaml`
- Modify: `signal_engine.py` (`__main__` block ~line 329)
- Modify: `.github/workflows/refresh.yml`
- Test: `tests/test_signal_engine.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_signal_engine.py`:

```python
def test_generate_snapshot_records_requested_source(tmp_path):
    snap = generate_snapshot(tmp_path / "s.json", as_of="2026-06-02", source="mock")
    assert snap["data_source"] == "mock"
```

- [ ] **Step 2: Run test to verify it passes already (regression guard)**

Run: `python -m pytest tests/test_signal_engine.py -k records_requested_source -v`
Expected: PASS (this guards the `source` plumbing from Task 6).

- [ ] **Step 3: Add a `data_source` block to `signal_config.yaml`**

Append to `signal_config.yaml`:

```yaml
data_source:
  # "mock" uses bundled CSVs (deterministic, offline). "live" overlays
  # World Bank macro data (inflation, GDP growth, unemployment) where available.
  default_mode: mock
  history_years: 8
```

- [ ] **Step 4: Add argparse to the `__main__` block**

In `signal_engine.py`, replace:

```python
if __name__ == "__main__":
    generate_snapshot()
    print(f"Wrote {SNAPSHOT_PATH}")
```

with:

```python
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate the macro signal snapshot.")
    parser.add_argument(
        "--source",
        choices=("mock", "live"),
        default="mock",
        help="Data source: 'mock' (bundled CSVs) or 'live' (World Bank API).",
    )
    args = parser.parse_args()
    generate_snapshot(source=args.source)
    print(f"Wrote {SNAPSHOT_PATH} (source={args.source})")
```

- [ ] **Step 5: Smoke-test the live path against the real API**

Run: `python signal_engine.py --source live`
Expected: prints `Wrote snapshot.json (source=live)`. Open `snapshot.json` and confirm at least one economy shows `"inflation_yoy": "world_bank:<year>"` in its `provenance` block (USA/Canada/Japan typically resolve; Euro Area inflation stays `"mock"` as verified).

- [ ] **Step 6: Restore the deterministic mock snapshot for the repo**

Run: `python signal_engine.py`
Expected: `Wrote snapshot.json (source=mock)` — the committed artifact stays deterministic and offline.

- [ ] **Step 7: Make the daily CI refresh use live data**

In `.github/workflows/refresh.yml`, change the "Generate snapshot" step's `run:` from:

```yaml
        run: python signal_engine.py
```

to:

```yaml
        run: python signal_engine.py --source live
```

- [ ] **Step 8: Run the whole suite**

Run: `python -m pytest -q`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add signal_config.yaml signal_engine.py .github/workflows/refresh.yml tests/test_signal_engine.py
git commit -m "feat: --source flag, config block, and live World Bank CI refresh"
```

---

## Task 8: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the Architecture section**

In `README.md`, under `## Architecture`, add this bullet after the `signal_engine.py` bullet:

```markdown
- `data_sources/world_bank.py`: live macro data adapter (World Bank API, no key); used when generation runs with `--source live`
```

- [ ] **Step 2: Update the Run section**

In `README.md`, under `## Run`, replace the code block:

```bash
pip install -r requirements.txt
python signal_engine.py
uvicorn main:app --reload
```

with:

```bash
pip install -r requirements.txt
python signal_engine.py            # mock data (deterministic, offline)
python signal_engine.py --source live   # live World Bank macro data
uvicorn main:app --reload
```

- [ ] **Step 3: Update Current Limitations**

In `README.md`, under `## Current Limitations`, replace the line `- Mock data only` with:

```markdown
- Macro inputs (inflation, GDP growth, unemployment) can be sourced live from the World Bank API; market, consensus, real estate, and PMI remain mock. Each value's origin is recorded in `snapshot.json` under `provenance`.
- Consensus for live macro columns is a naive baseline (mean of recent prior years), not true analyst consensus
```

- [ ] **Step 4: Update the TODO list**

In `README.md`, under `## TODO`, delete the line `- Add FRED / OECD / World Bank macro data` (now partially done) and add:

```markdown
- Extend live coverage to policy rate and PMI (needs keyed/proprietary sources)
- Add live market data (yfinance) and real estate (BIS)
```

- [ ] **Step 5: Final full verification**

Run: `python -m pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: document live World Bank macro source and provenance"
```

---

## Out of scope (explicit, for this stage)

- Live market data (yfinance), real estate (BIS), and true analyst consensus.
- Real RAG retrieval/LLM pipeline — the `rag_signal.py` stub is unchanged.
- Historical time-series storage and trend views — single live snapshot only.
- `policy_rate` and `pmi` live sourcing — no key-free source exists; they stay mock with `provenance="mock"`.
- Broader hardening from the tech-debt audit (dependency pinning, CWD-relative paths, API endpoint tests, CDN SRI) — track separately.
