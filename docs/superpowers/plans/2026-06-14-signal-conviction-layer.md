# Signal Conviction Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a readable, deterministic conviction layer to every asset-class signal — driver breadth plus quant-vs-narrative agreement — so the user can tell a broad-based call from a fragile one at a glance.

**Architecture:** Conviction is computed in `signal_engine.py` (pure functions) and written as a `conviction` block into each signal in `snapshot.json`. The vanilla-JS frontend (`static/index.html`) renders it as an outlined chip + a plain-words breakdown line in the detail panel, demotes the existing math into a `<details>` disclosure, and adds a conviction line to the map tooltip and heatmap cell titles. No new data feeds, no LLM, no change to signal values.

**Tech Stack:** Python 3 / pandas / NumPy (engine), pytest (tests), vanilla HTML/JS + D3 (frontend), FastAPI/uvicorn (serving).

**Reference spec:** `docs/superpowers/specs/2026-06-14-signal-conviction-layer-design.md`

**Conventions:** Run Python via `.venv/bin/python` and tests via `.venv/bin/pytest` (this repo ships a `.venv`). If your shell exposes them on PATH, plain `python`/`pytest` also work.

---

## File Structure

- **Modify** `signal_engine.py` — add `_narrative_state`, `_conviction_band`, `compute_conviction`; call it inside `build_snapshot`'s asset loop. Single responsibility preserved: turn inputs into the snapshot.
- **Modify** `tests/test_signal_engine.py` — append synthetic-input unit tests + snapshot invariant tests.
- **Regenerate** `snapshot.json` — committed live artifact gains the `conviction` blocks.
- **Modify** `static/index.html` — CSS for the conviction chip/line; JS helpers; panel reorg; map tooltip + heatmap title.
- **Modify** `README.md` — document the conviction layer under Signal Methodology.

---

## Task 1: Conviction core functions in `signal_engine.py`

**Files:**
- Modify: `signal_engine.py` (add functions after `explain_contributions`, before `build_snapshot` — around line 481)
- Test: `tests/test_signal_engine.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_signal_engine.py`:

```python
import pandas as pd

from signal_engine import compute_conviction, _conviction_band, _narrative_state


def _row(**features):
    return pd.Series(features)


def test_narrative_state_no_view_when_rag_zero():
    assert _narrative_state(0.0, 1.0) == "no_view"
    assert _narrative_state(0.0, -1.0) == "no_view"


def test_narrative_state_agrees_on_same_sign():
    assert _narrative_state(0.4, 1.0) == "agrees"
    assert _narrative_state(-0.4, -1.0) == "agrees"


def test_narrative_state_disagrees_on_opposite_sign():
    assert _narrative_state(0.4, -1.0) == "disagrees"
    assert _narrative_state(-0.4, 1.0) == "disagrees"


def test_band_high_requires_broad_and_unconcentrated():
    assert _conviction_band(0.7, 0.4, "no_view") == "high"


def test_band_low_when_concentrated_even_if_broad():
    assert _conviction_band(0.9, 0.7, "no_view") == "low"


def test_band_disagree_drops_one_level():
    assert _conviction_band(0.7, 0.4, "disagrees") == "medium"
    assert _conviction_band(0.4, 0.4, "disagrees") == "low"


def test_band_agree_never_raises():
    assert _conviction_band(0.4, 0.4, "agrees") == "medium"
    assert _conviction_band(0.1, 0.4, "agrees") == "low"


def test_conviction_all_aligned_is_high():
    c = compute_conviction(_row(a_rank=1.0, b_rank=1.0),
                           {"a_rank": 0.5, "b_rank": 0.5}, 1.0, 0.0, 0.8)
    assert c["net_lean"] == 1.0
    assert c["top_driver_share"] == 0.5
    assert c["band"] == "high"
    assert c["narrative"] == "no_view"


def test_conviction_negative_net_lean_when_drivers_oppose_call():
    # contribs a=-0.5, b=+0.1; deterministic call is +1 (cross-sectional)
    c = compute_conviction(_row(a_rank=-1.0, b_rank=0.2),
                           {"a_rank": 0.5, "b_rank": 0.5}, 1.0, 0.0, 0.5)
    assert c["net_lean"] < 0
    assert c["band"] == "low"


def test_conviction_dominant_driver_is_low():
    c = compute_conviction(_row(a_rank=1.0, b_rank=0.05),
                           {"a_rank": 1.0, "b_rank": 1.0}, 1.0, 0.0, 0.5)
    assert c["top_driver_share"] > 0.60
    assert c["band"] == "low"
    assert c["top_driver"] == "a"


def test_conviction_disagree_drops_band():
    c = compute_conviction(_row(a_rank=1.0, b_rank=1.0),
                           {"a_rank": 0.5, "b_rank": 0.5}, 1.0, -0.3, 0.5)
    assert c["narrative"] == "disagrees"
    assert c["band"] == "medium"


def test_conviction_agree_does_not_raise_low():
    c = compute_conviction(_row(a_rank=1.0, b_rank=0.05),
                           {"a_rank": 1.0, "b_rank": 1.0}, 1.0, 0.5, 0.5)
    assert c["narrative"] == "agrees"
    assert c["band"] == "low"


def test_conviction_neutral_final_is_na():
    c = compute_conviction(_row(a_rank=1.0, b_rank=1.0),
                           {"a_rank": 0.5, "b_rank": 0.5}, 0.05, 0.0, 0.05)
    assert c["band"] == "na"
    assert c["top_driver"] is None


def test_conviction_zero_gross_is_na():
    c = compute_conviction(_row(a_rank=0.0, b_rank=0.0),
                           {"a_rank": 0.5, "b_rank": 0.5}, 0.5, 0.0, 0.5)
    assert c["band"] == "na"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_signal_engine.py -k "conviction or narrative_state or band_" -v`
