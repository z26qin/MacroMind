# Historical Time-Series Snapshots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the per-economy signal history that the daily CI commits have been accumulating in git, via a new `/api/history` endpoint and a sparkline in the dashboard detail panel.

**Architecture:** A new `history.py` reads every committed version of `snapshot.json` from git history (one per day) and reshapes them into a per-economy, per-view time series. `main.py` serves it at `/api/history`, computed on demand (no cached file — git history is the source of truth and updates daily). The frontend fetches it once and draws a small inline-SVG sparkline of the selected economy's selected-view `final` over time.

**Tech Stack:** Python 3.14, FastAPI, pytest (no new deps — git is invoked via `subprocess`); vanilla JS + inline SVG in `static/index.html`.

---

## Context & Verified Facts (read before starting)

Confirmed on 2026-06-12 — use as-is:

- **Git already holds a daily series:** `git log -- snapshot.json` → **11 commits**, one per day from 2026-06-02 onward (the CI workflow `refresh.yml` runs `python signal_engine.py --source live` daily and commits if changed; its commit message says "mock" but the data is live). So the history is real and grows automatically.
- **Each historical `snapshot.json`** has `as_of` and `economies[<name>].composite.final` plus `economies[<name>].signals[<asset>].final`. Old/early commits may lack some keys — the extractor must use `.get()` defensively and skip `None`.
- **Decision — compute on demand, do not cache a file.** Unlike `snapshot.json`/`regime_snapshot.json` (generated artifacts), history is derived from git itself and would go stale if cached once. With ~11 (growing slowly) commits, recomputing per request is sub-second. So `/api/history` returns a freshly built `JSONResponse`; there is **no `history.json` file** and no `.gitignore` change.
- **Requires running inside a git checkout** (the app already does; `git rev-parse --is-inside-work-tree` → true). Document this as a limitation.
- **Views match the existing toggle** (`button[data-view]` values): `composite`, `fx`, `rates`, `equity`, `real_estate`. `signalValue(entry, view)` (static/index.html:730) maps `composite` → `entry.composite.final`, else `entry.signals[view].final` — the history shape mirrors this.
- **Frontend hooks (exact):** globals at `static/index.html:682-687` (`selectedView`, `selectedCountry`, `snapshot`, `economies`); boot `Promise.all([...])` at `:1107-1118`; `renderPanel(countryName)` at `:780-832` (insert the sparkline after the `selected-signal` block, ~`:821`); the view toggle re-calls `renderPanel(selectedCountry)` at `:1099`, so the sparkline follows the selected view for free.
- **CSS-variables-in-SVG caveat (already hit in the Guide tab):** SVG presentation attributes like `stroke="var(--x)"` do **not** resolve CSS variables — use CSS classes/`style=""`. The plan uses classes.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `history.py` | **Create** | Read snapshot.json versions from git; reshape into per-economy/per-view series. Injectable `run` for offline tests. |
| `tests/test_history.py` | **Create** | Unit tests for `build_history` (pure) and `load_snapshots_from_git` (fake `run`). |
| `main.py` | **Modify** | Add `GET /api/history` → `JSONResponse(compute_history())`. |
| `tests/test_history_api.py` | **Create** | Structural API test against the real repo git history. |
| `static/index.html` | **Modify** | Fetch `/api/history`; draw a sparkline in the detail panel. |
| `README.md` | **Modify** | Document the endpoint, the sparkline, and the git-checkout requirement. |

---

### Task H1: History extractor (`history.py`)

**Files:**
- Create: `history.py`
- Test: `tests/test_history.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_history.py`:

