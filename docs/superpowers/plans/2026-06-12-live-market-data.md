# Live Market Data (Yahoo) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In `--source live` mode, source the `equity_3m_return` and `fx_3m_return` market columns from real Yahoo Finance price data instead of the mock CSV.

**Architecture:** A new `data_sources/market.py` adapter fetches monthly price history from Yahoo's public chart endpoint over `httpx` (mirroring the `world_bank`/`imf_weo` adapters with an injectable `fetch_json`) and computes trailing 3-month returns. A new `overlay_market_inputs()` step in `signal_engine.py` overlays those two columns in live mode using the same all-or-nothing-per-column fallback the macro overlay uses; mock mode is untouched.

**Tech Stack:** Python 3.14, pandas, httpx (already a dependency — no `yfinance` package), pytest. No new dependencies.

---

## Context & Verified Facts (read before starting)

Confirmed live against the real API on 2026-06-12 — use these, don't re-derive:

- **Yahoo chart endpoint** (free, no key): `GET https://query1.finance.yahoo.com/v8/finance/chart/{TICKER}?range=1y&interval=1mo`. Monthly closes live at `json()["chart"]["result"][0]["indicators"]["adjclose"][0]["adjclose"]` (fall back to `["quote"][0]["close"]`), aligned with `["result"][0]["timestamp"]`.
- **Yahoo requires a browser `User-Agent`.** A request with no UA gets **HTTP 429 "Edge: Too Many Requests"**; with `User-Agent: Mozilla/5.0` it returns 200. Yahoo also rate-limits bursts (429), so the default fetch retries with backoff. (This is the *opposite* of the IMF adapter, which rejects a Mozilla UA.)
- **Why httpx, not `yfinance`:** the existing adapters use an injectable `fetch_json` so tests run offline; `yfinance` is a heavy, fragile scraper that can't be cleanly injected. We use the same chart JSON `yfinance` itself calls.
- **3-month return** = `close[-1] / close[-4] - 1` from monthly bars (the latest bar may be the current partial month — that is the intended "as of now" 3m return).

### Tickers & conventions (verified, all six economies return data)

| Economy | Equity ETF (USD) | FX pair |
|---|---|---|
| United States of America | `SPY` | — (numeraire → `fx_3m_return = 0.0`) |
| Canada | `EWC` | `CADUSD=X` |
| China | `MCHI` | `CNYUSD=X` |
| Japan | `EWJ` | `JPYUSD=X` |
| Brazil | `EWZ` | `BRLUSD=X` |
| Euro Area | `EZU` | `EURUSD=X` |

- `equity_3m_return` = trailing 3-month return of the **USD** country ETF (positive = up in USD).
- `fx_3m_return` = trailing 3-month return of `<CCY>USD=X` = local currency value in USD (positive = local **appreciation** vs USD). The US is the numeraire, so its FX return is `0.0`, dated to the US equity bar.

### Scope (which columns go live)

- **Live:** `equity_3m_return`, `fx_3m_return` (both are used in `signal_config.yaml`: `equity_3m_return_rank +0.25`, `fx_3m_return_rank +0.20`). Signs already match momentum — **no weight changes**.
- **Stay mock** (need rate/fundamentals/BIS sources, out of scope here): `fx_carry`, `rate_3m_change`, `curve_slope_2s10s`, `equity_forward_pe`, `reit_3m_return`, `house_price_yoy`.

### Integration shape

`generate_snapshot` currently does: `load_macro_inputs → add_surprises → add_ranked_features → compute_deterministic_signals → build_snapshot`. We insert `overlay_market_inputs` right after `load_macro_inputs`, before `add_surprises`. It is a **no-op in mock mode** (so the committed mock snapshot is unchanged) and overlays the two columns in live mode with the same all-or-nothing rule as the macro overlay (a column only goes live when all six economies resolve; otherwise it stays mock). Market provenance is recorded as `yahoo:<YYYY-MM>` and appears in the snapshot automatically.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `data_sources/market.py` | **Create** | Fetch Yahoo monthly closes; compute 3-month returns; map economy → equity/fx returns. Injectable `fetch_json`, browser UA, 429 retry. |
| `tests/test_market.py` | **Create** | Offline unit tests for the adapter using a fake `fetch_json`. |
| `signal_engine.py` | **Modify** | Add `MARKET_LIVE_COLUMNS`, `overlay_market_inputs()`, import `market`; call it in `generate_snapshot`. |
| `tests/test_signal_engine.py` | **Modify** | Tests: mock no-op + live overlay with fakes. |
| `README.md` | **Modify** | Document live market coverage + the new adapter. |

