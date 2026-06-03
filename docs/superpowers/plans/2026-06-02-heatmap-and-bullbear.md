# Heatmap Tab + Bullish/Bearish Indicators Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a transposed country×asset Heatmap tab and explicit BULLISH/BEARISH/NEUTRAL badges across the dashboard, frontend-only.

**Architecture:** Single-file vanilla-JS + D3 app (`static/index.html`). Add a pure `signalVerdict()` helper and `verdictBadgeHtml()` renderer; add Map|Heatmap top-level tabs; add `drawHeatmap()` that re-renders the existing in-memory `economies` data as a grid (rows = asset classes, columns = economies) reusing the existing `color()` scale; inject badges into the detail panel and map tooltip. No backend/API/Python changes — `snapshot.json` already carries `composite.final` and `signals[asset].final`.

**Tech Stack:** HTML/CSS, vanilla JS, D3 v7 (already loaded from CDN). Verification via the running uvicorn server + the preview MCP tools (`preview_eval`, `preview_screenshot`). No JS test runner is added.

---

## Verification model (read first)

This file has no unit-test harness. The project's `pytest` suite covers Python only; the frontend exposes a `window.macroDashboardDebug` hook for inspection. Each task is verified by:
1. Reloading the page on the running server (uvicorn serves `static/index.html` from disk on port **8123**; `StaticFiles` picks up edits, the browser must reload).
2. Running a `preview_eval` expression and checking its JSON return against the expected value.
3. For visual tasks, a `preview_screenshot`.