Expected: FAIL — `ImportError: cannot import name 'compute_conviction'`.

- [ ] **Step 3: Implement the functions**

In `signal_engine.py`, insert immediately after `explain_contributions` (after line 480, before `def build_snapshot`):

```python
def _narrative_state(rag_signal: float, deterministic: float) -> str:
    """Whether the RAG narrative agrees with the deterministic call.

    Asymmetric by design: callers only ever let "disagrees" lower conviction;
    "agrees" never raises it, because the RAG overlay is still a hardcoded stub
    and must not be allowed to inflate confidence.
    """
    if rag_signal == 0 or deterministic == 0:
        return "no_view"
    return "agrees" if (rag_signal > 0) == (deterministic > 0) else "disagrees"


def _conviction_band(net_lean: float, top_driver_share: float, narrative: str) -> str:
    """Roll breadth + narrative agreement into a high/medium/low band."""
    if net_lean >= 0.60 and top_driver_share <= 0.50:
        base = "high"
    elif net_lean < 0.20 or top_driver_share > 0.60:
        base = "low"
    else:
        base = "medium"
    if narrative == "disagrees":
        base = {"high": "medium", "medium": "low", "low": "low"}[base]
    return base


def compute_conviction(
    row: pd.Series,
    asset_weights: dict[str, float],
    deterministic: float,
    rag_signal: float,
    final: float,
) -> dict:
    """Driver-breadth + narrative-agreement conviction for one asset signal.

    Breadth and narrative both reference the deterministic call direction.
    ``net_lean`` is in [-1, 1]; negative means the drivers point against the
    call, i.e. it survives only on the cross-sectional ranking. Breadth is
    computed over the full weight set, not the truncated driver lists stored
    in the snapshot.
    """
    narrative = _narrative_state(rag_signal, deterministic)
    contributions = {
        feature: float(row[feature]) * float(weight)
        for feature, weight in asset_weights.items()
    }
    gross = sum(abs(c) for c in contributions.values())

    if deterministic == 0 or abs(final) < 0.10 or gross == 0:
        return {
            "band": "na",
            "net_lean": 0.0,
            "top_driver_share": 0.0,
            "top_driver": None,
            "narrative": narrative,
        }

    call_dir = 1.0 if deterministic > 0 else -1.0
    aligned = sum(abs(c) for c in contributions.values() if c * call_dir > 0)
    opposing = sum(abs(c) for c in contributions.values() if c * call_dir < 0)
    net_lean = (aligned - opposing) / gross

    top_feature = max(contributions, key=lambda f: abs(contributions[f]))
    top_driver_share = abs(contributions[top_feature]) / gross

    return {
        "band": _conviction_band(net_lean, top_driver_share, narrative),
        "net_lean": round(net_lean, 4),
        "top_driver_share": round(top_driver_share, 4),
        "top_driver": top_feature.replace("_rank", ""),
        "narrative": narrative,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_signal_engine.py -k "conviction or narrative_state or band_" -v`
