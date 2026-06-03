# Macro Regime Detection (V0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic macro regime-detection engine (regime score, narrative gap, cross-asset confirmation, templated expressions/risks) for six economies, served at `/api/regime` and shown in a new Regime tab.

**Architecture:** `regime_engine.py` mirrors `signal_engine.py` (load mock CSV → validate → score → assemble → write `regime_snapshot.json`). `main.py` adds `/api/regime` mirroring `/api/signals`. `static/index.html` gains a third tab (quadrant + card on top, ranking table below). Pure/deterministic; no LLM or real feeds.

**Tech Stack:** Python 3 (pandas, PyYAML), FastAPI, pytest, httpx (TestClient); vanilla JS + D3 v7 frontend. Verification: pytest for backend; preview MCP tools for frontend.

---

## Conventions (read first)

- Interpreter is `.venv/bin/python` (`python` is not on PATH). Tests: `.venv/bin/python -m pytest`.
- `tests/conftest.py` already puts the repo root on `sys.path`, so `import regime_engine` works.
- Backend data/config files are read relative to CWD (repo root), matching `signal_engine.py`.
- **Frontend verification uses the running uvicorn preview server on port 8123.** After editing `main.py`, **restart** the preview server (it has no auto-reload). After editing `static/index.html`, just reload the browser. Start/restart via the preview tool (`preview_start macromind`) or `.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8123`.
- Reuse existing frontend helpers: `color()`, `fmt()`, `escapeHtml()`, `signalVerdict()`, `verdictBadgeHtml()`, and CSS classes `eyebrow/panel-title/meta/metric-grid/metric/section-title`.

---

## Task 1: Regime engine scaffolding — data files, loaders, verdict

**Files:**
- Create: `regime_engine.py`, `data/mock_regime.csv`, `regime_config.yaml`, `regime_templates.yaml`
- Test: `tests/test_regime_engine.py`

- [ ] **Step 1: Create `data/mock_regime.csv`** with exactly:

```csv
country,policy,liquidity,foreign_access,rating_momentum,index_catalyst,narrative_score,equity,sovereign_credit,fx,cds,etf_flows,local_banks,rates
Argentina,0.85,0.45,0.6,0.75,0.4,0.05,0.8,0.6,0.2,0.5,0.3,0.9,-0.1
Greece,0.6,0.55,0.7,0.8,0.7,0.5,0.7,0.7,0.3,0.6,0.5,0.6,0.2
Turkey,0.3,0.2,0.25,0.4,0.1,0.1,0.3,0.4,0.1,0.3,0.2,0.3,0.0
Japan,0.4,0.3,0.45,0.2,0.3,0.45,0.5,0.3,-0.3,0.2,0.4,0.3,0.2
China,-0.4,-0.2,-0.35,-0.3,-0.2,-0.15,-0.4,-0.2,-0.1,-0.3,-0.5,-0.4,-0.1
Brazil,0.3,0.25,0.2,0.3,0.1,0.25,0.4,0.3,0.2,0.2,0.3,0.3,0.1
```

- [ ] **Step 2: Create `regime_config.yaml`** with exactly:

```yaml
regime_weights:
  policy: 0.30
  liquidity: 0.20
  foreign_access: 0.20
  rating_momentum: 0.20
  index_catalyst: 0.10

verdict:
  deteriorating_max: -0.10
  repricing_gap: 0.30
  active_min: 0.10
```

- [ ] **Step 3: Create `regime_templates.yaml`** with exactly:

```yaml
Argentina:
  drivers: ["Fiscal adjustment continuing", "IMF support improving external funding", "FX framework less distorted", "Reserve accumulation still fragile"]
  best_expressions: ["Long sovereign USD bonds", "Long local banks / ADR basket", "Long energy exporters (YPF)", "Avoid unhedged local FX"]
  left_tail_risks: ["FX gap widens", "IMF review delay", "Election shock", "Reserve target miss", "Social unrest"]
Greece:
  drivers: ["Fiscal credibility restored", "Bank balance sheets normalized", "Index reclassification catalyst", "Sovereign spread compression"]
  best_expressions: ["Long Greek bank equities", "Long GGB sovereign vs Bund", "Long Greece equity ETF", "RV: Greece vs periphery"]
  left_tail_risks: ["EU growth slowdown", "Political slippage", "Banking-sector wobble", "Tourism shock"]
Turkey:
  drivers: ["Return to orthodox monetary policy", "Real rates turning positive", "Reserves rebuilding", "Disinflation starting"]
  best_expressions: ["Long TRY carry (hedged)", "Long Turkish banks", "Receive TRY rates as inflation falls", "Long equity ETF"]
  left_tail_risks: ["Policy U-turn", "FX reserve adequacy", "Inflation re-acceleration", "Geopolitical shock"]
Japan:
  drivers: ["BoJ policy normalization", "Sustained wage growth", "Corporate governance reform", "Yen undervaluation"]
  best_expressions: ["Long Japan banks", "Long Nikkei (FX-hedged)", "Pay JGB rates", "Long value vs growth"]
  left_tail_risks: ["Global risk-off / yen surge", "BoJ delays normalization", "Wage momentum fades", "China demand shock"]
China:
  drivers: ["Property-sector drag", "Soft domestic confidence", "Deflationary pressure", "Stimulus underwhelming"]
  best_expressions: ["Underweight China equities", "Long defensive HK names", "Short CNH (hedged)", "Avoid property beta"]
  left_tail_risks: ["Large-scale stimulus surprise", "Policy bazooka", "Export rebound", "Valuation snapback"]
Brazil:
  drivers: ["High real rates", "Disinflation underway", "Fiscal uncertainty lingering", "Commodity support"]
  best_expressions: ["Receive BRL rates", "Long Brazilian equities", "Long BRL carry (hedged)", "Long commodity exporters"]
  left_tail_risks: ["Fiscal slippage", "Inflation surprise", "Commodity downturn", "Political noise"]
```

- [ ] **Step 4: Write the failing test** — create `tests/test_regime_engine.py`:

```python
import pytest

import regime_engine as re_eng

EXPECTED_UNIVERSE = ["Argentina", "Greece", "Turkey", "Japan", "China", "Brazil"]


def test_regime_verdict_ladder():
    th = {"deteriorating_max": -0.10, "repricing_gap": 0.30, "active_min": 0.10}
    assert re_eng.regime_verdict(-0.5, 0.0, th) == "Deteriorating"
    assert re_eng.regime_verdict(0.6, 0.4, th) == "Repricing"
    assert re_eng.regime_verdict(0.5, 0.15, th) == "Early"
    assert re_eng.regime_verdict(0.5, 0.0, th) == "Priced in"
    assert re_eng.regime_verdict(0.05, 0.0, th) == "Neutral"


def test_load_regime_inputs_has_six_countries():
    df = re_eng.load_regime_inputs()
    assert list(df.index) == EXPECTED_UNIVERSE
    assert not df.isna().any().any()


def test_load_regime_config_has_weights_and_thresholds():
    cfg = re_eng.load_regime_config()
    assert set(cfg["regime_weights"]) >= set(re_eng.STRUCTURAL_BUCKETS)
    assert {"deteriorating_max", "repricing_gap", "active_min"} <= set(cfg["verdict"])


def test_load_regime_templates_covers_universe():
    tpl = re_eng.load_regime_templates()
    for country in EXPECTED_UNIVERSE:
        assert {"drivers", "best_expressions", "left_tail_risks"} <= set(tpl[country])
```

