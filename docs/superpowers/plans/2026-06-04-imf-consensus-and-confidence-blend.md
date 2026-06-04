# IMF Real Consensus + Confidence-Weighted RAG Blend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the naive live "consensus" baseline with real IMF World Economic Outlook forecasts (#1), and make the RAG blend weight scale with RAG confidence (#4).

**Architecture:** Two independent changes to the deterministic signal pipeline. Part A adds an `imf_weo` data adapter (mirroring the existing `world_bank` adapter) and redefines the live macro "surprise" as a forward expected-change, `forecast(T+1) − actual(T)`, where the forecast is the real IMF consensus. Part B replaces the fixed `0.75/0.25` deterministic/RAG blend with a confidence-weighted convex blend so a low-confidence RAG view barely moves the signal. The two parts touch different code paths and can be built, tested, committed, and merged separately.

**Tech Stack:** Python 3.14, pandas, numpy, httpx (injectable `fetch_json` for offline tests), PyYAML, pytest. No new dependencies.

---

## Context & Verified Facts (read before starting)

These were confirmed live against the real APIs on 2026-06-04 — do not re-derive, just use them:

- **IMF DataMapper API** (free, no key): `GET https://www.imf.org/external/datamapper/api/v1/{INDICATOR}` returns **all 229 economies** in `json()["values"][INDICATOR][CODE][YEAR]` (the country in the URL path is ignored — filter client-side).
- **The IMF WAF rejects a `User-Agent: Mozilla/...` header (HTTP 403).** Use httpx's *default* User-Agent (send no custom UA). This is the opposite of many sites — do not add a browser UA.
- **Economy → IMF code:** `USA, CAN, CHN, JPN, BRA`, and **Euro Area → `EURO`** (label "Euro area"; confirmed `EURO` 2024 values match World Bank actuals). Do **not** use `EU` (= European Union) or `EUQ` (wrong series, no unemployment).
- **Column → IMF indicator code:** `inflation_yoy → PCPIPCH`, `gdp_growth → NGDP_RPCH`, `unemployment → LUR`. All three exist for all six economies, with forecast years present **through 2031**, so `forecast(T+1)` is always available for the current actual year `T` (~2024).
- The engine already fetches the realized **actual(T)** for these three columns from World Bank (`data_sources/world_bank.py`), tagged `world_bank:{year}` in provenance. We keep that and add the IMF forecast as the consensus.

### Surprise definition (decided)

Live macro feature = **expected change** = `IMF_forecast(T+1) − actual(T)` (e.g., US inflation `2.3 (2025f) − 2.95 (2024) = −0.65`, i.e. cooling). The IMF DataMapper only exposes the latest vintage, so a true point-in-time forecast error is not available; the forward expected-change is the chosen, fully-feasible definition.

### Weight-sign review (already done — signs are unchanged)

Under the new "expected change" meaning, every existing weight sign in `signal_config.yaml` stays economically correct, because "X is high now" and "X is expected to rise" push each asset the same way:

| Live feature (positive means…) | rates | fx | equity | real_estate | OK? |
|---|---|---|---|---|---|
| inflation expected to rise | −0.35 (bonds↓) | — | — | — | ✅ |
| growth expected to accelerate | −0.25 | +0.30 | +0.30 | — | ✅ |
| unemployment expected to rise | +0.20 (bonds↑) | −0.05 | — | −0.10 | ✅ |

So **no weight values change.** `policy_surprise` and `pmi_surprise` stay mock-based beat/miss (`policy_rate`/`pmi` have no live source), so in live mode those two keep `actual − consensus` semantics while the three IMF-backed columns use `consensus − actual`. This is intentional and must be documented.

### Critical sign trap

The engine's `add_surprises` computes `surprise = actual − consensus`. If we naively drop the forecast into the consensus column and leave that formula, we get `actual − forecast = −(expected change)` — every sign flips. **Part A Task A2 changes the formula to `consensus − actual` for the IMF-backed columns** so the stored consensus column shows the *real* IMF forecast (transparency) while the feature equals the expected change (correct sign).

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `data_sources/imf_weo.py` | **Create** | Fetch + parse IMF DataMapper forecasts; injectable `fetch_json`; map economy/column → `{year: value}`. Mirrors `world_bank.py`. |
| `tests/test_imf_weo.py` | **Create** | Unit tests for the IMF adapter using a fake `fetch_json` (no network). |
| `signal_engine.py` | **Modify** | `add_surprises` gains `expected_change_columns`; `load_macro_inputs` returns a 3-tuple and overlays IMF consensus in live mode; `generate_snapshot` passes the new set; Part B adds `blend_signal` + confidence weighting in `build_snapshot`; `load_signal_config` validates blend weights sum to 1. |
| `tests/test_signal_engine.py` | **Modify** | Update the two tests that unpack `load_macro_inputs`; add tests for expected-change surprises, live IMF overlay, and the confidence-weighted blend. |
| `README.md` | **Modify** | Document IMF consensus + expected-change semantics (Part A) and confidence-weighted blend (Part B). |