---

### Task M1: Yahoo market adapter

**Files:**
- Create: `data_sources/market.py`
- Test: `tests/test_market.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_market.py`:

```python
from data_sources import market


def _chart_payload(timestamps, closes, with_adjclose=True):
    indicators = {"quote": [{"close": closes}]}
    if with_adjclose:
        indicators["adjclose"] = [{"adjclose": closes}]
    return {"chart": {"result": [{"timestamp": timestamps, "indicators": indicators}], "error": None}}


# Jan, Feb, Mar, Apr 2024 (UTC month starts)
TS = [1704067200, 1706745600, 1709251200, 1711929600]


def test_fetch_3m_return_computes_from_monthly_closes():
    payload = _chart_payload(TS, [100.0, 105.0, 108.0, 110.0])  # 110/100 - 1 = 10%
    captured = {}

    def fake(url):
        captured["url"] = url
        return payload

    ret, asof = market.fetch_3m_return("SPY", fetch_json=fake)
    assert ret == 10.0
    assert asof == "2024-04"
    assert "SPY" in captured["url"]


def test_fetch_3m_return_drops_nulls_and_needs_four_bars():
    payload = _chart_payload([1, 2, 3], [100.0, None, 103.0])  # only 2 usable bars
    assert market.fetch_3m_return("X", fetch_json=lambda u: payload) is None


def test_fetch_3m_return_falls_back_to_quote_close():
    payload = _chart_payload(TS, [10.0, 11.0, 12.0, 13.0], with_adjclose=False)
    ret, asof = market.fetch_3m_return("EZU", fetch_json=lambda u: payload)
    assert ret == 30.0  # 13/10 - 1
    assert asof == "2024-04"


def test_load_market_returns_assembles_equity_fx_and_us_numeraire():
    equity = _chart_payload(TS, [100.0, 105.0, 108.0, 110.0])  # +10%
    fx = _chart_payload(TS, [1.00, 1.02, 1.04, 1.05])          # +5%

    def fake(url):
        return fx if "=X" in url else equity

    economies = tuple(market.EQUITY_TICKER_BY_ECONOMY)  # all six
    out = market.load_market_returns(economies, fetch_json=fake)

    us = "United States of America"
    assert out[us]["equity_3m_return"] == (10.0, "2024-04")
    assert out[us]["fx_3m_return"] == (0.0, "2024-04")          # numeraire, dated to US equity
    assert out["Canada"]["equity_3m_return"][0] == 10.0
    assert out["Canada"]["fx_3m_return"][0] == 5.0
    assert out["Euro Area"]["fx_3m_return"][0] == 5.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_market.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'data_sources.market'`.

- [ ] **Step 3: Implement the adapter**

Create `data_sources/market.py`:

```python
"""Live market-return data from Yahoo Finance (free, no API key).

Sources trailing 3-month price returns for the signal universe from Yahoo's
public chart endpoint and shapes them to the engine's market columns:
- equity_3m_return : USD country-equity ETF return
- fx_3m_return     : local currency vs USD (positive = local appreciation);
                     the US is the numeraire, so its FX return is 0.0

We use Yahoo's chart JSON over httpx (not the `yfinance` package) to match the
World Bank / IMF adapters: an injectable ``fetch_json`` keeps tests offline.
NOTE: Yahoo requires a browser User-Agent (no-UA requests get HTTP 429) and
rate-limits bursts, so the default fetch sends a UA and retries on 429.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

import httpx

YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"

EQUITY_TICKER_BY_ECONOMY = {
    "United States of America": "SPY",
    "Canada": "EWC",
    "China": "MCHI",
    "Japan": "EWJ",
    "Brazil": "EWZ",
    "Euro Area": "EZU",
}

# US is the numeraire (no pair); every other economy quotes local-per-USD.
FX_TICKER_BY_ECONOMY = {
    "Canada": "CADUSD=X",
    "China": "CNYUSD=X",
    "Japan": "JPYUSD=X",
    "Brazil": "BRLUSD=X",
    "Euro Area": "EURUSD=X",
}

US_ECONOMY = "United States of America"


def _default_fetch_json(url: str) -> dict:
    # Yahoo needs a browser UA (no-UA -> 429) and rate-limits bursts.
    headers = {"User-Agent": "Mozilla/5.0"}
    response = None
    for attempt in range(4):
        response = httpx.get(url, timeout=20.0, headers=headers, follow_redirects=True)
        if response.status_code == 200:
            return response.json()
        if response.status_code == 429:
            time.sleep(1.5 * (attempt + 1))
            continue
        response.raise_for_status()
    response.raise_for_status()
    return response.json()


def fetch_3m_return(
    ticker: str,
    fetch_json: Callable[[str], dict] = _default_fetch_json,
) -> tuple[float, str] | None:
    """Return (trailing 3-month return in %, 'YYYY-MM' of latest bar) or None.

    Computed from monthly closes as close[-1] / close[-4] - 1.
    """
    url = f"{YAHOO_BASE}/{ticker}?range=1y&interval=1mo"
    payload = fetch_json(url)
    try:
        result = payload["chart"]["result"][0]
        timestamps = result["timestamp"]
        indicators = result["indicators"]
    except (TypeError, KeyError, IndexError):
        return None
    adjclose = indicators.get("adjclose", [{}])[0].get("adjclose")
    closes = adjclose if adjclose is not None else indicators["quote"][0]["close"]
    pairs = [(t, c) for t, c in zip(timestamps, closes) if c is not None]
    if len(pairs) < 4:
        return None
    asof = datetime.fromtimestamp(pairs[-1][0], tz=timezone.utc).strftime("%Y-%m")
    pct = (pairs[-1][1] / pairs[-4][1] - 1.0) * 100.0
    return round(pct, 4), asof


def load_market_returns(
    economies: tuple[str, ...],
    fetch_json: Callable[[str], dict] = _default_fetch_json,
) -> dict[str, dict[str, tuple[float, str]]]:
    """Return {economy: {column: (return_pct, asof)}} for live market columns.

    equity_3m_return for every economy with an ETF; fx_3m_return for every
    non-US economy with a pair, plus the US numeraire at 0.0 (dated to the US
    equity bar). Economies/columns with no data are omitted (caller falls back).
    """
    out: dict[str, dict[str, tuple[float, str]]] = {economy: {} for economy in economies}

    for economy in economies:
        ticker = EQUITY_TICKER_BY_ECONOMY.get(economy)
        if ticker is not None:
            result = fetch_3m_return(ticker, fetch_json=fetch_json)
            if result is not None:
                out[economy]["equity_3m_return"] = result

    for economy in economies:
        ticker = FX_TICKER_BY_ECONOMY.get(economy)
        if ticker is not None:
            result = fetch_3m_return(ticker, fetch_json=fetch_json)
            if result is not None:
                out[economy]["fx_3m_return"] = result

    # US is the FX numeraire: 0.0, dated to its own equity bar when available.
    if US_ECONOMY in economies and "equity_3m_return" in out.get(US_ECONOMY, {}):
        out[US_ECONOMY]["fx_3m_return"] = (0.0, out[US_ECONOMY]["equity_3m_return"][1])

    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_market.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add data_sources/market.py tests/test_market.py
git commit -m "feat(data): add Yahoo market-return adapter"
```

---

### Task M2: Overlay market data in live mode

**Files:**
- Modify: `signal_engine.py` (add import + `MARKET_LIVE_COLUMNS` + `overlay_market_inputs`; wire into `generate_snapshot`)
- Test: `tests/test_signal_engine.py`

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_signal_engine.py` (the file already imports `signal_engine as se` at line 89):

```python
from data_sources import market as market_mod


def _market_chart(closes):
    ts = [1704067200, 1706745600, 1709251200, 1711929600]  # Jan-Apr 2024 UTC
    return {"chart": {"result": [{
        "timestamp": ts,
        "indicators": {"adjclose": [{"adjclose": closes}], "quote": [{"close": closes}]},
    }], "error": None}}


def _market_fake_all(url):
    # FX pairs (".=X") -> +5% ; equity ETFs -> +10%
    return _market_chart([1.00, 1.02, 1.04, 1.05]) if "=X" in url else _market_chart([100.0, 105.0, 108.0, 110.0])


