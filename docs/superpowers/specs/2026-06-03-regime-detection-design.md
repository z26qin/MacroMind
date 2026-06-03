# Macro Regime Detection (V0) — Design

**Date:** 2026-06-03
**Status:** Approved (pending spec review)
**Scope:** New deterministic backend module + API endpoint + new frontend tab. No LLM, no real data feeds.

## Goal

Industrialize a macro PM's regime-change workflow as a deterministic, inspectable prototype. For a small set of economies, surface: (1) a **regime score** from structural buckets, (2) a **narrative gap** (hard data ahead of the market narrative = repricing opportunity), (3) **cross-asset confirmation**, and (4) curated **trade expressions** and **left-tail risks**. The headline thesis: *detect when data has moved to a new regime while the narrative is still anchored to the old one.*

This is **Version 0**: deterministic scores from mock inputs, mirroring `signal_engine.py`. Expressions and risks are curated templates (not AI-generated). Later stages (real data adapters, LLM narrative ingestion, agent orchestration, backtesting) are out of scope.

## Decisions (from brainstorming)

- **Realism:** deterministic V0; mock inputs; templated expressions/risks; no LLM/real feeds.
- **Universe (decoupled from the map):** Argentina, Greece, Turkey, Japan, China, Brazil.
- **`narrative_gap = regime_score − narrative_score`** (positive = data ahead of narrative).
- **Cross-asset confirmation is self-contained mock** in V0 (the regime universe only partly overlaps the signal universe; reusing snapshot signals for the overlap is a future enhancement).
- **Frontend Regime tab layout:** one scrollable page — top = narrative-gap quadrant (left) + regime card (right); bottom = full-width ranking table (no second card). A dot click or row click updates the shared top card.

## Architecture

`regime_engine.py` is a structural twin of `signal_engine.py`: load mock CSV → validate → compute deterministic scores → assemble → write `regime_snapshot.json`. `main.py` gains `/api/regime` mirroring `/api/signals` (serve the JSON, regenerate if missing). The frontend adds a third top-level tab to `static/index.html`. All units are pure/deterministic and independently testable.

### File structure

**Create:**
- `regime_engine.py` — load/validate/score/assemble/generate. One responsibility: turn regime inputs into the regime snapshot.
- `data/mock_regime.csv` — mock per-country inputs.
- `regime_config.yaml` — structural-bucket weights + verdict thresholds.
- `regime_templates.yaml` — per-country `drivers`, `best_expressions`, `left_tail_risks`.
- `regime_snapshot.json` — generated artifact (committed, like `snapshot.json`).
- `tests/test_regime_engine.py` — pytest suite.

**Modify:**
- `main.py` — add `/api/regime` endpoint + `REGIME_SNAPSHOT_PATH` import.
- `static/index.html` — add Regime tab (CSS, markup, JS).
- `README.md` — document the regime module.

## Backend — inputs

`data/mock_regime.csv`, one row per country, every value in `[-1, +1]`:

| Column group | Columns |
|---|---|
| Structural buckets | `policy`, `liquidity`, `foreign_access`, `rating_momentum`, `index_catalyst` |
| Narrative | `narrative_score` |
| Cross-asset channels | `equity`, `sovereign_credit`, `fx`, `cds`, `etf_flows`, `local_banks`, `rates` |

Plus a `country` key column. Validation mirrors `signal_engine.validate_input_frame`: required columns present, no duplicate/missing countries vs the regime universe, no NaNs, values numeric.

`regime_config.yaml`:
```yaml
regime_weights:        # structural buckets -> regime_score (sum = 1.0)
  policy: 0.30
  liquidity: 0.20
  foreign_access: 0.20
  rating_momentum: 0.20
  index_catalyst: 0.10
verdict:
  deteriorating_max: -0.10   # regime_score <= this -> Deteriorating
  repricing_gap: 0.30        # narrative_gap >= this -> Repricing
  active_min: 0.10           # regime_score / gap threshold for Early vs Priced-in
```

`regime_templates.yaml` — per country:
```yaml
Argentina:
  drivers: ["Fiscal adjustment continuing", "IMF support improving external funding", "FX framework less distorted", "Reserve accumulation still fragile"]
  best_expressions: ["Long sovereign USD bonds", "Long local banks / ADR basket", "Long energy exporters (YPF)", "Avoid unhedged local FX"]
  left_tail_risks: ["FX gap widens", "IMF review delay", "Election shock", "Reserve target miss", "Social unrest"]
# ... Greece, Turkey, Japan, China, Brazil
```

## Backend — computed values