```python
import history


def _snap(as_of, econ_vals):
    """Build a minimal snapshot dict. econ_vals: {economy: {view: final}}."""
    economies = {}
    for econ, vals in econ_vals.items():
        economies[econ] = {
            "composite": {"final": vals.get("composite")},
            "signals": {v: {"final": vals[v]} for v in ("fx", "rates", "equity", "real_estate") if v in vals},
        }
    return {"as_of": as_of, "economies": economies}


def test_build_history_dedups_by_date_keeping_last_and_orders_ascending():
    snaps = [
        _snap("2026-06-02", {"United States of America": {"composite": 0.10, "fx": 0.20}}),
        _snap("2026-06-02", {"United States of America": {"composite": 0.15, "fx": 0.25}}),  # same date, later wins
        _snap("2026-06-03", {"United States of America": {"composite": 0.30, "fx": 0.40}}),
    ]
    out = history.build_history(snaps)
    assert out["as_of"] == "2026-06-03"
    assert out["views"] == ["composite", "fx", "rates", "equity", "real_estate"]
    assert out["history"]["United States of America"]["composite"] == [
        {"date": "2026-06-02", "value": 0.15},
        {"date": "2026-06-03", "value": 0.30},
    ]


def test_build_history_skips_none_and_missing_views():
    out = history.build_history([_snap("2026-06-02", {"Japan": {"composite": None, "fx": 0.5}})])
    japan = out["history"]["Japan"]
    assert "composite" not in japan                 # None is dropped
    assert japan["fx"] == [{"date": "2026-06-02", "value": 0.5}]


def test_build_history_tolerates_malformed_snapshots():
    out = history.build_history([{"foo": "bar"}, {"as_of": "2026-06-02"}])  # no economies / no as_of
    assert out["history"] == {}
    assert out["as_of"] == "2026-06-02"


def test_load_snapshots_from_git_reads_blobs_in_log_order():
    calls = []

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["git", "log"]:
            return "aaa\nbbb\n"
        if args[1] == "show":
            sha = args[2].split(":")[0]
            return '{"as_of": "2026-06-02"}' if sha == "aaa" else '{"as_of": "2026-06-03"}'
        raise AssertionError(args)

    snaps = history.load_snapshots_from_git("snapshot.json", run=fake_run)
    assert [s["as_of"] for s in snaps] == ["2026-06-02", "2026-06-03"]
    assert calls[0][:2] == ["git", "log"]
    assert "--reverse" in calls[0]


def test_load_snapshots_from_git_skips_unparseable_blobs():
    def fake_run(args):
        if args[:2] == ["git", "log"]:
            return "aaa\nbbb\n"
        return "not json" if args[2].startswith("aaa") else '{"as_of": "2026-06-03"}'

    snaps = history.load_snapshots_from_git("snapshot.json", run=fake_run)
    assert [s["as_of"] for s in snaps] == ["2026-06-03"]


def test_compute_history_wires_git_and_build():
    def fake_run(args):
        if args[:2] == ["git", "log"]:
            return "aaa\n"
        return '{"as_of": "2026-06-02", "economies": {"Brazil": {"composite": {"final": 0.2}, "signals": {}}}}'

    out = history.compute_history("snapshot.json", run=fake_run)
    assert out["history"]["Brazil"]["composite"] == [{"date": "2026-06-02", "value": 0.2}]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_history.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'history'`.

- [ ] **Step 3: Implement `history.py`**

Create `history.py`:

```python
"""Build a per-economy signal time series from git history of snapshot.json.

The daily CI job commits a fresh live snapshot.json; this module walks those
commits and reshapes each into {economy: {view: [{date, value}, ...]}} so the
dashboard can draw sparklines. Git is reached through an injectable ``run``
callable so tests stay offline; the API computes the series on demand (git
history is the source of truth, so nothing is cached to disk).
"""
from __future__ import annotations

import json
import subprocess
from typing import Callable

VIEWS = ("composite", "fx", "rates", "equity", "real_estate")


def _finals(entry: dict) -> dict:
    signals = entry.get("signals") or {}
    composite = entry.get("composite") or {}
    out = {"composite": composite.get("final")}
    for view in ("fx", "rates", "equity", "real_estate"):
        out[view] = (signals.get(view) or {}).get("final")
    return out


def build_history(snapshots: list[dict]) -> dict:
    """Reshape chronologically-ordered snapshot dicts into a per-economy series.

    Snapshots sharing an ``as_of`` collapse to the last one seen (latest commit
    for that day). Missing or None finals are dropped.
    """
    by_date: dict[str, dict[str, dict]] = {}
    for snap in snapshots:
        as_of = snap.get("as_of")
        if not isinstance(as_of, str):
            continue
        by_date[as_of] = {
            economy: _finals(entry)
            for economy, entry in (snap.get("economies") or {}).items()
        }

    dates = sorted(by_date)
    series: dict[str, dict[str, list]] = {}
    for date in dates:
        for economy, finals in by_date[date].items():
            for view in VIEWS:
                value = finals.get(view)
                if value is None:
                    continue
                series.setdefault(economy, {}).setdefault(view, []).append(
                    {"date": date, "value": value}
                )

    return {
        "as_of": dates[-1] if dates else None,
        "views": list(VIEWS),
        "history": series,
    }


def _default_run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def load_snapshots_from_git(
    path: str = "snapshot.json",
    run: Callable[[list[str]], str] = _default_run,
) -> list[dict]:
    """Return every committed version of ``path`` (oldest first) as parsed dicts."""
    log = run(["git", "log", "--reverse", "--pretty=%H", "--", path])
    snapshots: list[dict] = []
    for sha in log.split():
        try:
            blob = run(["git", "show", f"{sha}:{path}"])
        except Exception:
            continue
        try:
            snapshots.append(json.loads(blob))
        except json.JSONDecodeError:
            continue
    return snapshots


def compute_history(
    snapshot_path: str = "snapshot.json",
    run: Callable[[list[str]], str] = _default_run,
) -> dict:
    return build_history(load_snapshots_from_git(snapshot_path, run=run))


if __name__ == "__main__":
    print(json.dumps(compute_history(), indent=2))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_history.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add history.py tests/test_history.py
git commit -m "feat(history): extract per-economy signal series from git history"
```