Expected: PASS (all 14 tests).

- [ ] **Step 5: Commit**

```bash
git add signal_engine.py tests/test_signal_engine.py
git commit -m "feat(signal): add deterministic conviction metric (breadth + narrative)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Wire conviction into the snapshot

**Files:**
- Modify: `signal_engine.py` — `build_snapshot` asset loop (around lines 515-539)
- Test: `tests/test_signal_engine.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_signal_engine.py` (the `snapshot` fixture already exists at the top of the file):

```python
def test_each_asset_signal_has_conviction_block(snapshot):
    valid_bands = {"high", "medium", "low", "na"}
    valid_narratives = {"agrees", "disagrees", "no_view"}
    for economy in snapshot["economies"].values():
        for signal in economy["signals"].values():
            conviction = signal["conviction"]
            assert conviction["band"] in valid_bands
            assert conviction["narrative"] in valid_narratives
            assert -1.0 <= conviction["net_lean"] <= 1.0
            assert 0.0 <= conviction["top_driver_share"] <= 1.0


def test_composite_has_no_conviction(snapshot):
    for economy in snapshot["economies"].values():
        assert "conviction" not in economy["composite"]


def test_conviction_methodology_invariants(snapshot):
    for economy in snapshot["economies"].values():
        for signal in economy["signals"].values():
            conviction = signal["conviction"]
            if conviction["narrative"] == "disagrees":
                assert conviction["band"] != "high"
            if conviction["band"] == "na":
                assert abs(signal["final"]) < 0.10 or signal["deterministic"] == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_signal_engine.py -k "conviction_block or composite_has_no or methodology_invariants" -v`
Expected: FAIL — `KeyError: 'conviction'`.

- [ ] **Step 3: Wire it into `build_snapshot`**

In `signal_engine.py`, in the `for asset_class in ASSET_CLASSES:` loop, after the line `top_positive, top_negative = explain_contributions(row, asset_weights)` (line 522), add:

```python
            conviction = compute_conviction(
                row, asset_weights, deterministic, rag_signal, final
            )
```

Then add a `"conviction"` key to the `entry["signals"][asset_class]` dict (after `"top_negative_drivers": top_negative,`, line 538):

```python
                "top_negative_drivers": top_negative,
                "conviction": conviction,
```

(Leave the `entry["composite"]` dict at lines 541-545 untouched — composite gets no conviction in v1.)

- [ ] **Step 4: Run the full engine test suite to verify it passes**

Run: `.venv/bin/pytest tests/test_signal_engine.py -v`
Expected: PASS (all prior tests + the 3 new ones).

- [ ] **Step 5: Commit**

```bash
git add signal_engine.py tests/test_signal_engine.py
git commit -m "feat(signal): write conviction block into each snapshot signal

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Regenerate the committed live snapshot

The committed `snapshot.json` is a **live** build (see README). Regenerate it live so the dashboard artifact carries `conviction` blocks without reverting to mock values.

**Files:**
- Regenerate: `snapshot.json`

- [ ] **Step 1: Regenerate live**

Run: `.venv/bin/python signal_engine.py --source live`
Expected: `Wrote .../snapshot.json (source=live)`.

If the live APIs are unreachable in your environment, STOP and report it — do **not** silently fall back to `--source mock`, which would overwrite the live artifact and flip `data_source` to `"mock"`. Regenerating live is the intent; a mock fallback is a visible regression that needs a human decision.