- `regime_score = clip(Σ weight_b · bucket_b, -1, 1)` over the 5 structural buckets.
- `narrative_gap = regime_score − narrative_score`.
- `confirmation_score = mean(cross-asset channels)`.
- `verdict` via an explicit ladder:
  ```
  if regime_score <= deteriorating_max: "Deteriorating"
  elif narrative_gap >= repricing_gap:  "Repricing"
  elif regime_score >= active_min and narrative_gap >= active_min: "Early"
  elif regime_score >= active_min:       "Priced in"
  else:                                  "Neutral"
  ```
- `drivers`, `best_expressions`, `left_tail_risks` copied from `regime_templates.yaml`.

## Output schema (`/api/regime` → `regime_snapshot.json`)

```json
{
  "as_of": "2026-06-03",
  "methodology_version": "v0.1",
  "regime_universe": ["Argentina","Greece","Turkey","Japan","China","Brazil"],
  "countries": {
    "Argentina": {
      "country": "Argentina",
      "regime_score": 0.72, "narrative_score": 0.07, "narrative_gap": 0.65,
      "confirmation_score": 0.46, "verdict": "Repricing",
      "buckets": {"policy":0.8,"liquidity":0.5,"foreign_access":0.6,"rating_momentum":0.7,"index_catalyst":0.4},
      "drivers": ["Fiscal adjustment continuing", "..."],
      "best_expressions": ["Long sovereign USD bonds", "..."],
      "left_tail_risks": ["FX gap widens", "..."],
      "cross_asset_confirmation": {"equity":0.8,"sovereign_credit":0.6,"fx":0.2,"cds":0.5,"etf_flows":0.3,"local_banks":0.9,"rates":-0.1}
    }
  }
}
```
All numeric scores rounded to 4 dp. `countries` is keyed and ordered by `regime_universe`.

## API

- `GET /api/regime` — returns `regime_snapshot.json`; if missing, generate it (mirror `/api/signals` fallback + 500-on-failure handling).
- Generation also available via `python regime_engine.py` (writes `regime_snapshot.json`).

## Frontend — Regime tab

- Add `Regime` to the existing view tabs (`Map | Heatmap | Regime`); reuse the `setTab` machinery (hide asset toggles on Regime, like Heatmap). The shared right-hand detail `<aside>` is **not** used by Regime — the Regime tab has its own internal card.
- `#regime-view` is a scrollable column:
  - **Top row** (grid, 2 columns): `#regime-quadrant` (SVG scatter) + `#regime-card`.
  - **Bottom**: `#regime-table` (full width).
- **Quadrant:** SVG; x-axis = `regime_score` (−1…+1), y-axis = `narrative_score` (−1…+1); diagonal line `y=x`; shaded triangle below the diagonal (gap > 0) = opportunity zone. One labeled dot per country, filled via the existing `color(regime_score)`. Click a dot → `renderRegimeCard(country)`.
- **Card:** country name + `verdict`; `regime_score`, `narrative_gap`, `confirmation_score` with reused `verdictBadgeHtml`; `buckets` mini-bars; `drivers`, `best_expressions`, `left_tail_risks` lists; a cross-asset confirmation table (channel → value, colored). All text via `escapeHtml`.
- **Table:** rows = countries sorted by `narrative_gap` desc; columns Country / Regime / Narrative gap / Confirmation / Verdict; colored cells reuse `color()`. Click a row → `renderRegimeCard(country)` and scroll the card into view.
- Data: fetch `/api/regime` lazily on first Regime-tab open; cache in memory. Render error → inline message in `#regime-view`.

## Error handling

- Missing `regime_snapshot.json` → endpoint regenerates; generation failure → HTTP 500 with detail (same shape as `/api/signals`).
- Frontend fetch failure → inline error block in the Regime tab; Map/Heatmap unaffected.
- Validation errors in `regime_engine` raise `ValueError` with the offending file/columns (mirrors `signal_engine`).

## Testing

- **Backend (`tests/test_regime_engine.py`, pytest):** stable top-level schema; all 6 countries present and ordered; `regime_score`/`narrative_score`/`narrative_gap`/`confirmation_score` in range and matching their formulas; `narrative_gap == regime_score − narrative_score`; verdict ladder for representative inputs; templates attached for every country; deterministic across two runs.
- **Frontend (preview eval + screenshots):** Regime tab shows 6 quadrant dots and 6 table rows; clicking a dot and a row both populate the card with the right country; quadrant opportunity zone renders; screenshots of the tab.

## Out of scope (V0)
- Real data feeds (IMF/EPFR/CFTC/ratings/CDS), LLM-generated narrative/expressions, multi-agent orchestration, backtesting.
- Reusing the signal `snapshot.json` for cross-asset confirmation of overlapping countries.
- Persisting the selected tab across reloads.