---

### Task H2: `/api/history` endpoint

**Files:**
- Modify: `main.py`
- Test: `tests/test_history_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_history_api.py`:

```python
from fastapi.testclient import TestClient

import main


def test_api_history_returns_series():
    client = TestClient(main.app)
    resp = client.get("/api/history")
    assert resp.status_code == 200
    data = resp.json()
    assert {"as_of", "views", "history"} <= set(data)
    assert data["views"] == ["composite", "fx", "rates", "equity", "real_estate"]
    assert isinstance(data["history"], dict)
    # The repo's snapshot.json has many commits, so at least one economy has a
    # non-empty composite series.
    assert any("composite" in econ and econ["composite"] for econ in data["history"].values())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_history_api.py -q`
Expected: FAIL — 404 (route not defined), so the status assertion fails.

- [ ] **Step 3: Add the route to `main.py`**

In `main.py`, change the responses import:

```python
from fastapi.responses import FileResponse, RedirectResponse
```

to:

```python
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
```

Add the import near the other engine imports (after the `regime_engine` import):

```python
from history import compute_history
```

Add the route at the end of the file:

```python
@app.get("/api/history")
def get_history():
    try:
        return JSONResponse(compute_history())
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"signal history could not be computed: {exc}",
        ) from exc
```

- [ ] **Step 4: Run the test, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_history_api.py -q`
Expected: PASS.

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_history_api.py
git commit -m "feat(api): serve computed signal history at /api/history"
```

---

### Task H3: Sparkline in the detail panel

**Files:**
- Modify: `static/index.html`

- [ ] **Step 1: Add sparkline CSS**

In `static/index.html`, just after the `.driver-reason strong { ... }` rule, add:

```css
    .sparkline { display: block; width: 100%; height: 44px; margin: 4px 0 2px; }
    .sparkline polyline { fill: none; stroke-width: 1.6; }
    .spark-pos { stroke: var(--positive); }
    .spark-neg { stroke: var(--negative); }
    .sparkline circle.spark-pos { fill: var(--positive); }
    .sparkline circle.spark-neg { fill: var(--negative); }
    .spark-zero { stroke: var(--line); stroke-width: 1; stroke-dasharray: 3 3; }
```

- [ ] **Step 2: Add the `historyData` global and sparkline helpers**

In `static/index.html`, immediately after the line `let economies = {};` (`:687`), add:

```javascript
    let historyData = {};
```

Just before `function renderPanel(countryName) {` (`:780`), add these two helpers:

```javascript
    function sparkline(series) {
      if (!series || series.length < 2) return "";
      const w = 220, h = 44, pad = 4, n = series.length;
      const x = i => pad + (i * (w - 2 * pad)) / (n - 1);
      const y = v => pad + (1 - (Math.max(-1, Math.min(1, v)) + 1) / 2) * (h - 2 * pad);
      const points = series.map((d, i) => `${x(i).toFixed(1)},${y(d.value).toFixed(1)}`).join(" ");
      const last = series[n - 1].value;
      const cls = last >= 0 ? "spark-pos" : "spark-neg";
      const zeroY = y(0).toFixed(1);
      return `<svg class="sparkline" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-label="history sparkline">`
        + `<line class="spark-zero" x1="${pad}" y1="${zeroY}" x2="${w - pad}" y2="${zeroY}"></line>`
        + `<polyline class="${cls}" points="${points}"></polyline>`
        + `<circle class="${cls}" cx="${x(n - 1).toFixed(1)}" cy="${y(last).toFixed(1)}" r="2.4"></circle>`
        + `</svg>`;
    }

    function historyBlock(economyName, view) {
      const series = (historyData[economyName] || {})[view] || [];
      if (series.length < 2) return "";
      const first = series[0], last = series[series.length - 1];
      return `<div class="section-title">History · ${escapeHtml(String(series.length))} snapshots</div>`
        + sparkline(series)
        + `<div class="meta">${viewLabel(view)} final: ${fmt(first.value)} (${escapeHtml(first.date)}) &rarr; ${fmt(last.value)} (${escapeHtml(last.date)})</div>`;
    }
```