- [ ] **Step 2: Verify the regenerated snapshot carries conviction**

Run:
```bash
.venv/bin/python -c "import json; d=json.load(open('snapshot.json')); s=d['economies']['United States of America']['signals']; print({k: v['conviction']['band'] for k,v in s.items()}); print('data_source=', d['data_source'])"
```
Expected: a dict of four bands (e.g. `{'fx': 'medium', ...}`) and `data_source= live`.

- [ ] **Step 3: Commit**

```bash
git add snapshot.json
git commit -m "chore(signal): refresh live snapshot with conviction blocks

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Frontend — conviction CSS and JS helpers

No JS test harness exists; correctness here is verified visually in Task 7. Keep all colors on the existing CSS variables (dark-mode safe).

**Files:**
- Modify: `static/index.html` — CSS after line 462; JS helpers after `verdictBadgeHtml` (line 759)

- [ ] **Step 1: Add CSS**

In `static/index.html`, after the `.badge-neu { background: var(--muted); }` line (line 462), add:

```css
    .conv-badge {
      display: inline-block;
      font-size: 10px;
      font-weight: 800;
      letter-spacing: 0.04em;
      padding: 1px 7px;
      border-radius: 999px;
      vertical-align: middle;
      white-space: nowrap;
      border: 1px solid var(--muted);
      color: var(--muted);
    }
    .conv-high { color: var(--positive); border-color: var(--positive); }
    .conv-low { color: var(--negative); border-color: var(--negative); }
    .conv-line { margin: 6px 0 0; color: var(--text); font-size: 12.5px; line-height: 1.45; }
    .asset details { margin-top: 8px; }
    .asset summary { cursor: pointer; color: var(--muted); font-size: 12px; }
```

- [ ] **Step 2: Add JS helpers**

In `static/index.html`, after the `verdictBadgeHtml` function (ends line 759, before `viewLabel`), add:

```javascript
    const convictionMeta = {
      high: { label: "High conviction", glyph: "●", cls: "conv-high" },
      medium: { label: "Medium conviction", glyph: "◐", cls: "conv-medium" },
      low: { label: "Low conviction", glyph: "○", cls: "conv-low" },
    };

    function convictionBadgeHtml(conviction) {
      if (!conviction || conviction.band === "na") return "";
      const meta = convictionMeta[conviction.band];
      if (!meta) return "";
      return `<span class="conv-badge ${meta.cls}">${meta.glyph} ${escapeHtml(meta.label)}</span>`;
    }

    function convictionLine(conviction) {
      if (!conviction || conviction.band === "na") return "";
      const nl = conviction.net_lean;
      let breadth;
      if (nl >= 0.60) breadth = "drivers broadly support";
      else if (nl >= 0.20) breadth = "drivers mixed";
      else breadth = "drivers lean against the call";
      let concentration = "";
      if (conviction.top_driver_share > 0.50 && conviction.top_driver) {
        const pct = Math.round(conviction.top_driver_share * 100);
        concentration = ` · leans on ${escapeHtml(conviction.top_driver)} (${pct}%)`;
      }
      const narrative = {
        agrees: " · narrative agrees",
        disagrees: " · narrative disagrees",
        no_view: " · no narrative view",
      }[conviction.narrative] || "";
      return `<p class="conv-line">${breadth}${concentration}${narrative}</p>`;
    }
```

(`convictionMeta.medium.cls` is `conv-medium`, which has no extra CSS rule — it intentionally inherits the muted `.conv-badge` base.)

- [ ] **Step 3: Commit**

```bash
git add static/index.html
git commit -m "feat(dashboard): conviction chip styles and render helpers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Frontend — reorganize the panel asset block

Surface conviction as the first read; demote the existing math into a `<details>`.

**Files:**
- Modify: `static/index.html` — the `assets` template in `renderPanel` (lines 842-851)

- [ ] **Step 1: Replace the asset block template**

In `renderPanel`, replace this exact block (lines 842-851):