---

# PART A — Real IMF Consensus (#1)

### Task A1: IMF WEO data adapter

**Files:**
- Create: `data_sources/imf_weo.py`
- Test: `tests/test_imf_weo.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_imf_weo.py`:

```python
from data_sources import imf_weo as imf


def test_fetch_indicator_maps_codes_parses_years_and_drops_nulls():
    payload = {"values": {"PCPIPCH": {
        "USA": {"2024": 2.9, "2025": 2.3, "2026": None},  # null year dropped
        "EURO": {"2024": 2.4, "2025": 2.1},
        "ZZZ": {"2024": 9.9},                              # unmapped code ignored
    }}}
    captured = {}

    def fake(url):
        captured["url"] = url
        return payload

    series = imf.fetch_indicator("inflation_yoy", fetch_json=fake)

    assert series["United States of America"] == {2024: 2.9, 2025: 2.3}
    assert series["Euro Area"] == {2024: 2.4, 2025: 2.1}
    assert "Canada" not in series          # economy with no data is absent
    assert captured["url"].endswith("/PCPIPCH")


def test_fetch_indicator_raises_on_bad_payload():
    def fake(url):
        return {"unexpected": True}
    try:
        imf.fetch_indicator("gdp_growth", fetch_json=fake)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_load_imf_forecasts_nests_by_economy_and_column():
    payloads = {
        "PCPIPCH": {"values": {"PCPIPCH": {"USA": {"2024": 2.9, "2025": 2.3}}}},
        "NGDP_RPCH": {"values": {"NGDP_RPCH": {"USA": {"2024": 2.8, "2025": 2.0}}}},
        "LUR": {"values": {"LUR": {"USA": {"2024": 4.0, "2025": 4.2}}}},
    }

    def fake(url):
        for ind, p in payloads.items():
            if url.endswith("/" + ind):
                return p
        raise AssertionError(url)

    out = imf.load_imf_forecasts(
        ("United States of America", "Canada"), fetch_json=fake
    )
    assert out["United States of America"]["inflation_yoy"] == {2024: 2.9, 2025: 2.3}
    assert out["United States of America"]["gdp_growth"] == {2024: 2.8, 2025: 2.0}
    assert out["Canada"] == {}             # present but empty -> caller falls back
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_imf_weo.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'data_sources.imf_weo'`.

- [ ] **Step 3: Implement the adapter**

Create `data_sources/imf_weo.py`:

```python
"""IMF World Economic Outlook forecasts (free, no API key).

Fetches forward macro forecasts for the signal universe from the public IMF
DataMapper API and exposes them per economy/column as {year: value}. Used to
build a real "consensus" baseline: in live mode the macro 'surprise' becomes
the forecast-implied expected change, forecast(T+1) - actual(T).

All HTTP access goes through an injectable ``fetch_json`` callable so callers
(and tests) can run without network.

NOTE: the IMF WAF returns 403 for a 'Mozilla' User-Agent, so the default
fetch deliberately sends no custom User-Agent (httpx's own UA works).
"""
from __future__ import annotations

from typing import Callable

import httpx

IMF_BASE = "https://www.imf.org/external/datamapper/api/v1"

# Engine economy name -> IMF DataMapper code. Euro Area is "EURO" (Euro area).
# "EU" (European Union) and "EUQ" are intentionally NOT used.
IMF_CODE_BY_ECONOMY = {
    "United States of America": "USA",
    "Canada": "CAN",
    "China": "CHN",
    "Japan": "JPN",
    "Brazil": "BRA",
    "Euro Area": "EURO",
}

# Engine macro column -> IMF WEO indicator code (forecast columns only).
IMF_INDICATOR_BY_COLUMN = {
    "inflation_yoy": "PCPIPCH",   # Inflation, average consumer prices (% change)
    "gdp_growth": "NGDP_RPCH",    # Real GDP growth (% change)
    "unemployment": "LUR",        # Unemployment rate (% of total labor force)
}

FORECAST_COLUMNS = tuple(IMF_INDICATOR_BY_COLUMN)


def _default_fetch_json(url: str) -> dict:
    # No custom User-Agent: the IMF WAF rejects 'Mozilla' UAs with HTTP 403.
    response = httpx.get(url, timeout=20.0)
    response.raise_for_status()
    return response.json()


def fetch_indicator(
    column: str,
    fetch_json: Callable[[str], dict] = _default_fetch_json,
) -> dict[str, dict[int, float]]:
    """Return {economy: {year: value}} for the six mapped economies.

    The DataMapper returns every economy under values[indicator]; we keep only
    the engine universe. Economies/years with no value are simply absent.
    """
    indicator = IMF_INDICATOR_BY_COLUMN[column]
    url = f"{IMF_BASE}/{indicator}"
    payload = fetch_json(url)
    try:
        values = payload["values"][indicator]
    except (TypeError, KeyError) as exc:
        raise ValueError(
            f"Unexpected IMF response for {indicator}: {payload!r:.200}"
        ) from exc

    out: dict[str, dict[int, float]] = {}
    for economy, code in IMF_CODE_BY_ECONOMY.items():
        series = values.get(code)
        if not isinstance(series, dict):
            continue
        parsed = {
            int(year): float(value)
            for year, value in series.items()
            if value is not None
        }
        if parsed:
            out[economy] = parsed
    return out


def load_imf_forecasts(
    economies: tuple[str, ...],
    fetch_json: Callable[[str], dict] = _default_fetch_json,
) -> dict[str, dict[str, dict[int, float]]]:
    """Return {economy: {column: {year: value}}} for the forecast columns."""
    by_column = {
        column: fetch_indicator(column, fetch_json=fetch_json)
        for column in FORECAST_COLUMNS
    }
    out: dict[str, dict[str, dict[int, float]]] = {economy: {} for economy in economies}
    for column, series_by_economy in by_column.items():
        for economy in economies:
            if economy in series_by_economy:
                out[economy][column] = series_by_economy[economy]
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_imf_weo.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add data_sources/imf_weo.py tests/test_imf_weo.py
git commit -m "feat(data): add IMF WEO forecast adapter"
```

---

### Task A2: Expected-change surprises in `add_surprises`

**Files:**
- Modify: `signal_engine.py` (`add_surprises`, currently lines 220-227)
- Test: `tests/test_signal_engine.py`

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_signal_engine.py` (the file already imports `signal_engine as se` at line 89, which is available to every test):

```python
import pandas as pd


def _surprise_frame():
    return pd.DataFrame({
        "inflation_yoy": [3.0], "inflation_consensus": [2.5],
        "gdp_growth": [2.0], "gdp_consensus": [1.5],
        "unemployment": [4.0], "unemployment_consensus": [4.5],
        "policy_rate": [5.0], "policy_rate_consensus": [4.8],
        "pmi": [51.0], "pmi_consensus": [50.0],
    })


def test_add_surprises_default_is_actual_minus_consensus():
    out = se.add_surprises(_surprise_frame())
    assert out["inflation_surprise"].iloc[0] == pytest.approx(0.5)    # 3.0 - 2.5
    assert out["growth_surprise"].iloc[0] == pytest.approx(0.5)       # 2.0 - 1.5
    assert out["unemployment_surprise"].iloc[0] == pytest.approx(-0.5)
    assert out["policy_surprise"].iloc[0] == pytest.approx(0.2)


