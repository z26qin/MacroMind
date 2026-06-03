# Heatmap Tab + Bullish/Bearish Indicators — Design

**Date:** 2026-06-02
**Status:** Approved (pending spec review)
**Scope:** Frontend only — `static/index.html`. No backend, API, or Python changes.

## Goal

Make the dashboard communicate signal direction more clearly, and add a second way to view the data:

1. **Bullish/bearish verdict labels** — every signal value gains an explicit BULLISH / BEARISH / NEUTRAL word badge (not color alone), used on the map tooltip, the detail panel, and heatmap cells.
2. **Heatmap tab** — a new top-level view showing all asset classes × all economies at once, as a colored grid.

## Decisions (from brainstorming)

- **Color convention:** green = bullish, red = bearish, **consistent across map and heatmap**. The map's existing `d3.scaleLinear().domain([-1,0,1]).range(["#b94b4b","#e6e5da","#3d8b5a"])` already encodes this, so there is **no recolor** — the heatmap reuses the same `color()` function.
- **Heatmap orientation:** transposed — **rows = asset classes** (Composite, FX, Rates, Equity, Real estate), **columns = the six economies**.
- **Verdict treatment:** word **badge** (BULLISH / BEARISH / NEUTRAL) plus the signed number.

## Why no backend change

`snapshot.json` (served by `/api/signals`) already contains, for all six economies: `composite.final` and `signals.{fx,rates,equity,real_estate}.final`. Verified present for every economy/asset. The heatmap is a re-rendering of these existing numbers; the verdict is a pure client-side threshold. Nothing new is required from the engine or API.

## Architecture

Single-file vanilla JS + D3 app (`static/index.html`). New/changed units, each with one responsibility:

| Unit | Responsibility | Inputs | Output |
|---|---|---|---|
| `signalVerdict(value)` | Pure: map a signal to a verdict | number or null | `{label, glyph, cls}` |
| `verdictBadgeHtml(value)` | Render the badge markup | number or null | HTML string |
| View tabs (`selectedTab`) | Switch between Map and Heatmap | click | shows/hides `#map-view` / `#heatmap-view` |
| `drawHeatmap()` | Build the transposed grid | `economies`, `snapshot.universe` | DOM in `#heatmap` |
| `renderPanel()` (existing, extended) | Detail panel + badges | economy name | DOM in `#panel` |

### `signalVerdict(value)` — the core helper

```
if value == null            -> { label: "No data",  glyph: "",  cls: "neu" }
else if |value| < 0.10      -> { label: "Neutral",  glyph: "▬", cls: "neu" }
else if value >= 0.10       -> { label: "Bullish",  glyph: "▲", cls: "pos" }
else (value <= -0.10)       -> { label: "Bearish",  glyph: "▼", cls: "neg" }
```

Neutral band: `|value| < 0.10`. Exposed on `window.macroDashboardDebug.signalVerdict` for verification.

## Component detail

### Navigation / view tabs
- Add a tab control (`Map` | `Heatmap`) in the header; `selectedTab` defaults to `"map"`.
- `#map-view` wraps the current `.map-wrap` (SVG + legend) and the **asset toggles** (`Composite/FX/Rates/Equity/Real estate`). Asset toggles live with the Map view and are hidden when the Heatmap tab is active.
- `#heatmap-view` wraps the heatmap grid + its legend; hidden when the Map tab is active.
- The detail `<aside id="panel">` is shared and always visible.
- Switching tabs toggles visibility only; both views read the same in-memory `economies`/`snapshot`.

### Heatmap grid (`drawHeatmap`)
- Layout: CSS grid. First column = asset row-labels; remaining 6 columns = economies (in `snapshot.universe` order). Header row = economy short names (iso3) with full name on hover.
- Rows (top→bottom): Composite, FX, Rates, Equity, Real estate.
- Cell value = Composite → `economy.composite.final`; otherwise `economy.signals[asset].final`.
- Cell appearance: `background = color(value)`; text = `glyph + signed number` (e.g. `▲ +0.65`), via `d3.format("+.2f")`. `title`/tooltip shows the full verdict badge text + economy + asset.
- Cell interaction: click selects that economy (`selectedCountry = <economy>`), calls `renderPanel`, and highlights the clicked cell's column; the panel's matching asset card gets the existing `.highlight` treatment.
- Legend: reuse `.legend` / `.legend-scale` markup — gradient red→neutral→green, labels `Bearish −1` / `No data` / `Bullish +1`.
- Responsive: horizontal scroll wrapper so the grid never crushes on narrow screens.
- Re-render `drawHeatmap` after data load and whenever it becomes visible.

### Detail panel additions
- Next to the Composite **Final** metric and each asset card's **Final** value, render `verdictBadgeHtml(value)`.
- All dynamic text continues to pass through the existing `escapeHtml`.

### Map tooltip addition
- The existing map hover tooltip appends the verdict badge for the hovered economy under the current view.

## Data flow

`/api/signals` → `snapshot` (in memory) → `economies`. Both `draw()` (map) and `drawHeatmap()` read from `economies`. No new fetches, no new endpoints.

## Error / edge handling

- `value == null` (uncovered economy / missing) → "No data" verdict, neutral styling, `color` falls back to the existing `--nodata` gray (heatmap cells for covered economies always have data, so this mainly guards the map/tooltip path).
- Existing top-level load-error handler (`.catch` rendering "Dashboard failed to load") is unchanged and covers both views.
- Boundary values: exactly `±0.10` classify as Bullish/Bearish (band is strict `< 0.10`).

## Testing / verification

Consistent with the repo: Python is covered by `pytest`; the frontend has no JS test runner, only the `window.macroDashboardDebug` hook. Verify via the preview/eval tool, no new toolchain:

1. `signalVerdict` returns correct labels for representative values: `0.5→Bullish`, `-0.5→Bearish`, `0.05→Neutral`, `0.10→Bullish`, `-0.10→Bearish`, `null→No data`.
2. Heatmap renders exactly 5 asset rows × 6 economy columns = 30 value cells; cell signs match the snapshot.
3. Tab switching: Map tab shows the SVG + asset toggles and hides the heatmap; Heatmap tab does the reverse; panel stays visible in both.
4. Clicking a heatmap cell populates the panel for that economy (panel title = economy name, 4 asset cards present).
5. Verdict badges appear in the panel and tooltip.
6. Screenshots of both tabs at desktop width.

## Out of scope

- Backend/API/Python changes.
- Showing data provenance (`world_bank:<year>` vs `mock`) in the UI — separate future enhancement.
- Persisting the selected tab across reloads.
- Any change to the signal math, thresholds aside (the 0.10 neutral band is a display-only choice).