- [ ] **Step 3: Render the sparkline in the panel**

In `renderPanel`, inside the main `panel.innerHTML = ...` template, insert the history block right after the closing `</div>` of the `selected-signal` block. Replace:

```javascript
        <div class="selected-signal">
          ${viewLabel(selectedView)} signal ${verdictBadgeHtml(selectedValue)}
          <strong>${fmt(selectedValue)}</strong>
        </div>
        <div class="section-title">Composite</div>
```

with:

```javascript
        <div class="selected-signal">
          ${viewLabel(selectedView)} signal ${verdictBadgeHtml(selectedValue)}
          <strong>${fmt(selectedValue)}</strong>
        </div>
        ${historyBlock(economyName, selectedView)}
        <div class="section-title">Composite</div>
```

- [ ] **Step 4: Fetch history at boot**

In `static/index.html`, after the closing `});` of the main `Promise.all([...]).then(...).catch(...)` block (`:1118`), add:

```javascript
    d3.json("/api/history").then(data => {
      historyData = (data && data.history) || {};
      if (selectedCountry) renderPanel(selectedCountry);
    }).catch(() => { /* history is optional; dashboard works without it */ });
```

- [ ] **Step 5: Verify in the browser**

Ensure the server is running (`preview_start` / `uvicorn ... --port 8123`). Open the dashboard, click a covered country (e.g. **United States**). Confirm a "History · N snapshots" sparkline appears under the selected-signal line, with a dashed zero line and a dot at the latest point; toggle **FX / Rates / Equity** and confirm the sparkline switches to that view. Check console for errors (none expected). Capture a screenshot.

- [ ] **Step 6: Commit**

```bash
git add static/index.html
git commit -m "feat(ui): sparkline of signal history in the detail panel"
```

---

### Task H4: Document the history feature

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the module to Architecture**

In `README.md`, after the `data_sources/market.py` bullet, add:

```text
- `history.py`: builds a per-economy signal time series by reading every committed version of `snapshot.json` from git history; served at `/api/history` and drawn as a sparkline in the detail panel (requires running inside a git checkout)
```

- [ ] **Step 2: Resolve the TODO**

In `README.md` remove the line:

```text
- Add historical time series snapshots
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document /api/history and the signal sparkline"
```

---

## Final verification

- [ ] **Full suite green:** `.venv/bin/python -m pytest -q` → all pass (current 50 + new history tests).
- [ ] **Endpoint sane:** `.venv/bin/python -c "from fastapi.testclient import TestClient; import main; d=TestClient(main.app).get('/api/history').json(); print(d['as_of'], len(d['history']), 'economies')"` → a recent date and 6 economies.
- [ ] **Mock snapshot untouched:** this feature adds only a read path; `git status` shows no change to `snapshot.json`.

---

## Self-Review notes (already applied)

- **Spec coverage:** extractor = H1; endpoint = H2; sparkline UI = H3; docs = H4. The "compute on demand, no cached file" decision is realized by `JSONResponse(compute_history())` (H2) — there is no `history.json` and no `.gitignore` change, by design.
- **No placeholders:** every code/test step is complete; `build_history` defends against missing `economies`/`as_of`/finals (tested in `test_build_history_tolerates_malformed_snapshots`), which matters because the earliest committed snapshots predate some keys.
- **Type/name consistency:** `build_history(snapshots) -> {as_of, views, history}`, `load_snapshots_from_git(path, run=)`, and `compute_history(snapshot_path, run=)` are used identically across `history.py`, the tests, and `main.py`. The history shape `{economy: {view: [{date, value}]}}` matches the frontend reads `historyData[economyName][selectedView]` and the `VIEWS`/`data-view` values (`composite, fx, rates, equity, real_estate`). Sparkline colors use CSS classes (`.spark-pos/.spark-neg`), not SVG `var()` presentation attributes.
- **Graceful degradation:** the history fetch is a separate, catch-all'd request, so a git/compute failure leaves the rest of the dashboard fully working; the sparkline simply renders nothing when a series has < 2 points.