```javascript
        <div class="asset ${selectedView === asset ? "highlight" : ""}">
          <div class="asset-head">
            <span>${assetLabels[asset]}</span>
            <span>${verdictBadgeHtml(item.final)} ${fmt(item.final)}</span>
          </div>
          <div class="bar-track">
            <div class="bar-zero"></div>
            <div class="bar" style="${barStyle(item.final)}"></div>
          </div>
          <p>Final ${fmt(item.final)} · deterministic ${fmt(item.deterministic)} · RAG ${fmt(item.rag)}</p>
          <p class="driver-reason">${formatReason(item.driver)}</p>
          <p>${escapeHtml(item.rag_summary)}</p>
          <div class="driver-grid">
            ${renderDriverList("Top positive", item.top_positive_drivers || [])}
            ${renderDriverList("Top negative", item.top_negative_drivers || [])}
          </div>
        </div>
```

with:

```javascript
        <div class="asset ${selectedView === asset ? "highlight" : ""}">
          <div class="asset-head">
            <span>${assetLabels[asset]}</span>
            <span>${verdictBadgeHtml(item.final)} ${fmt(item.final)} ${convictionBadgeHtml(item.conviction)}</span>
          </div>
          <div class="bar-track">
            <div class="bar-zero"></div>
            <div class="bar" style="${barStyle(item.final)}"></div>
          </div>
          ${convictionLine(item.conviction)}
          <p class="driver-reason">${formatReason(item.driver)}</p>
          <p>${escapeHtml(item.rag_summary)}</p>
          <details>
            <summary>Show math</summary>
            <p>Final ${fmt(item.final)} · deterministic ${fmt(item.deterministic)} · RAG ${fmt(item.rag)}</p>
            <div class="driver-grid">
              ${renderDriverList("Top positive", item.top_positive_drivers || [])}
              ${renderDriverList("Top negative", item.top_negative_drivers || [])}
            </div>
          </details>
        </div>
```

- [ ] **Step 2: Commit**

```bash
git add static/index.html
git commit -m "feat(dashboard): lead asset panel with conviction, demote math to details

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Frontend — conviction in the map tooltip and heatmap titles

**Files:**
- Modify: `static/index.html` — map `mousemove` handler (lines 884-892); `drawHeatmap` cell title (lines 942-948)

- [ ] **Step 1: Add conviction to the map tooltip**

In the `.on("mousemove", ...)` handler, replace the tooltip line (line 892):

```javascript
          tooltip.innerHTML = `<strong>${escapeHtml(name)}</strong><br>${economyName ? `Economy: ${escapeHtml(economyName)}<br>${viewLabel(selectedView)}: ${verdictBadgeHtml(value)} ${fmt(value)}` : "No data"}`;
```

with:

```javascript
          const conviction = (entry && selectedView !== "composite")
            ? entry.signals[selectedView].conviction : null;
          const convBadge = convictionBadgeHtml(conviction);
          tooltip.innerHTML = `<strong>${escapeHtml(name)}</strong><br>${economyName ? `Economy: ${escapeHtml(economyName)}<br>${viewLabel(selectedView)}: ${verdictBadgeHtml(value)} ${fmt(value)}${convBadge ? "<br>" + convBadge : ""}` : "No data"}`;
```

(`entry` and `value` are already defined earlier in this handler at lines 887-888.)

- [ ] **Step 2: Add conviction to the heatmap cell title**

In `drawHeatmap`, inside the `for (const economy of universe)` inner loop, after the `const title = ...` line (line 945), the title is a native `title=` attribute. Replace lines 942-945:

```javascript
          const value = heatmapValue(economies[economy], row.key);
          const verdict = signalVerdict(value);
          const bg = value == null ? "var(--nodata)" : color(value);
          const text = value == null ? "&ndash;" : `${verdict.glyph} ${fmt(value)}`;
          const title = `${economy} · ${row.label}: ${verdict.label}${value == null ? "" : " " + fmt(value)}`;