def test_add_surprises_expected_change_flips_named_columns_only():
    out = se.add_surprises(
        _surprise_frame(),
        expected_change_columns={"inflation_surprise", "growth_surprise", "unemployment_surprise"},
    )
    # forecast(consensus) - actual  => expected change
    assert out["inflation_surprise"].iloc[0] == pytest.approx(-0.5)   # 2.5 - 3.0
    assert out["growth_surprise"].iloc[0] == pytest.approx(-0.5)      # 1.5 - 2.0
    assert out["unemployment_surprise"].iloc[0] == pytest.approx(0.5) # 4.5 - 4.0
    # non-IMF columns stay beat/miss
    assert out["policy_surprise"].iloc[0] == pytest.approx(0.2)       # 5.0 - 4.8
    assert out["pmi_surprise"].iloc[0] == pytest.approx(1.0)          # 51 - 50
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_signal_engine.py -k add_surprises -q`
Expected: FAIL — `test_add_surprises_expected_change_flips_named_columns_only` fails because `add_surprises` does not accept `expected_change_columns` yet (TypeError).

- [ ] **Step 3: Replace `add_surprises`**

In `signal_engine.py`, replace the whole current function (lines 220-227):

```python
def add_surprises(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["inflation_surprise"] = out["inflation_yoy"] - out["inflation_consensus"]
    out["growth_surprise"] = out["gdp_growth"] - out["gdp_consensus"]
    out["unemployment_surprise"] = out["unemployment"] - out["unemployment_consensus"]
    out["policy_surprise"] = out["policy_rate"] - out["policy_rate_consensus"]
    out["pmi_surprise"] = out["pmi"] - out["pmi_consensus"]
    return out
```

with:

```python
# (actual column, consensus column, surprise column)
SURPRISE_SPECS = (
    ("inflation_yoy", "inflation_consensus", "inflation_surprise"),
    ("gdp_growth", "gdp_consensus", "growth_surprise"),
    ("unemployment", "unemployment_consensus", "unemployment_surprise"),
    ("policy_rate", "policy_rate_consensus", "policy_surprise"),
    ("pmi", "pmi_consensus", "pmi_surprise"),
)


def add_surprises(
    df: pd.DataFrame,
    expected_change_columns: frozenset[str] = frozenset(),
) -> pd.DataFrame:
    """Compute per-feature surprises.

    Default (mock mode): surprise = actual - consensus  (beat/miss vs the
    period consensus; positive means the print ran hot).

    For columns named in ``expected_change_columns`` (live IMF mode), the
    consensus column holds the IMF next-year forecast, so the feature is the
    forecast-implied expected change = consensus - actual (positive means the
    series is expected to rise). The sign convention is identical either way
    ("higher/hotter => positive"), so the configured weight signs are unchanged.
    """
    out = df.copy()
    for actual_col, consensus_col, surprise_col in SURPRISE_SPECS:
        if surprise_col in expected_change_columns:
            out[surprise_col] = out[consensus_col] - out[actual_col]
        else:
            out[surprise_col] = out[actual_col] - out[consensus_col]
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_signal_engine.py -k add_surprises -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — all previously-passing tests still pass (the default arg keeps mock behavior identical).

- [ ] **Step 6: Commit**

```bash
git add signal_engine.py tests/test_signal_engine.py
git commit -m "feat(signal): support expected-change surprises in add_surprises"
```

---

### Task A3: Overlay IMF consensus in `load_macro_inputs` (live mode)

**Files:**
- Modify: `signal_engine.py` (`load_macro_inputs` lines 171-217; `generate_snapshot` lines 377-389)
- Test: `tests/test_signal_engine.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_signal_engine.py`:

```python
from data_sources import world_bank as wb_mod
from data_sources import imf_weo as imf_mod


def _wb_fake_all_six(url):
    """World Bank fake: actual(2024)=10.0, prior(2023)=9.0 for all six economies."""
    for code in wb_mod.WB_INDICATOR_BY_COLUMN.values():
        if code in url:
            rows = []
            for iso in wb_mod.WB_CODE_BY_ECONOMY.values():
                rows.append({"countryiso3code": iso, "date": "2024", "value": 10.0})
                rows.append({"countryiso3code": iso, "date": "2023", "value": 9.0})
            return [{"page": 1, "pages": 1, "per_page": 20000, "total": len(rows)}, rows]
    raise AssertionError(url)


def _imf_fake_all_six(url):
    """IMF fake: forecast(2025)=12.0 for every economy/indicator."""
    for ind in imf_mod.IMF_INDICATOR_BY_COLUMN.values():
        if url.endswith("/" + ind):
            by_code = {code: {"2024": 8.0, "2025": 12.0}
                       for code in imf_mod.IMF_CODE_BY_ECONOMY.values()}
            return {"values": {ind: by_code}}
    raise AssertionError(url)


def test_load_macro_inputs_mock_returns_empty_expected_change():
    df, provenance, expected_change = se.load_macro_inputs(source="mock")
    assert expected_change == frozenset()
    assert len(df) == 6


def test_load_macro_inputs_live_overlays_imf_consensus_and_marks_expected_change():
    df, provenance, expected_change = se.load_macro_inputs(
        source="live", fetch_json=_wb_fake_all_six, imf_fetch_json=_imf_fake_all_six,
    )
    assert expected_change == frozenset(
        {"inflation_surprise", "growth_surprise", "unemployment_surprise"}
    )
    usa = "United States of America"
    # consensus column holds the REAL IMF forecast (2025), not a naive baseline
    assert df.loc[usa, "inflation_consensus"] == 12.0
    assert df.loc[usa, "gdp_consensus"] == 12.0
    # actual still from World Bank
    assert df.loc[usa, "inflation_yoy"] == 10.0
    assert provenance[usa]["inflation_yoy"] == "world_bank:2024"
    assert provenance[usa]["inflation_consensus"] == "imf_weo:2025"
    # the resulting feature is the expected change forecast(T+1) - actual(T)
    out = se.add_surprises(df, expected_change)
    assert out.loc[usa, "inflation_surprise"] == pytest.approx(2.0)   # 12 - 10
```

Update the **two existing** tests that unpack `load_macro_inputs` (they currently unpack two values and will now break). Change their first lines:

```python
# in test_load_macro_inputs_mock_marks_all_provenance_mock:
df, provenance, _expected_change = se.load_macro_inputs(source="mock")

# in test_load_macro_inputs_live_overlays_world_bank_values:
df, provenance, _expected_change = se.load_macro_inputs(source="live", fetch_json=fake_fetch)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_signal_engine.py -k "load_macro_inputs" -q`
Expected: FAIL — new live test errors (`load_macro_inputs() got an unexpected keyword argument 'imf_fetch_json'`) and `test_load_macro_inputs_mock_returns_empty_expected_change` fails (returns a 2-tuple).

- [ ] **Step 3: Update the import and `load_macro_inputs`**

In `signal_engine.py`, add to the imports near the top (after `from data_sources import world_bank as wb`):

```python
from data_sources import imf_weo
```

Replace the entire `load_macro_inputs` function (lines 171-217) with:

```python
def load_macro_inputs(
    source: str = "mock",
    data_dir: Path = DATA_DIR,
    fetch_json=None,
    imf_fetch_json=None,
) -> tuple[pd.DataFrame, dict[str, dict[str, str]], frozenset[str]]:
    """Return (joined input frame, provenance, expected_change_columns).

    source="mock": the existing mock CSVs, every value tagged "mock"; the
    expected-change set is empty (surprises are classic beat/miss).
    source="live": World Bank realized values overlay the mock actuals, and the
    IMF WEO next-year forecast overlays the consensus for the three live macro
    columns where every economy has data. Those columns are returned in
    expected_change_columns so the surprise becomes forecast(T+1) - actual(T).
    """
    df = load_mock_data(data_dir)

    tracked_columns = sorted(REQUIRED_MACRO_COLUMNS - {"economy"})
    provenance = {
        economy: {column: "mock" for column in tracked_columns}
        for economy in df.index
    }

    if source == "mock":
        return df, provenance, frozenset()
    if source != "live":
        raise ValueError(f"Unknown data source: {source!r} (expected 'mock' or 'live')")

    end_year = date.today().year
    start_year = end_year - LIVE_HISTORY_YEARS
    wb_kwargs = {} if fetch_json is None else {"fetch_json": fetch_json}
    macro, _wb_consensus, live_provenance = wb.load_world_bank_macro(
        tuple(df.index), start_year, end_year, **wb_kwargs
    )

    # Overlay World Bank realized actuals (consensus is replaced by IMF below).
    for economy in df.index:
        for column, value in macro[economy].items():
            df.loc[economy, column] = value
            provenance[economy][column] = live_provenance[economy][column]

    imf_kwargs = {} if imf_fetch_json is None else {"fetch_json": imf_fetch_json}
    forecasts = imf_weo.load_imf_forecasts(tuple(df.index), **imf_kwargs)

    consensus_column = {
        "inflation_yoy": "inflation_consensus",
        "gdp_growth": "gdp_consensus",
        "unemployment": "unemployment_consensus",
    }
    surprise_column = {
        "inflation_yoy": "inflation_surprise",
        "gdp_growth": "growth_surprise",
        "unemployment": "unemployment_surprise",
    }

    # All-or-nothing per column: only treat a column as IMF-backed expected
    # change when every economy has a World Bank actual year T and an IMF
    # forecast for T+1. Otherwise leave the mock consensus untouched.
    expected_change: set[str] = set()
    for macro_col, cons_col in consensus_column.items():
        resolved: dict[str, tuple[float, int]] = {}
        for economy in df.index:
            prov = provenance[economy][macro_col]
            if not prov.startswith("world_bank:"):
                break
            actual_year = int(prov.split(":")[1])
            forecast = forecasts.get(economy, {}).get(macro_col, {}).get(actual_year + 1)
            if forecast is None:
                break
            resolved[economy] = (forecast, actual_year + 1)
        if len(resolved) != len(df.index):
            continue  # not fully IMF-backed -> keep mock consensus for this column
        for economy, (forecast, forecast_year) in resolved.items():
            df.loc[economy, cons_col] = forecast
            provenance[economy][cons_col] = f"imf_weo:{forecast_year}"
        expected_change.add(surprise_column[macro_col])

    if df.isna().any().any():
        raise ValueError("Macro inputs contain missing values after live overlay")
    return df, provenance, frozenset(expected_change)
```

- [ ] **Step 4: Update `generate_snapshot` to pass the new set**

In `signal_engine.py`, in `generate_snapshot` (lines 377-389) replace these two lines:

```python
    df, provenance = load_macro_inputs(source=source)
    df = add_surprises(df)
```

with:

```python
    df, provenance, expected_change_columns = load_macro_inputs(source=source)
    df = add_surprises(df, expected_change_columns)
```

- [ ] **Step 5: Run the targeted tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_signal_engine.py -q`
Expected: PASS.

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all tests, including `test_imf_weo.py`).

- [ ] **Step 6: Commit**

```bash
git add signal_engine.py tests/test_signal_engine.py
git commit -m "feat(signal): use IMF WEO forecast as live consensus (expected-change surprise)"
```

---

### Task A4: Live smoke test against the real IMF + World Bank APIs

**Files:** none (manual verification only)

- [ ] **Step 1: Generate a live snapshot against the real APIs**

Run: `.venv/bin/python signal_engine.py --source live`
Expected: prints `Wrote snapshot.json (source=live)` with no error. (Requires network. If the World Bank API throttles with an HTTP 400 HTML page, re-run after a few seconds — it is rate-limiting, not a code bug.)

- [ ] **Step 2: Confirm the snapshot used real IMF consensus**

Run:

```bash
.venv/bin/python -c "import json; s=json.load(open('snapshot.json')); e=s['economies']['United States of America']; print('data_source=', s['data_source']); print('provenance=', e['provenance'])"
```

Expected: `data_source= live`, and provenance shows `inflation_yoy` as `world_bank:<year>` and `inflation_consensus` as `imf_weo:<year+1>`.

- [ ] **Step 3: Restore the committed mock snapshot**

Run: `.venv/bin/python signal_engine.py` then `git checkout -- snapshot.json` is **not** needed — instead regenerate the mock snapshot so the repo's committed artifact stays mock-sourced:

Run: `.venv/bin/python signal_engine.py`
Expected: prints `Wrote snapshot.json (source=mock)`. Leave this mock snapshot in the working tree (committed in Task A5).

---

### Task A5: Regenerate the committed mock snapshot

**Files:**
- Modify: `snapshot.json` (regenerated artifact)

- [ ] **Step 1: Regenerate**

Run: `.venv/bin/python signal_engine.py`
Expected: `Wrote snapshot.json (source=mock)`.

- [ ] **Step 2: Verify it is unchanged by Part A (mock path must be byte-stable)**

Run: `git diff --stat snapshot.json`
Expected: **no diff** — Part A does not alter mock-mode output (the `expected_change_columns` default is empty). If there is a diff, a mock-path regression was introduced; stop and investigate before continuing.

- [ ] **Step 3: Commit (only if there is a diff; otherwise skip)**

```bash
git add snapshot.json
git commit -m "chore: regenerate mock snapshot"
```

---

### Task A6: Document the IMF consensus change

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the Current Limitations bullet**

In `README.md` replace this line (currently line 99):

```text
- Consensus for live macro columns is a naive baseline (mean of recent prior years), not true analyst consensus
```

with:

```text
- Consensus for live macro columns (inflation, GDP growth, unemployment) is the IMF WEO **next-year forecast**; the live "surprise" is the forecast-implied expected change, `forecast(T+1) - actual(T)`. It is an institutional forecast, not an intra-period analyst-consensus print. `policy_rate` and `pmi` have no live source, so their surprises stay mock beat/miss.
```

- [ ] **Step 2: Update the TODO list**

In `README.md` remove this line (currently line 110):

```text
- Add real consensus data
```

- [ ] **Step 3: Note the adapter in Architecture**

In `README.md`, after the `data_sources/world_bank.py` bullet (line 9), add:

```text
- `data_sources/imf_weo.py`: IMF World Economic Outlook forecast adapter (DataMapper API, no key); supplies the live "consensus" so the live surprise becomes a forward expected-change
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document IMF WEO consensus and expected-change surprise"
```

---

# PART B — Confidence-Weighted RAG Blend (#4)

**Goal:** Scale the RAG weight by RAG confidence so a low-confidence narrative barely moves the signal, and a no-view cell collapses toward the deterministic signal.

**Formula:** `effective_rag = rag_weight * rag_confidence`; `final = clip((1 - effective_rag) * deterministic + effective_rag * rag)`. At `confidence = 1` this equals the current `0.75*det + 0.25*rag` (because `deterministic_weight == 1 - rag_weight`). At `confidence = 0`, RAG is ignored.

### Task B1: Extract and confidence-weight the blend

**Files:**
- Modify: `signal_engine.py` (`build_snapshot` lines 310-374; add a `blend_signal` helper near `clip_signal`)
- Test: `tests/test_signal_engine.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_signal_engine.py`:

```python
def test_blend_signal_full_confidence_matches_legacy_weights():
    # confidence 1.0 -> 0.75*det + 0.25*rag
    assert se.blend_signal(0.8, 0.4, 1.0, 0.25) == pytest.approx(0.7)


def test_blend_signal_zero_confidence_ignores_rag():
    assert se.blend_signal(0.8, 0.4, 0.0, 0.25) == pytest.approx(0.8)


def test_blend_signal_partial_confidence_scales_rag():
    # effective_rag = 0.25*0.75 = 0.1875 -> 0.8125*0.8 + 0.1875*0.4
    assert se.blend_signal(0.8, 0.4, 0.75, 0.25) == pytest.approx(0.725)


def test_blend_signal_is_clipped():
    assert se.blend_signal(1.0, 1.0, 1.0, 0.25) == 1.0
    assert se.blend_signal(-1.0, -1.0, 1.0, 0.25) == -1.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_signal_engine.py -k blend_signal -q`
Expected: FAIL — `AttributeError: module 'signal_engine' has no attribute 'blend_signal'`.

- [ ] **Step 3: Add the `blend_signal` helper**

In `signal_engine.py`, immediately after `clip_signal` (lines 78-79), add:

```python
def blend_signal(
    deterministic: float,
    rag_signal: float,
    rag_confidence: float,
    rag_weight: float,
) -> float:
    """Confidence-weighted convex blend of deterministic and RAG signals.

    effective_rag = rag_weight * rag_confidence
    final         = (1 - effective_rag) * deterministic + effective_rag * rag

    At confidence 1 this reduces to deterministic_weight*det + rag_weight*rag
    (since deterministic_weight == 1 - rag_weight); at confidence 0 the RAG view
    is ignored and the deterministic signal passes through unchanged.
    """
    effective_rag = rag_weight * rag_confidence
    return clip_signal((1.0 - effective_rag) * deterministic + effective_rag * rag_signal)
```

- [ ] **Step 4: Use it in `build_snapshot`**

In `signal_engine.py` `build_snapshot`, delete this line (currently line 319):

```python
    deterministic_weight = float(blend["deterministic_weight"])
```

Then replace this block (currently lines 345-348):

```python
            deterministic = round(clip_signal(row[f"{asset_class}_deterministic_signal"]), 4)
            rag = compute_rag_signal(country, asset_class)
            rag_signal = round(clip_signal(rag["signal"]), 4)
            final = round(clip_signal(deterministic_weight * deterministic + rag_weight * rag_signal), 4)
```

with:

```python
            deterministic = round(clip_signal(row[f"{asset_class}_deterministic_signal"]), 4)
            rag = compute_rag_signal(country, asset_class)
            rag_signal = round(clip_signal(rag["signal"]), 4)
            rag_confidence = float(rag["confidence"])
            final = round(blend_signal(deterministic, rag_signal, rag_confidence, rag_weight), 4)
```

Then, inside the `entry["signals"][asset_class] = { ... }` dict (currently lines 355-365), add one field for transparency after `"rag_confidence": ...,`:

```python
                "rag_effective_weight": round(rag_weight * rag_confidence, 4),
```

- [ ] **Step 5: Run the targeted tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_signal_engine.py -k blend_signal -q`
Expected: PASS (4 passed).

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (the composite-mean and clip tests still hold; no test asserts the old exact blend).

- [ ] **Step 6: Commit**

```bash
git add signal_engine.py tests/test_signal_engine.py
git commit -m "feat(signal): confidence-weight the RAG blend"
```

---

### Task B2: Validate blend weights sum to 1.0

**Files:**
- Modify: `signal_engine.py` (`load_signal_config`, the blend validation at lines 120-122)
- Test: `tests/test_signal_engine.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_signal_engine.py`:

```python
def test_load_signal_config_rejects_blend_not_summing_to_one(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "weights:\n"
        "  fx: {growth_surprise_rank: 1.0}\n"
        "  rates: {inflation_surprise_rank: -1.0}\n"
        "  equity: {growth_surprise_rank: 1.0}\n"
        "  real_estate: {rate_3m_change_rank: -1.0}\n"
        "signal_blend:\n"
        "  deterministic_weight: 0.5\n"
        "  rag_weight: 0.25\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must sum to 1.0"):
        se.load_signal_config(bad)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_signal_engine.py -k blend_not_summing -q`
Expected: FAIL — no error is raised (config loads despite weights summing to 0.75).

- [ ] **Step 3: Add the validation**

In `signal_engine.py` `load_signal_config`, replace this block (currently lines 120-122):

```python
    for key in ("deterministic_weight", "rag_weight"):
        if not isinstance(blend.get(key), (int, float)):
            raise ValueError(f"Malformed signal config {path}: signal_blend.{key} must be numeric")
```

with:

```python
    for key in ("deterministic_weight", "rag_weight"):
        if not isinstance(blend.get(key), (int, float)):
            raise ValueError(f"Malformed signal config {path}: signal_blend.{key} must be numeric")
    if abs(float(blend["deterministic_weight"]) + float(blend["rag_weight"]) - 1.0) > 1e-9:
        raise ValueError(
            f"Malformed signal config {path}: signal_blend deterministic_weight + "
            f"rag_weight must sum to 1.0"
        )
```

- [ ] **Step 4: Run the test, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_signal_engine.py -k blend_not_summing -q`
Expected: PASS.

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (the shipped `signal_config.yaml` has `0.75 + 0.25 == 1.0`).

- [ ] **Step 5: Commit**

```bash
git add signal_engine.py tests/test_signal_engine.py
git commit -m "feat(signal): validate blend weights sum to 1.0"
```

---

### Task B3: Regenerate snapshot + document the blend

**Files:**
- Modify: `snapshot.json`, `README.md`

- [ ] **Step 1: Regenerate the snapshot**

Run: `.venv/bin/python signal_engine.py`
Expected: `Wrote snapshot.json (source=mock)`.

- [ ] **Step 2: Sanity-check the new field and a changed final**

Run:

```bash
.venv/bin/python -c "import json; s=json.load(open('snapshot.json')); eq=s['economies']['United States of America']['signals']['equity']; print('rag_effective_weight=', eq['rag_effective_weight'], 'final=', eq['final'])"
```

Expected: `rag_effective_weight= 0.1875` for US equity (RAG confidence 0.75 × 0.25), and `final` differs from the pre-change value (now uses 0.1875 RAG weight instead of 0.25).

- [ ] **Step 3: Update the README methodology**

In `README.md` replace this block (currently lines 30-33):

```text
signal = 2 * percentile_rank - 1
final_signal = 0.75 * deterministic_signal + 0.25 * rag_signal
```

with:

```text
signal = 2 * percentile_rank - 1
effective_rag_weight = rag_weight * rag_confidence          # rag_weight = 0.25
final_signal = (1 - effective_rag_weight) * deterministic_signal + effective_rag_weight * rag_signal
```

And add one sentence right after that code block:

```text
The RAG overlay is confidence-weighted: a full-confidence view uses the configured `rag_weight` (0.25), while a no-view / low-confidence cell collapses toward the deterministic signal. Each signal reports its `rag_effective_weight`.
```

- [ ] **Step 4: Commit**

```bash
git add snapshot.json README.md
git commit -m "docs: document confidence-weighted blend; regenerate snapshot"
```

---

## Final verification

- [ ] **Run the whole suite:** `.venv/bin/python -m pytest -q` → all green (original 29 + new tests).
- [ ] **Mock determinism intact:** `.venv/bin/python signal_engine.py && git diff --stat snapshot.json` → snapshot only differs from pre-Part-B by the blend/`rag_effective_weight` changes, and is stable across repeated runs.
- [ ] **Live path works end-to-end:** `.venv/bin/python signal_engine.py --source live` writes a snapshot whose US `inflation_consensus` provenance is `imf_weo:<year>` (network required).

---

## Self-Review notes (already applied)

- **Spec coverage:** #1 = Tasks A1-A6 (IMF adapter, expected-change surprise, live overlay, smoke test, snapshot, docs). #4 = Tasks B1-B3 (confidence blend, weight-sum validation, snapshot+docs).
- **No placeholders:** every code/test step contains complete code; every run step has an exact command + expected result.
- **Type/name consistency:** `load_macro_inputs` returns a 3-tuple everywhere it is called (`generate_snapshot` + both updated tests); `add_surprises(df, expected_change_columns=...)`, `blend_signal(deterministic, rag_signal, rag_confidence, rag_weight)`, `imf_weo.fetch_indicator(column, fetch_json=...)`, and `imf_weo.load_imf_forecasts(economies, fetch_json=...)` signatures match across tasks.
- **Independence:** Part A and Part B share no code; either can be merged without the other.