- [ ] **Step 5: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_regime_engine.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'regime_engine'`).

- [ ] **Step 6: Create `regime_engine.py`** with:

```python
"""Generate a deterministic macro regime-detection snapshot.

Mirrors signal_engine.py: read mock per-country regime inputs, apply
YAML-configured weights, compute regime / narrative-gap / cross-asset
confirmation scores, attach curated templates, and write a versioned
snapshot consumed by the FastAPI app and the dashboard's Regime tab.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

REGIME_SNAPSHOT_PATH = Path("regime_snapshot.json")
REGIME_CONFIG_PATH = Path("regime_config.yaml")
REGIME_TEMPLATES_PATH = Path("regime_templates.yaml")
REGIME_DATA_PATH = Path("data/mock_regime.csv")
METHODOLOGY_VERSION = "v0.1"

REGIME_UNIVERSE = ("Argentina", "Greece", "Turkey", "Japan", "China", "Brazil")
STRUCTURAL_BUCKETS = ("policy", "liquidity", "foreign_access", "rating_momentum", "index_catalyst")
CROSS_ASSET_CHANNELS = ("equity", "sovereign_credit", "fx", "cds", "etf_flows", "local_banks", "rates")
REQUIRED_REGIME_COLUMNS = {"country", "narrative_score", *STRUCTURAL_BUCKETS, *CROSS_ASSET_CHANNELS}


def clip_unit(value: float) -> float:
    return float(max(-1.0, min(1.0, value)))


def regime_verdict(regime_score: float, narrative_gap: float, thresholds: dict) -> str:
    if regime_score <= thresholds["deteriorating_max"]:
        return "Deteriorating"
    if narrative_gap >= thresholds["repricing_gap"]:
        return "Repricing"
    active = thresholds["active_min"]
    if regime_score >= active and narrative_gap >= active:
        return "Early"
    if regime_score >= active:
        return "Priced in"
    return "Neutral"


def load_regime_config(path: Path = REGIME_CONFIG_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Missing regime config: {path}")
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError(f"Malformed regime config {path}: expected a YAML object")
    weights = config.get("regime_weights")
    verdict = config.get("verdict")
    if not isinstance(weights, dict) or not weights:
        raise ValueError(f"Malformed regime config {path}: missing regime_weights")
    for bucket in STRUCTURAL_BUCKETS:
        if not isinstance(weights.get(bucket), (int, float)):
            raise ValueError(f"Malformed regime config {path}: weight {bucket} must be numeric")
    if not isinstance(verdict, dict):
        raise ValueError(f"Malformed regime config {path}: missing verdict thresholds")
    for key in ("deteriorating_max", "repricing_gap", "active_min"):
        if not isinstance(verdict.get(key), (int, float)):
            raise ValueError(f"Malformed regime config {path}: verdict.{key} must be numeric")
    return config


def load_regime_templates(path: Path = REGIME_TEMPLATES_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Missing regime templates: {path}")
    templates = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(templates, dict):
        raise ValueError(f"Malformed regime templates {path}: expected a YAML object")
    for country in REGIME_UNIVERSE:
        entry = templates.get(country)
        if not isinstance(entry, dict):
            raise ValueError(f"Malformed regime templates {path}: missing {country}")
        for key in ("drivers", "best_expressions", "left_tail_risks"):
            if not isinstance(entry.get(key), list) or not entry[key]:
                raise ValueError(f"Malformed regime templates {path}: {country}.{key} must be a non-empty list")
    return templates


def load_regime_inputs(path: Path = REGIME_DATA_PATH) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing_columns = sorted(REQUIRED_REGIME_COLUMNS - set(df.columns))
    if missing_columns:
        raise ValueError(f"{path} is missing required columns: {missing_columns}")
    duplicates = df["country"][df["country"].duplicated()].tolist()
    if duplicates:
        raise ValueError(f"{path} has duplicate countries: {duplicates}")
    missing_countries = sorted(set(REGIME_UNIVERSE) - set(df["country"]))
    if missing_countries:
        raise ValueError(f"{path} is missing required countries: {missing_countries}")
    numeric_columns = sorted(REQUIRED_REGIME_COLUMNS - {"country"})
    if df[numeric_columns].isna().any().any():
        raise ValueError(f"{path} contains missing values in required columns")
    return df.set_index("country").loc[list(REGIME_UNIVERSE)]
```

- [ ] **Step 7: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_regime_engine.py -v`
Expected: PASS (4 tests).

- [ ] **Step 8: Commit**

```bash
git add regime_engine.py data/mock_regime.csv regime_config.yaml regime_templates.yaml tests/test_regime_engine.py
git commit -m "feat(regime): add regime engine scaffolding, mock data, loaders, verdict

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Compute regime scores

**Files:**
- Modify: `regime_engine.py`
- Test: `tests/test_regime_engine.py`

- [ ] **Step 1: Append the failing test**

```python
def test_compute_regime_scores_argentina():
    cfg = re_eng.load_regime_config()
    df = re_eng.load_regime_inputs()
    scores = re_eng.compute_regime_scores(df, cfg)
    arg = scores["Argentina"]
    assert arg["regime_score"] == pytest.approx(0.655, abs=1e-4)
    assert arg["narrative_gap"] == pytest.approx(0.605, abs=1e-4)
    assert arg["verdict"] == "Repricing"
    assert set(arg["buckets"]) == set(re_eng.STRUCTURAL_BUCKETS)
    assert set(arg["cross_asset_confirmation"]) == set(re_eng.CROSS_ASSET_CHANNELS)


def test_compute_regime_scores_china_deteriorating():
    cfg = re_eng.load_regime_config()
    df = re_eng.load_regime_inputs()
    scores = re_eng.compute_regime_scores(df, cfg)
    assert scores["China"]["verdict"] == "Deteriorating"
    assert -1.0 <= scores["China"]["confirmation_score"] <= 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_regime_engine.py -k compute_regime_scores -v`
Expected: FAIL (`AttributeError: ... 'compute_regime_scores'`).

- [ ] **Step 3: Add `compute_regime_scores`** to `regime_engine.py` (after `load_regime_inputs`):

```python
def compute_regime_scores(df: pd.DataFrame, config: dict) -> dict:
    weights = config["regime_weights"]
    thresholds = config["verdict"]
    results: dict[str, dict] = {}
    for country, row in df.iterrows():
        regime_score = clip_unit(sum(float(weights[b]) * float(row[b]) for b in STRUCTURAL_BUCKETS))
        narrative_score = float(row["narrative_score"])
        narrative_gap = regime_score - narrative_score
        confirmation = sum(float(row[c]) for c in CROSS_ASSET_CHANNELS) / len(CROSS_ASSET_CHANNELS)
        results[country] = {
            "regime_score": round(regime_score, 4),
            "narrative_score": round(narrative_score, 4),
            "narrative_gap": round(narrative_gap, 4),
            "confirmation_score": round(confirmation, 4),
            "verdict": regime_verdict(regime_score, narrative_gap, thresholds),
            "buckets": {b: round(float(row[b]), 4) for b in STRUCTURAL_BUCKETS},
            "cross_asset_confirmation": {c: round(float(row[c]), 4) for c in CROSS_ASSET_CHANNELS},
        }
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_regime_engine.py -k compute_regime_scores -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add regime_engine.py tests/test_regime_engine.py
git commit -m "feat(regime): compute regime score, narrative gap, confirmation, verdict

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Build + generate the regime snapshot

**Files:**
- Modify: `regime_engine.py`
- Test: `tests/test_regime_engine.py`

- [ ] **Step 1: Append the failing tests**

```python
@pytest.fixture()
def snapshot(tmp_path):
    return re_eng.generate_regime_snapshot(tmp_path / "regime_snapshot.json", as_of="2026-06-03")


def test_top_level_schema(snapshot):
    assert set(snapshot) == {"as_of", "methodology_version", "regime_universe", "countries"}
    assert snapshot["regime_universe"] == EXPECTED_UNIVERSE
    assert list(snapshot["countries"]) == EXPECTED_UNIVERSE


def test_narrative_gap_identity(snapshot):
    for c in snapshot["countries"].values():
        assert c["narrative_gap"] == pytest.approx(c["regime_score"] - c["narrative_score"], abs=1e-4)


def test_templates_attached(snapshot):
    for c in snapshot["countries"].values():
        assert c["drivers"] and c["best_expressions"] and c["left_tail_risks"]


def test_deterministic(tmp_path):
    a = re_eng.generate_regime_snapshot(tmp_path / "a.json", as_of="2026-06-03")
    b = re_eng.generate_regime_snapshot(tmp_path / "b.json", as_of="2026-06-03")
    assert a == b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_regime_engine.py -k "schema or narrative_gap_identity or templates_attached or deterministic" -v`
Expected: FAIL (`AttributeError: ... 'generate_regime_snapshot'`).

- [ ] **Step 3: Add `build_regime_snapshot` + `generate_regime_snapshot` + `__main__`** to `regime_engine.py`:

```python
def build_regime_snapshot(df: pd.DataFrame, config: dict, templates: dict, as_of: str | None = None) -> dict:
    scores = compute_regime_scores(df, config)
    countries: dict[str, dict] = {}
    for country in df.index:
        entry = {"country": country, **scores[country]}
        tpl = templates[country]
        entry["drivers"] = list(tpl["drivers"])
        entry["best_expressions"] = list(tpl["best_expressions"])
        entry["left_tail_risks"] = list(tpl["left_tail_risks"])
        countries[country] = entry
    return {
        "as_of": as_of or date.today().isoformat(),
        "methodology_version": METHODOLOGY_VERSION,
        "regime_universe": list(REGIME_UNIVERSE),
        "countries": countries,
    }


def generate_regime_snapshot(path: Path = REGIME_SNAPSHOT_PATH, as_of: str | None = None) -> dict:
    config = load_regime_config()
    templates = load_regime_templates()
    df = load_regime_inputs()
    snapshot = build_regime_snapshot(df, config, templates, as_of=as_of)
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return snapshot


if __name__ == "__main__":
    generate_regime_snapshot()
    print(f"Wrote {REGIME_SNAPSHOT_PATH}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_regime_engine.py -v`
Expected: all PASS (10 tests).

- [ ] **Step 5: Generate the committed snapshot artifact**

Run: `.venv/bin/python regime_engine.py`
Expected: prints `Wrote regime_snapshot.json`. Confirm the file has top-level keys `as_of/methodology_version/regime_universe/countries` and 6 countries.

- [ ] **Step 6: Commit**

```bash
git add regime_engine.py tests/test_regime_engine.py regime_snapshot.json
git commit -m "feat(regime): build and generate regime snapshot

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: `/api/regime` endpoint

**Files:**
- Modify: `main.py`
- Test: `tests/test_regime_api.py`

- [ ] **Step 1: Write the failing test** — create `tests/test_regime_api.py`:

```python
from fastapi.testclient import TestClient

import main


def test_api_regime_returns_snapshot():
    client = TestClient(main.app)
    resp = client.get("/api/regime")
    assert resp.status_code == 200
    data = resp.json()
    assert {"as_of", "methodology_version", "regime_universe", "countries"} <= set(data)
    assert len(data["countries"]) == 6
    argentina = data["countries"]["Argentina"]
    assert argentina["verdict"] == "Repricing"
    assert argentina["best_expressions"]
    assert set(argentina["cross_asset_confirmation"])  # non-empty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_regime_api.py -v`
Expected: FAIL (404 — route not defined yet).

- [ ] **Step 3: Add the import and endpoint to `main.py`**

Change:
```python
from signal_engine import SNAPSHOT_PATH, generate_snapshot
```
to:
```python
from signal_engine import SNAPSHOT_PATH, generate_snapshot
from regime_engine import REGIME_SNAPSHOT_PATH, generate_regime_snapshot
```

Then add this endpoint immediately after the existing `get_signals` function:
```python
@app.get("/api/regime")
def get_regime():
    path = Path(REGIME_SNAPSHOT_PATH)
    if not path.exists():
        try:
            generate_regime_snapshot(path)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"regime_snapshot.json is missing and could not be generated: {exc}",
            ) from exc
    return FileResponse(path, media_type="application/json")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_regime_api.py -v`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all PASS (existing 18 + new regime tests).

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_regime_api.py
git commit -m "feat(regime): serve regime snapshot at /api/regime

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Frontend — Regime tab scaffold, card, and ranking table

**Files:**
- Modify: `static/index.html`

- [ ] **Step 1: Add Regime tab CSS**

In `<style>`, after the `.heatmap-legend { ... }` rule, add:

```css
    main.regime-full { grid-template-columns: 1fr; }
    #regime-view { min-height: calc(100vh - 125px); }
    .regime-top { display: grid; grid-template-columns: 1.4fr 1fr; gap: 18px; align-items: start; }
    .regime-quadrant-wrap, .regime-card {
      border: 1px solid var(--line); border-radius: 8px;
      background: var(--panel); padding: 16px; box-shadow: var(--shadow);
    }
    #regime-quadrant-svg { width: 100%; height: 340px; display: block; }
    .regime-card .rg-list { margin: 6px 0 4px; padding-left: 18px; font-size: 12px; color: var(--muted); line-height: 1.5; }
    .ca-table { border: 1px solid var(--line); border-radius: 7px; overflow: hidden; margin-top: 6px; }
    .ca-row { display: flex; justify-content: space-between; padding: 5px 9px; font-size: 12px; border-bottom: 1px solid var(--line); }
    .ca-row:last-child { border-bottom: none; }
    #regime-table {
      margin-top: 18px; border: 1px solid var(--line); border-radius: 8px;
      background: var(--panel); padding: 16px; box-shadow: var(--shadow);
    }
    .rg-table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 13px; }
    .rg-table th, .rg-table td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); }
    .rg-table th { font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); }
    .rg-table tbody tr { cursor: pointer; }
    .rg-table tbody tr:hover { background: rgba(127,127,127,0.08); }
    .rg-table tbody tr.selected-row { box-shadow: inset 0 0 0 1px var(--active); }
    @media (max-width: 900px) { .regime-top { grid-template-columns: 1fr; } }
```

- [ ] **Step 2: Add the Regime tab button**

In the `.view-tabs` div, change:
```html
        <button data-tab="heatmap">Heatmap</button>
```
to:
```html
        <button data-tab="heatmap">Heatmap</button>
        <button data-tab="regime">Regime</button>
```

- [ ] **Step 3: Add the `#regime-view` markup**

In `<main>`'s `<section>`, immediately after the `<div id="heatmap-view" hidden> ... </div>` block, add:
```html
      <div id="regime-view" hidden>
        <div class="regime-top">
          <div class="regime-quadrant-wrap">
            <div class="section-title">Narrative-gap map</div>
            <svg id="regime-quadrant-svg" aria-label="Narrative gap quadrant"></svg>
            <div class="meta">Below the dashed line = hard data ahead of narrative (repricing opportunity).</div>
          </div>
          <aside id="regime-card" class="regime-card"></aside>
        </div>
        <div id="regime-table"></div>
      </div>
```

- [ ] **Step 4: Extend `setTab` to handle the regime tab**

Replace the entire existing `setTab` function with:
```javascript
    function setTab(tab) {
      selectedTab = tab;
      document.querySelector("#map-view").hidden = tab !== "map";
      document.querySelector("#heatmap-view").hidden = tab !== "heatmap";
      document.querySelector("#regime-view").hidden = tab !== "regime";
      document.querySelector("#asset-toggles").hidden = tab !== "map";
      document.querySelector("#panel").hidden = tab === "regime";
      document.querySelector("main").classList.toggle("regime-full", tab === "regime");
      document.querySelectorAll("button[data-tab]").forEach(b =>
        b.classList.toggle("active", b.dataset.tab === tab));
      if (tab === "map") draw();
      if (tab === "heatmap") drawHeatmap();
      if (tab === "regime") renderRegime();
    }
```

- [ ] **Step 5: Add regime state + render functions**

Immediately after the `setTab` function, add (note: `drawRegimeQuadrant` is a stub here, implemented in Task 6):
```javascript
    let regimeData = null;
    let regimeSelected = null;

    function renderRegime() {
      if (regimeData) { drawRegimeViews(); return; }
      d3.json("/api/regime").then(data => {
        regimeData = data;
        regimeSelected = (data.regime_universe || [])[0] || null;
        drawRegimeViews();
      }).catch(error => {
        document.querySelector("#regime-view").innerHTML =
          `<div class="meta">Regime data failed to load: ${escapeHtml(String(error))}</div>`;
      });
    }

    function drawRegimeViews() {
      drawRegimeQuadrant();
      renderRegimeTable();
      renderRegimeCard(regimeSelected);
    }

    function drawRegimeQuadrant() {
      // populated in Task 6
    }

    function highlightRegime() {
      d3.selectAll("#regime-quadrant-svg g.rg-dot circle")
        .attr("stroke-width", d => d.country === regimeSelected ? 2.5 : 0.5);
      document.querySelectorAll("#regime-table tr[data-country]").forEach(tr => {
        tr.classList.toggle("selected-row", tr.dataset.country === regimeSelected);
      });
    }

    function renderRegimeCard(country) {
      const c = regimeData && regimeData.countries[country];
      const card = document.querySelector("#regime-card");
      if (!c) { card.innerHTML = `<div class="meta">Select a country.</div>`; return; }
      const rowsHtml = obj => Object.entries(obj).map(([k, v]) =>
        `<div class="ca-row"><span>${escapeHtml(k.replace(/_/g, " "))}</span>`
        + `<span style="color:${color(v)}">${fmt(v)}</span></div>`).join("");
      const channels = rowsHtml(c.cross_asset_confirmation);
      const buckets = rowsHtml(c.buckets);
      const list = arr => arr.map(s => `<li>${escapeHtml(s)}</li>`).join("");
      card.innerHTML = `
        <div class="eyebrow">Regime card</div>
        <div class="panel-title">${escapeHtml(c.country)} ${verdictBadgeHtml(c.regime_score)}</div>
        <div class="meta">Verdict: <strong>${escapeHtml(c.verdict)}</strong></div>
        <div class="metric-grid">
          <div class="metric"><label>Regime</label><strong>${fmt(c.regime_score)}</strong></div>
          <div class="metric"><label>Narrative gap</label><strong>${fmt(c.narrative_gap)}</strong></div>
          <div class="metric"><label>Confirmation</label><strong>${fmt(c.confirmation_score)}</strong></div>
        </div>
        <div class="section-title">Structural buckets</div>
        <div class="ca-table">${buckets}</div>
        <div class="section-title">Drivers</div><ul class="rg-list">${list(c.drivers)}</ul>
        <div class="section-title">Best expressions</div><ul class="rg-list">${list(c.best_expressions)}</ul>
        <div class="section-title">Left-tail risks</div><ul class="rg-list">${list(c.left_tail_risks)}</ul>
        <div class="section-title">Cross-asset confirmation</div>
        <div class="ca-table">${channels}</div>
      `;
    }

    function renderRegimeTable() {
      const rows = regimeData.regime_universe
        .map(c => regimeData.countries[c])
        .slice()
        .sort((a, b) => b.narrative_gap - a.narrative_gap);
      const body = rows.map(c => `
        <tr data-country="${escapeHtml(c.country)}">
          <td>${escapeHtml(c.country)}</td>
          <td style="color:${color(c.regime_score)}">${fmt(c.regime_score)}</td>
          <td style="color:${color(c.narrative_gap)}">${fmt(c.narrative_gap)}</td>
          <td style="color:${color(c.confirmation_score)}">${fmt(c.confirmation_score)}</td>
          <td>${escapeHtml(c.verdict)}</td>
        </tr>`).join("");
      document.querySelector("#regime-table").innerHTML = `
        <div class="section-title">Regime ranking (sorted by narrative gap)</div>
        <table class="rg-table">
          <thead><tr><th>Country</th><th>Regime</th><th>Narrative gap</th><th>Confirmation</th><th>Verdict</th></tr></thead>
          <tbody>${body}</tbody>
        </table>`;
      document.querySelectorAll("#regime-table tr[data-country]").forEach(tr => {
        tr.addEventListener("click", () => {
          regimeSelected = tr.dataset.country;
          renderRegimeCard(regimeSelected);
          highlightRegime();
          document.querySelector("#regime-card").scrollIntoView({ behavior: "smooth", block: "start" });
        });
      });
      highlightRegime();
    }
```

- [ ] **Step 6: Expose `regimeCountry` on the debug hook**

Change:
```javascript
      get selectedTab() { return selectedTab; },
```
to:
```javascript
      get selectedTab() { return selectedTab; },
      get regimeCountry() { return regimeSelected; },
```

- [ ] **Step 7: Restart the preview server (main.py changed in Task 4) and verify the card + table**

Restart the server (`preview_start macromind`), reload the page, then `preview_eval`:
```javascript
(() => { document.querySelector('button[data-tab="regime"]').click(); return new Promise(r => setTimeout(() => {
  r({ tab: window.macroDashboardDebug.selectedTab,
      panelHidden: document.querySelector('#panel').hidden,
      rows: document.querySelectorAll('#regime-table tr[data-country]').length,
      cardTitle: (document.querySelector('#regime-card .panel-title')||{}).textContent }); }, 400)); })()
```
Expected: `tab:"regime"`, `panelHidden:true`, `rows:6`, `cardTitle` = a country name (the first in the universe, "Argentina").

- [ ] **Step 8: Verify a table row click updates the card**

`preview_eval`:
```javascript
(() => { document.querySelector('#regime-table tr[data-country="China"]').click();
  return { card: document.querySelector('#regime-card .panel-title').textContent,
           selected: window.macroDashboardDebug.regimeCountry }; })()
```
Expected: card text starts with `China`, `selected: "China"`.

- [ ] **Step 9: Commit**

```bash
git add static/index.html
git commit -m "feat(ui): add Regime tab with regime card and ranking table

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Frontend — narrative-gap quadrant

**Files:**
- Modify: `static/index.html`

- [ ] **Step 1: Implement `drawRegimeQuadrant`**

Replace the stub:
```javascript
    function drawRegimeQuadrant() {
      // populated in Task 6
    }
```
with:
```javascript
    function drawRegimeQuadrant() {
      const svg = d3.select("#regime-quadrant-svg");
      const node = svg.node();
      const W = node.clientWidth || 460;
      const H = node.clientHeight || 340;
      const m = 36;
      svg.attr("viewBox", [0, 0, W, H]).selectAll("*").remove();

      const x = d3.scaleLinear().domain([-1, 1]).range([m, W - m]);
      const y = d3.scaleLinear().domain([-1, 1]).range([H - m, m]);

      // Opportunity zone: regime_score > narrative_score (below the y=x diagonal).
      svg.append("path")
        .attr("d", `M${x(-1)},${y(-1)} L${x(1)},${y(1)} L${x(1)},${y(-1)} Z`)
        .attr("fill", "var(--positive)").attr("opacity", 0.12);
      svg.append("line")
        .attr("x1", x(-1)).attr("y1", y(-1)).attr("x2", x(1)).attr("y2", y(1))
        .attr("stroke", "var(--line)").attr("stroke-dasharray", "4 4");
      svg.append("line").attr("x1", x(-1)).attr("y1", y(0)).attr("x2", x(1)).attr("y2", y(0)).attr("stroke", "var(--line)");
      svg.append("line").attr("x1", x(0)).attr("y1", y(-1)).attr("x2", x(0)).attr("y2", y(1)).attr("stroke", "var(--line)");

      const countries = regimeData.regime_universe.map(c => regimeData.countries[c]);
      const g = svg.selectAll("g.rg-dot").data(countries).enter().append("g")
        .attr("class", "rg-dot").style("cursor", "pointer")
        .attr("transform", d => `translate(${x(d.regime_score)},${y(d.narrative_score)})`)
        .on("click", (event, d) => { regimeSelected = d.country; renderRegimeCard(d.country); highlightRegime(); });
      g.append("circle").attr("r", 6).attr("fill", d => color(d.regime_score))
        .attr("stroke", "var(--text)").attr("stroke-width", 0.5);
      g.append("text").attr("x", 9).attr("dy", "0.32em").attr("font-size", 11)
        .attr("fill", "var(--text)").text(d => d.country);

      svg.append("text").attr("x", W - m).attr("y", H - 10).attr("text-anchor", "end")
        .attr("font-size", 10).attr("fill", "var(--muted)").text("regime / data score →");
      svg.append("text").attr("x", 8).attr("y", m - 12)
        .attr("font-size", 10).attr("fill", "var(--muted)").text("↑ narrative re-rated");

      highlightRegime();
    }
```

- [ ] **Step 2: Verify the quadrant renders and dots are clickable**

Reload the page, then `preview_eval`:
```javascript
(() => { document.querySelector('button[data-tab="regime"]').click();
  return new Promise(r => setTimeout(() => {
    const dots = document.querySelectorAll('#regime-quadrant-svg g.rg-dot');
    dots[0].dispatchEvent(new MouseEvent('click', {bubbles:true, view:window}));
    r({ dotCount: dots.length,
        labels: [...document.querySelectorAll('#regime-quadrant-svg g.rg-dot text')].map(t=>t.textContent),
        cardAfterClick: document.querySelector('#regime-card .panel-title').textContent }); }, 400)); })()
```
Expected: `dotCount: 6`, `labels` = the six country names, `cardAfterClick` = the clicked country's name.

- [ ] **Step 3: Screenshot the Regime tab** (resize to 1280×800 first). Confirm: quadrant with 6 labeled dots and a shaded lower-right opportunity zone; regime card on the right with verdict badge, metrics, drivers/expressions/risks lists, and the cross-asset table; ranking table below on scroll.

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "feat(ui): add narrative-gap quadrant to Regime tab

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Docs + final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document the regime module in `README.md`**

Under `## Architecture`, after the `data_sources/world_bank.py` bullet, add:
```markdown
- `regime_engine.py`: deterministic macro **regime-detection** engine (regime score, narrative gap, cross-asset confirmation, templated expressions/risks) for a separate six-economy set; writes `regime_snapshot.json`, served at `/api/regime` and shown in the dashboard's Regime tab
```

Under `## Run`, inside the existing run command block (the one with `python signal_engine.py`), add one line after the signal-engine line:
`python regime_engine.py   # rebuild regime_snapshot.json (regenerates on demand too)`

- [ ] **Step 2: Full backend suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all PASS (18 prior + regime tests).

- [ ] **Step 3: Full frontend sweep** (server running, page reloaded), `preview_eval`:
```javascript
(() => { const tabs=['map','heatmap','regime'];
  document.querySelector('button[data-tab="regime"]').click();
  return new Promise(r => setTimeout(() => {
    r({ dots: document.querySelectorAll('#regime-quadrant-svg g.rg-dot').length,
        rows: document.querySelectorAll('#regime-table tr[data-country]').length,
        verdictBadges: document.querySelectorAll('#regime-card .verdict-badge').length,
        mapStillWorks: (document.querySelector('button[data-tab="map"]').click(), document.querySelectorAll('path.country').length) }); }, 400)); })()
```
Expected: `dots:6`, `rows:6`, `verdictBadges:>=1`, `mapStillWorks:177`.

- [ ] **Step 4: Screenshots** of the Regime tab (quadrant + card, and the ranking table on scroll) at 1280×800.

- [ ] **Step 5: Confirm clean tree + commit docs**

```bash
git add README.md
git commit -m "docs: document regime-detection module and /api/regime

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
git status --short
```
Expected: working tree clean after commit (regime_snapshot.json already committed in Task 3; if it shows a date-only `as_of` change, restore it with `git checkout -- regime_snapshot.json`).

---

## Out of scope (V0)
- Real data feeds, LLM-generated narrative/expressions, agent orchestration, backtesting.
- Reusing the signal `snapshot.json` for cross-asset confirmation of overlapping countries.
- Persisting the selected tab across reloads.