```

with:

```javascript
          const value = heatmapValue(economies[economy], row.key);
          const verdict = signalVerdict(value);
          const bg = value == null ? "var(--nodata)" : color(value);
          const text = value == null ? "&ndash;" : `${verdict.glyph} ${fmt(value)}`;
          const conviction = (row.key !== "composite" && economies[economy])
            ? economies[economy].signals[row.key].conviction : null;
          const convText = (conviction && conviction.band !== "na" && convictionMeta[conviction.band])
            ? ` · ${convictionMeta[conviction.band].label}` : "";
          const title = `${economy} · ${row.label}: ${verdict.label}${value == null ? "" : " " + fmt(value)}${convText}`;
```

- [ ] **Step 3: Commit**

```bash
git add static/index.html
git commit -m "feat(dashboard): show conviction in map tooltip and heatmap titles

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Manual verification + README

**Files:**
- Modify: `README.md` — Signal Methodology section

- [ ] **Step 1: Start the dashboard and verify rendering**

Use the preview tooling (preferred) or run `.venv/bin/uvicorn main:app --reload` and open http://127.0.0.1:8000.

Verify on the **Map** tab:
- Click the United States. Each asset block shows the verdict chip followed by an outlined conviction chip (e.g. `◐ Medium conviction`), then a plain-words line ("drivers broadly support · …" / "drivers lean against the call · no narrative view").
- A `Show math` disclosure is collapsed by default; expanding it reveals the old `Final · deterministic · RAG` line and the top positive/negative driver grid.
- Hover a covered country: the tooltip shows a conviction chip on its own line (for a non-composite view). Switch the view selector to **Composite** and confirm the tooltip shows **no** conviction line.

Verify on the **Heatmap** tab:
- Hover an asset-row cell: the native tooltip title ends with ` · <Band> conviction`. Hover a **Composite**-row cell: no conviction suffix.

Verify **dark mode** (toggle the theme): conviction chips and the breakdown line remain legible (colors come from CSS variables).

Capture a screenshot of the US panel as proof.

- [ ] **Step 2: Update the README**

In `README.md`, under "Signal Methodology", after the paragraph describing `rag_effective_weight` / the RAG overlay, add:

```markdown
### Conviction

Each asset signal also carries a deterministic **conviction** read (`signals.<asset>.conviction` in `snapshot.json`) answering "how trustworthy is this call":

- **Breadth** — `net_lean ∈ [−1, +1]`, the weight-aligned agreement of the drivers with the deterministic call direction (negative = the drivers point against the call, which then rests purely on the cross-sectional ranking), plus `top_driver_share` (concentration on a single driver).
- **Narrative agreement** — whether the RAG overlay agrees with the deterministic call. Asymmetric: disagreement lowers the band, agreement never raises it (the RAG overlay is a stub).

These roll up to a `band` of `high` / `medium` / `low`, or `na` for a Neutral signal. The dashboard shows the band as a chip in the detail panel (with the raw math behind a "Show math" disclosure) and in the map/heatmap hover. Composite signals carry no conviction in this version.
```

- [ ] **Step 3: Run the full test suite**

Run: `.venv/bin/pytest`
Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document the signal conviction layer

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes

- **Spec coverage:** breadth (Task 1) ✓; asymmetric narrative (Task 1, `_narrative_state` + `_conviction_band`) ✓; band rollup incl. `na` (Task 1) ✓; snapshot block + composite-none (Task 2) ✓; live snapshot regeneration (Task 3) ✓; panel chip + plain-words line + demoted math (Tasks 4-5) ✓; map tooltip + heatmap title, composite excluded (Task 6) ✓; synthetic + invariant tests (Tasks 1-2) ✓; README (Task 7) ✓.
- **Breadth over full weight set, not truncated drivers:** enforced by computing from `asset_weights` in `compute_conviction` (Task 1), per the spec note.
- **Type/name consistency:** `compute_conviction`, `_narrative_state`, `_conviction_band`, `convictionMeta`, `convictionBadgeHtml`, `convictionLine` used identically across tasks; conviction keys (`band`, `net_lean`, `top_driver_share`, `top_driver`, `narrative`) consistent between engine output, tests, and frontend readers.
- **No exact-band assertions on real economies:** correctness proven by synthetic unit tests + methodology invariants, since mock and live produce different (and drifting) values.