If the server is not running, start it first: `.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8123` (or the preview tool's `preview_start macromind`). Reload in eval with `location.reload()` then re-query on the next call.

**Current anchors in `static/index.html` (do not guess — these exist):**
- `const color = d3.scaleLinear().domain([-1, 0, 1]).range(["#b94b4b", "#e6e5da", "#3d8b5a"]).clamp(true);`
- `function escapeHtml(value) { ... }` and `function fmt(value) { return value == null ? "No data" : d3.format("+.2f")(value); }`
- `window.macroDashboardDebug = { ... resolveEconomyName };`
- `function renderPanel(countryName) { ... }` — builds panel; has a composite `metric-grid` and per-asset `.asset` cards.
- `function draw() { ... }` and the tooltip `mousemove` handler that sets `tooltip.innerHTML`.
- Header markup: `<div class="toggles" aria-label="Asset class"> <button class="active" data-view="composite">…</button> … </div>`.
- `<main>` contains `<section class="map-wrap">…</section>` and `<aside id="panel">…</aside>`.

---

## Task 1: Verdict helpers + badge styles

**Files:**
- Modify: `static/index.html` (add CSS to `<style>`; add JS helpers + debug hook)

- [ ] **Step 1: Add badge CSS**

In the `<style>` block, immediately after the `.legend-labels { ... }` rule, add:

```css
    .verdict-badge {
      display: inline-block;
      font-size: 10px;
      font-weight: 800;
      letter-spacing: 0.04em;
      padding: 2px 7px;
      border-radius: 999px;
      color: #fff;
      vertical-align: middle;
      white-space: nowrap;
    }
    .badge-pos { background: var(--positive); }
    .badge-neg { background: var(--negative); }
    .badge-neu { background: var(--muted); }
```

- [ ] **Step 2: Add the `signalVerdict` and `verdictBadgeHtml` functions**

In the `<script>`, immediately after the existing `function fmt(value) { ... }` function, add:

```javascript
    function signalVerdict(value) {
      if (value == null) return { label: "No data", glyph: "", cls: "neu" };
      if (Math.abs(value) < 0.10) return { label: "Neutral", glyph: "▬", cls: "neu" };
      if (value >= 0.10) return { label: "Bullish", glyph: "▲", cls: "pos" };
      return { label: "Bearish", glyph: "▼", cls: "neg" };
    }

    function verdictBadgeHtml(value) {
      const v = signalVerdict(value);
      const glyph = v.glyph ? v.glyph + " " : "";
      return `<span class="verdict-badge badge-${v.cls}">${glyph}${escapeHtml(v.label)}</span>`;
    }
```

- [ ] **Step 3: Expose `signalVerdict` on the debug hook**

In the `window.macroDashboardDebug = { ... }` object, add `signalVerdict` to the property list. Change the closing of that object literal from:

```javascript
      resolveEconomyName
    };
```
to:
```javascript
      resolveEconomyName,
      signalVerdict
    };
```

- [ ] **Step 4: Verify helper behavior**

Reload the page, then `preview_eval`:
```javascript
(() => { const f = window.macroDashboardDebug.signalVerdict;
  return [0.5,-0.5,0.05,0.10,-0.10,null].map(v => { const r=f(v); return r.label+"/"+r.cls+"/"+(r.glyph||"-"); }); })()
```
Expected: `["Bullish/pos/▲","Bearish/neg/▼","Neutral/neu/▬","Bullish/pos/▲","Bearish/neg/▼","No data/neu/-"]`

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "feat(ui): add bull/bear verdict helper and badge styles

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Map | Heatmap view tabs (structure + switching)

**Files:**
- Modify: `static/index.html` (header markup, main markup, tab CSS, view-switch JS)

- [ ] **Step 1: Add tab + heatmap CSS**

In `<style>`, after the `.toggles { ... }` rule, add:

```css
    .view-tabs { display: flex; gap: 8px; }
    .header-right { display: flex; flex-direction: column; gap: 10px; align-items: flex-end; }
    [hidden] { display: none !important; }

    .heatmap-wrap {
      min-height: calc(100vh - 125px);
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: var(--shadow);
      padding: 18px;
      overflow-x: auto;
    }
    .heatmap {
      display: grid;
      gap: 5px;
      min-width: 540px;
    }
    .hm-cell {
      padding: 11px 6px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 700;
      color: #fff;
      text-align: center;
      white-space: nowrap;
      cursor: pointer;
      font-variant-numeric: tabular-nums;
    }
    .hm-corner { background: transparent; }
    .hm-colhead {
      display: flex; align-items: center; justify-content: center;
      font-size: 11px; font-weight: 700; opacity: 0.7;
    }
    .hm-rowhead {
      display: flex; align-items: center;
      font-size: 12px; font-weight: 700; opacity: 0.85;
    }
    .hm-cell.selected-col { outline: 2px solid var(--active); outline-offset: -2px; }
    .heatmap-legend { margin-top: 14px; max-width: 320px; }
```

- [ ] **Step 2: Restructure the header to hold view tabs + asset toggles**

Replace the existing header's toggles block. Change:

```html
    <div class="toggles" aria-label="Asset class">
      <button class="active" data-view="composite">Composite</button>
      <button data-view="fx">FX</button>
      <button data-view="rates">Rates</button>
      <button data-view="equity">Equity</button>
      <button data-view="real_estate">Real estate</button>
    </div>
```
to:
```html
    <div class="header-right">
      <div class="view-tabs" aria-label="View">
        <button class="active" data-tab="map">Map</button>
        <button data-tab="heatmap">Heatmap</button>
      </div>
      <div class="toggles" id="asset-toggles" aria-label="Asset class">
        <button class="active" data-view="composite">Composite</button>
        <button data-view="fx">FX</button>
        <button data-view="rates">Rates</button>
        <button data-view="equity">Equity</button>
        <button data-view="real_estate">Real estate</button>
      </div>
    </div>
```

- [ ] **Step 3: Wrap the map in `#map-view` and add `#heatmap-view`**

In `<main>`, replace the `<section class="map-wrap"> ... </section>` block. Change:

```html
    <section class="map-wrap">
      <svg id="map" aria-label="World macro signal map"></svg>
      <div class="legend">
        <div class="legend-scale"></div>
        <div class="legend-labels"><span>Bearish -1</span><span>No data</span><span>Bullish +1</span></div>
      </div>
    </section>
```
to:
```html
    <section>
      <div id="map-view">
        <div class="map-wrap">
          <svg id="map" aria-label="World macro signal map"></svg>
          <div class="legend">
            <div class="legend-scale"></div>
            <div class="legend-labels"><span>Bearish -1</span><span>No data</span><span>Bullish +1</span></div>
          </div>
        </div>
      </div>
      <div id="heatmap-view" hidden>
        <div class="heatmap-wrap">
          <div id="heatmap" class="heatmap"></div>
          <div class="legend heatmap-legend">
            <div class="legend-scale"></div>
            <div class="legend-labels"><span>Bearish -1</span><span>No data</span><span>Bullish +1</span></div>
          </div>
        </div>
      </div>
    </section>
```

- [ ] **Step 4: Add `selectedTab` state + `setTab` + a stub `drawHeatmap`**

In `<script>`, after the line `let selectedView = "composite";`, add:
```javascript
    let selectedTab = "map";
```

After the existing `function draw() { ... }` (just before `function updateColors()`), add:
```javascript
    function drawHeatmap() {
      // populated in Task 3
    }

    function setTab(tab) {
      selectedTab = tab;
      document.querySelector("#map-view").hidden = tab !== "map";
      document.querySelector("#heatmap-view").hidden = tab !== "heatmap";
      document.querySelector("#asset-toggles").hidden = tab !== "map";
      document.querySelectorAll("button[data-tab]").forEach(b =>
        b.classList.toggle("active", b.dataset.tab === tab));
      if (tab === "map") draw();
      if (tab === "heatmap") drawHeatmap();
    }
```

- [ ] **Step 5: Wire the tab buttons and expose `selectedTab`**

After the existing `document.querySelectorAll("button[data-view]").forEach(...)` block, add:
```javascript
    document.querySelectorAll("button[data-tab]").forEach(button => {
      button.addEventListener("click", () => setTab(button.dataset.tab));
    });
```

In `window.macroDashboardDebug`, add a `selectedTab` getter. Change:
```javascript
      get selectedCountry() { return selectedCountry; },
```
to:
```javascript
      get selectedCountry() { return selectedCountry; },
      get selectedTab() { return selectedTab; },
```

- [ ] **Step 6: Verify tab switching**

Reload, then `preview_eval`:
```javascript
(() => { document.querySelector('button[data-tab="heatmap"]').click();
  const r1 = { tab: window.macroDashboardDebug.selectedTab,
    mapHidden: document.querySelector('#map-view').hidden,
    hmHidden: document.querySelector('#heatmap-view').hidden,
    togglesHidden: document.querySelector('#asset-toggles').hidden };
  document.querySelector('button[data-tab="map"]').click();
  const r2 = { tab: window.macroDashboardDebug.selectedTab,
    mapHidden: document.querySelector('#map-view').hidden,
    hmHidden: document.querySelector('#heatmap-view').hidden };
  return { r1, r2 }; })()
```
Expected: `r1 = {tab:"heatmap", mapHidden:true, hmHidden:false, togglesHidden:true}`, `r2 = {tab:"map", mapHidden:false, hmHidden:true}`.

- [ ] **Step 7: Commit**

```bash
git add static/index.html
git commit -m "feat(ui): add Map/Heatmap view tabs with shared detail panel

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Heatmap grid rendering + cell interaction

**Files:**
- Modify: `static/index.html` (replace the stub `drawHeatmap`, add helpers)

- [ ] **Step 1: Implement `drawHeatmap` and helpers**

Replace the stub:
```javascript
    function drawHeatmap() {
      // populated in Task 3
    }
```
with:
```javascript
    const HEATMAP_ROWS = [
      { key: "composite", label: "Composite" },
      { key: "fx", label: "FX" },
      { key: "rates", label: "Rates" },
      { key: "equity", label: "Equity" },
      { key: "real_estate", label: "Real estate" }
    ];

    function heatmapValue(entry, rowKey) {
      if (!entry) return null;
      return rowKey === "composite" ? entry.composite.final : entry.signals[rowKey].final;
    }

    function highlightHeatmapColumn(economy) {
      document.querySelectorAll("#heatmap .hm-cell").forEach(cell => {
        cell.classList.toggle("selected-col", !!economy && cell.dataset.economy === economy);
      });
    }

    function drawHeatmap() {
      const universe = snapshot.universe || [];
      const grid = document.querySelector("#heatmap");
      grid.style.gridTemplateColumns = `120px repeat(${universe.length}, minmax(64px, 1fr))`;

      let html = `<div class="hm-corner"></div>`;
      for (const economy of universe) {
        const iso3 = economies[economy] ? economies[economy].iso3 : economy;
        html += `<div class="hm-colhead" title="${escapeHtml(economy)}">${escapeHtml(iso3)}</div>`;
      }
      for (const row of HEATMAP_ROWS) {
        html += `<div class="hm-rowhead">${escapeHtml(row.label)}</div>`;
        for (const economy of universe) {
          const value = heatmapValue(economies[economy], row.key);
          const verdict = signalVerdict(value);
          const bg = value == null ? "var(--nodata)" : color(value);
          const text = value == null ? "&ndash;" : `${verdict.glyph} ${fmt(value)}`;
          const title = `${economy} · ${row.label}: ${verdict.label}${value == null ? "" : " " + fmt(value)}`;
          html += `<div class="hm-cell" data-economy="${escapeHtml(economy)}" data-asset="${row.key}"`
            + ` style="background:${bg}" title="${escapeHtml(title)}">${text}</div>`;
        }
      }
      grid.innerHTML = html;

      grid.querySelectorAll(".hm-cell").forEach(cell => {
        cell.addEventListener("click", () => {
          selectedCountry = cell.dataset.economy;
          renderPanel(selectedCountry);
          highlightHeatmapColumn(selectedCountry);
        });
      });
      highlightHeatmapColumn(selectedCountry);
    }
```

- [ ] **Step 2: Verify grid shape and contents**

Reload, switch to heatmap, then `preview_eval`:
```javascript
(() => { document.querySelector('button[data-tab="heatmap"]').click();
  const cells = [...document.querySelectorAll('#heatmap .hm-cell')];
  const rowheads = [...document.querySelectorAll('#heatmap .hm-rowhead')].map(e=>e.textContent);
  const colheads = [...document.querySelectorAll('#heatmap .hm-colhead')].map(e=>e.textContent);
  const us = cells.find(c => c.dataset.economy==='United States of America' && c.dataset.asset==='equity');
  return { cellCount: cells.length, rowheads, colheads, usEquityText: us.textContent.trim() }; })()
```
Expected: `cellCount: 30`, `rowheads: ["Composite","FX","Rates","Equity","Real estate"]`, `colheads` = 6 iso3 codes (`["USA","CAN","CHN","JPN","BRA","EUR"]`), `usEquityText` starts with `▲ +` (US equity is positive in live/mock data).

- [ ] **Step 3: Verify cell click populates the panel**

`preview_eval`:
```javascript
(() => { const c = document.querySelector('#heatmap .hm-cell[data-economy="Japan"]');
  c.click();
  return { title: document.querySelector('.panel-title').textContent,
    assetCards: document.querySelectorAll('.asset').length,
    selectedCols: document.querySelectorAll('#heatmap .hm-cell.selected-col').length }; })()
```
Expected: `title: "Japan"`, `assetCards: 4`, `selectedCols: 5` (the whole Japan column highlighted).

- [ ] **Step 4: Screenshot the heatmap**

`preview_screenshot` — confirm a colored 5×6 grid with row labels (Composite/FX/Rates/Equity/Real estate), iso3 column headers, ▲/▼ + numbers, and a legend below. (Resize viewport to 1280×800 first if the capture is too narrow.)

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "feat(ui): render transposed country-by-asset heatmap with click-to-detail

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Verdict badges in the detail panel and map tooltip

**Files:**
- Modify: `static/index.html` (`renderPanel` and the tooltip `mousemove` handler)

- [ ] **Step 1: Add a badge to each asset card's Final value**

In `renderPanel`, inside the `entry.signals` `.map(...)` template, change:
```javascript
          <div class="asset-head">
            <span>${assetLabels[asset]}</span>
            <span>${fmt(item.final)}</span>
          </div>
```
to:
```javascript
          <div class="asset-head">
            <span>${assetLabels[asset]}</span>
            <span>${verdictBadgeHtml(item.final)} ${fmt(item.final)}</span>
          </div>
```

- [ ] **Step 2: Add a badge to the headline selected-signal and the composite Final metric**

In `renderPanel`, change the selected-signal block:
```javascript
        <div class="selected-signal">
          ${viewLabel(selectedView)} signal
          <strong>${fmt(selectedValue)}</strong>
        </div>
```
to:
```javascript
        <div class="selected-signal">
          ${viewLabel(selectedView)} signal ${verdictBadgeHtml(selectedValue)}
          <strong>${fmt(selectedValue)}</strong>
        </div>
```

And change the composite Final metric:
```javascript
          <div class="metric"><label>Final</label><strong>${fmt(entry.composite.final)}</strong></div>
```
to:
```javascript
          <div class="metric"><label>Final</label><strong>${fmt(entry.composite.final)}</strong> ${verdictBadgeHtml(entry.composite.final)}</div>
```

- [ ] **Step 3: Add a badge to the map tooltip**

In the `mousemove` handler of `draw()`, change:
```javascript
          tooltip.innerHTML = `<strong>${escapeHtml(name)}</strong><br>${economyName ? `Economy: ${escapeHtml(economyName)}<br>${viewLabel(selectedView)}: ${fmt(value)}` : "No data"}`;
```
to:
```javascript
          tooltip.innerHTML = `<strong>${escapeHtml(name)}</strong><br>${economyName ? `Economy: ${escapeHtml(economyName)}<br>${viewLabel(selectedView)}: ${verdictBadgeHtml(value)} ${fmt(value)}` : "No data"}`;
```

- [ ] **Step 4: Verify badges render in the panel**

Reload, then `preview_eval`:
```javascript
(() => { const usPath = document.querySelector('path[data-country="United States of America"]');
  usPath.dispatchEvent(new MouseEvent('click', {bubbles:true, view:window}));
  const badges = [...document.querySelectorAll('#panel .verdict-badge')].map(b => b.textContent.trim());
  return { badgeCount: badges.length, sample: badges.slice(0,3) }; })()
```
Expected: `badgeCount >= 5` (headline + composite Final + 4 asset finals = 6), with labels among `▲ Bullish` / `▼ Bearish` / `▬ Neutral`.

- [ ] **Step 5: Screenshot the panel** (desktop viewport) — confirm colored BULLISH/BEARISH badges next to the composite Final and each asset.

- [ ] **Step 6: Commit**

```bash
git add static/index.html
git commit -m "feat(ui): show BULLISH/BEARISH/NEUTRAL badges in panel and tooltip

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Final verification + working-tree cleanup

**Files:**
- Modify: none (verification); regenerate `snapshot.json` to mock

- [ ] **Step 1: Full behavioral sweep**

Reload, then `preview_eval`:
```javascript
(() => {
  const f = window.macroDashboardDebug.signalVerdict;
  const verdicts = [0.5,-0.5,0.05,null].map(v => f(v).label).join(",");
  document.querySelector('button[data-tab="heatmap"]').click();
  const cells = document.querySelectorAll('#heatmap .hm-cell').length;
  document.querySelector('#heatmap .hm-cell[data-economy="Brazil"]').click();
  const panel = document.querySelector('.panel-title').textContent;
  document.querySelector('button[data-tab="map"]').click();
  const mapVisible = !document.querySelector('#map-view').hidden;
  const paths = document.querySelectorAll('path.country').length;
  return { verdicts, cells, panel, mapVisible, paths };
})()
```
Expected: `verdicts: "Bullish,Bearish,Neutral,No data"`, `cells: 30`, `panel: "Brazil"`, `mapVisible: true`, `paths: 177`.

- [ ] **Step 2: Screenshots of both tabs** at 1280×800 (`preview_resize` then `preview_screenshot`) — map view and heatmap view, both showing badges/colors.

- [ ] **Step 3: Restore deterministic mock snapshot**

The repo's committed `snapshot.json` is the mock/deterministic build; an earlier live demo left it modified. Restore it:
```bash
.venv/bin/python signal_engine.py
git status --short
```
Expected: `signal_engine.py` prints `Wrote snapshot.json (source=mock)`; `git status --short` shows a clean working tree (snapshot.json back to its committed content).

- [ ] **Step 4: Confirm Python suite still green** (sanity — no backend changes, must still pass)

```bash
.venv/bin/python -m pytest -q
```
Expected: `18 passed`.

- [ ] **Step 5: Final commit (only if anything is uncommitted)**

```bash
git status --short
# if static/index.html or snapshot.json show changes:
git add static/index.html snapshot.json
git commit -m "chore(ui): finalize heatmap + badges; restore mock snapshot

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Out of scope
- Backend/API/Python changes (verified unnecessary).
- Showing data provenance (`world_bank:<year>` vs `mock`) in the UI.
- Persisting the selected tab across reloads.