def test_overlay_market_inputs_mock_is_noop():
    df, provenance, _ec = se.load_macro_inputs(source="mock")
    before = df["equity_3m_return"].tolist()
    se.overlay_market_inputs(df, provenance, source="mock")
    assert df["equity_3m_return"].tolist() == before
    assert "equity_3m_return" not in provenance["United States of America"]


def test_overlay_market_inputs_live_overlays_fx_and_equity():
    df, provenance, _ec = se.load_macro_inputs(source="mock")
    se.overlay_market_inputs(df, provenance, source="live", fetch_json=_market_fake_all)
    usa = "United States of America"
    assert df.loc[usa, "equity_3m_return"] == 10.0
    assert df.loc[usa, "fx_3m_return"] == 0.0            # numeraire
    assert df.loc["Canada", "fx_3m_return"] == 5.0
    assert df.loc["Euro Area", "equity_3m_return"] == 10.0
    assert provenance[usa]["equity_3m_return"] == "yahoo:2024-04"
    assert provenance[usa]["fx_3m_return"] == "yahoo:2024-04"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_signal_engine.py -k overlay_market -q`
Expected: FAIL — `AttributeError: module 'signal_engine' has no attribute 'overlay_market_inputs'`.

- [ ] **Step 3: Add the import**

In `signal_engine.py`, after the line `from data_sources import imf_weo`, add:

```python
from data_sources import market
```

- [ ] **Step 4: Add `MARKET_LIVE_COLUMNS` and `overlay_market_inputs`**

In `signal_engine.py`, immediately **after** the `load_macro_inputs` function (just before `SURPRISE_SPECS`), add:

```python
MARKET_LIVE_COLUMNS = ("fx_3m_return", "equity_3m_return")


def overlay_market_inputs(
    df: pd.DataFrame,
    provenance: dict[str, dict[str, str]],
    source: str = "mock",
    fetch_json=None,
) -> pd.DataFrame:
    """Overlay live Yahoo 3-month FX and equity returns onto the frame.

    Mock mode is a no-op (the bundled CSV values stand). In live mode each
    market column is overlaid all-or-nothing: only when every economy resolves
    a value does the column go live (else it keeps its mock value). Provenance
    for overlaid cells is recorded as ``yahoo:<YYYY-MM>``. Mutates df/provenance
    and returns df.
    """
    if source != "live":
        return df

    kwargs = {} if fetch_json is None else {"fetch_json": fetch_json}
    returns = market.load_market_returns(tuple(df.index), **kwargs)

    for column in MARKET_LIVE_COLUMNS:
        resolved: dict[str, tuple[float, str]] = {}
        for economy in df.index:
            value = returns.get(economy, {}).get(column)
            if value is None:
                break
            resolved[economy] = value
        if len(resolved) != len(df.index):
            continue  # not fully live -> keep mock for this column
        for economy, (return_pct, asof) in resolved.items():
            df.loc[economy, column] = return_pct
            provenance[economy][column] = f"yahoo:{asof}"
    return df
```

- [ ] **Step 5: Wire it into `generate_snapshot`**

In `signal_engine.py` `generate_snapshot`, replace:

```python
    df, provenance, expected_change_columns = load_macro_inputs(source=source)
    df = add_surprises(df, expected_change_columns)
```

with:

```python
    df, provenance, expected_change_columns = load_macro_inputs(source=source)
    df = overlay_market_inputs(df, provenance, source=source)
    df = add_surprises(df, expected_change_columns)
```

- [ ] **Step 6: Run the targeted tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_signal_engine.py -k overlay_market -q`
Expected: PASS (2 passed).

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all tests; mock path unchanged because the overlay is a no-op in mock mode).

- [ ] **Step 7: Commit**

```bash
git add signal_engine.py tests/test_signal_engine.py
git commit -m "feat(signal): overlay live Yahoo FX/equity returns in live mode"
```

---

### Task M3: Live smoke test against real Yahoo

**Files:** none (manual verification only)

- [ ] **Step 1: Generate a live snapshot against the real APIs**

Run: `.venv/bin/python signal_engine.py --source live`
Expected: prints `Wrote snapshot.json (source=live)`. (Requires network. Yahoo and the World Bank both rate-limit/stall intermittently — if it errors with 429 or a read timeout, re-run after a few seconds.)

- [ ] **Step 2: Confirm the snapshot used live market data**

Run:

```bash
.venv/bin/python -c "import json; s=json.load(open('snapshot.json')); p=s['economies']['Canada']['provenance']; print('fx_3m_return =', p.get('fx_3m_return')); print('equity_3m_return =', p.get('equity_3m_return'))"
```

Expected: both print `yahoo:<YYYY-MM>` (e.g. `yahoo:2026-06`). If they print `None`, a column fell back to mock — check the run output for a failed Yahoo fetch and re-run.

- [ ] **Step 3: Restore the committed mock snapshot**

Run: `.venv/bin/python signal_engine.py`
Expected: `Wrote snapshot.json (source=mock)`.

---

### Task M4: Verify mock snapshot is unchanged

**Files:**
- Modify (only if `as_of` changed): `snapshot.json`

- [ ] **Step 1: Regenerate and diff**

Run: `.venv/bin/python signal_engine.py && git diff --stat snapshot.json`
Expected: either no diff, or a one-line `as_of` date change only. **Signal values must not change** — this feature is a no-op in mock mode. If signal values differ, stop and investigate.

- [ ] **Step 2: Commit only if there is an `as_of`-only diff (else skip)**

```bash
git add snapshot.json
git commit -m "chore: regenerate mock snapshot (as_of refresh)"
```

---

### Task M5: Document live market coverage

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the adapter to Architecture**

In `README.md`, after the `data_sources/imf_weo.py` bullet, add:

```text
- `data_sources/market.py`: live market-return adapter (Yahoo Finance chart API, no key); sources `equity_3m_return` and `fx_3m_return` in `--source live`
```

- [ ] **Step 2: Update the first Current Limitations bullet**

In `README.md` replace:

```text
- Macro inputs (inflation, GDP growth, unemployment) can be sourced live from the World Bank API; market, consensus, real estate, and PMI remain mock. Each value's origin is recorded in `snapshot.json` under `provenance`.
```

with:

```text
- Live mode sources macro (inflation, GDP growth, unemployment) from the World Bank, consensus from IMF WEO, and the `equity_3m_return` / `fx_3m_return` market columns from Yahoo Finance. The remaining market columns (`fx_carry`, `rate_3m_change`, `curve_slope_2s10s`, `equity_forward_pe`, `reit_3m_return`, `house_price_yoy`), policy rate, and PMI remain mock. Each value's origin is recorded in `snapshot.json` under `provenance`.
```

- [ ] **Step 3: Update the TODO list**

In `README.md` replace:

```text
- Add live market data (yfinance) and real estate (BIS)
```

with:

```text
- Extend live market data to carry, rates/curve, forward P/E, REIT, and real estate (BIS)
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document live Yahoo market data (equity/FX 3m returns)"
```

---

## Final verification

- [ ] **Full suite green:** `.venv/bin/python -m pytest -q` → all pass (current 41 + new market/overlay tests).
- [ ] **Mock determinism:** `.venv/bin/python signal_engine.py && git diff snapshot.json` → only `as_of` may differ; signal values identical.
- [ ] **Live path:** `.venv/bin/python signal_engine.py --source live` writes a snapshot whose `equity_3m_return`/`fx_3m_return` provenance is `yahoo:<YYYY-MM>` (network required; re-run on 429/timeout).

---

## Self-Review notes (already applied)

- **Spec coverage:** live `equity_3m_return` + `fx_3m_return` from Yahoo = Tasks M1 (adapter) + M2 (overlay/wiring); smoke test M3; mock-stability M4; docs M5. Out-of-scope columns explicitly listed and left mock.
- **No placeholders:** every code/test step has complete code; every run step has an exact command + expected result.
- **Type/name consistency:** `fetch_3m_return(ticker, fetch_json=...) -> (float, str) | None` and `load_market_returns(economies, fetch_json=...) -> {economy: {column: (float, str)}}` are used identically in `market.py`, `overlay_market_inputs`, and both test files. `overlay_market_inputs(df, provenance, source=, fetch_json=)` matches its call in `generate_snapshot` (no `fetch_json` passed there → real Yahoo) and in the tests (fake injected). `MARKET_LIVE_COLUMNS` defined once and iterated in the overlay.
- **Independence/safety:** the overlay is a no-op in mock mode, so the committed mock snapshot and all existing mock tests are unaffected; the all-or-nothing rule mirrors the macro overlay so a Yahoo outage degrades to mock rather than erroring.
