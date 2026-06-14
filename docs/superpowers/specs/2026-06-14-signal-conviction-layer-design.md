# Signal Conviction Layer — Design

**Date:** 2026-06-14
**Status:** Approved (pending spec review)
**Scope:** Additive conviction metric computed in `signal_engine.py` and written into `snapshot.json`; rendered in the detail panel and the map/heatmap hover tooltip in `static/index.html`. No new data feeds, no LLM, no change to the signal values themselves.

## Goal

Every signal already shows a **direction + magnitude** (e.g. USA FX `-0.91`, Bearish). It says nothing about **how trustworthy that call is**. The goal is a readable conviction layer answering: *is this signal broad-based or hanging on one driver, and do the quant and the narrative agree?* — so the user can size a risk with conviction instead of treating `-0.91` and `-0.11` as equally "real".

Conviction is built from two inputs the user identified as the actual basis for trust:

1. **Breadth** — do the drivers within a signal support the call, or fight it / lean on a single driver?
2. **Narrative agreement** — do the deterministic rules and the RAG narrative point the same way?

Cross-asset coherence ("do FX/rates/equity/RE tell one story") is explicitly treated as a *market-state* concept, not part of conviction — it overlaps the existing Regime tab and is out of scope here.

## Decisions (from brainstorming)

- **Conviction = breadth + narrative agreement.** Cross-asset coherence is out of scope (market-state, not conviction).
- **One reference direction:** breadth and narrative both reference `sign(deterministic)` — the call the user acts on. This keeps the module internally consistent and, as a bonus, captures the "purely relative" case for free (see Breadth).
- **Narrative is asymmetric.** Disagreement *lowers* conviction; agreement does **not** raise it. Rationale: `rag_signal.py` is currently a hardcoded stub — a stub must not be allowed to inflate confidence. Two methods conflicting is real caution; a stub agreeing is weak evidence.
- **Headline breadth metric is `net_lean ∈ [−1, +1]`**, not a `[0.5, 1]`-bounded "% aligned" (which reads misleadingly high). Negative `net_lean` = the drivers actually point *against* the call, i.e. the call is held up purely by cross-sectional ranking.
- **Readability is a reorganization, not an addition.** When the conviction chip lands, the existing dense math line (`deterministic X · RAG Y` + raw driver lists) is demoted to a second layer so "direction + conviction" is the first read.
- **Plain words over glyphs.** Avoid cryptic symbols (`●●●○○ ◐ ◌`); use words ("Medium conviction · 3 of 5 drivers aligned · no narrative view"). A single optional band glyph is acceptable.
- **Placement:** detail panel chip + breakdown, and a conviction line in the map/heatmap hover tooltip. No heatmap-cell visual encoding in v1.
- **No separate Composite conviction in v1.** Composite is a mean of the four asset signals; its only meaningful "conviction" question is cross-asset agreement, which we excluded. Rather than fudge a band-average, the composite shows no conviction chip in v1.
- **Compute in the backend.** `signal_engine.py` writes a `conviction` block per signal into `snapshot.json`; the frontend only renders. Consistent with "snapshot.json is the interface", testable with pytest, shared by panel and tooltip.

## Conviction metric (deterministic)

Computed per asset-class signal (`fx`, `rates`, `equity`, `real_estate`). Inputs available per signal: `deterministic`, `rag`, `final`, `top_positive_drivers`, `top_negative_drivers` (each driver carries `contribution`), `rag_sources`.

### Reference direction

`call_dir = sign(deterministic)`. If `deterministic == 0` or `|final| < 0.10` (Neutral verdict), conviction is **not applicable** — band `"na"`, rendered as `—`. There is no call to have conviction about.

### (a) Breadth

Over all drivers (positive and negative lists combined), each with `contribution`:

- `A` = Σ `|contribution|` for drivers whose sign **matches** `call_dir` (support the call)
- `O` = Σ `|contribution|` for drivers whose sign **opposes** `call_dir`
- `G = A + O` (gross driver weight)
- **`net_lean = (A − O) / G`** ∈ [−1, +1] — headline breadth number
  - `+1` = every driver supports the call (broad-based)
  - `~0` = drivers split
  - `< 0` = drivers actually point against the call; it survives only on cross-sectional ranking (a *relative* call, low conviction)
- `top_driver_share = max|contribution| / G` ∈ [0, 1] — concentration; high = one-driver-dependent
- `top_driver` = feature name of that largest-|contribution| driver
- Edge: `G == 0` (no drivers) → breadth undefined → band `"na"`.

### (b) Narrative agreement

- `has_view = (rag != 0)`
- `not has_view` → `"no_view"`
- `sign(rag) == call_dir` → `"agrees"`
- else → `"disagrees"`

### (c) Roll up to band

Base band from breadth:

| Condition | Base band |
|---|---|
| `net_lean ≥ 0.60` **and** `top_driver_share ≤ 0.50` | High |
| `net_lean < 0.20` **or** `top_driver_share > 0.60` | Low |
| otherwise | Medium |

Narrative adjustment (asymmetric):

- `"disagrees"` → drop one band (High→Medium, Medium→Low, Low→Low)
- `"agrees"` or `"no_view"` → no change

### Worked validation (committed snapshot, USA)

- **FX** `deterministic −1.0`: A = 0.44 (policy .25 + growth .18 + unemp .01), O = 0.24 (carry .12 + momentum .12), G = 0.68 → `net_lean = 0.29`, `top_driver_share = 0.37`. Base = Medium; narrative `no_view` → **Medium**.
- **Rates** `deterministic +1.0`: all drivers support, O = 0 → `net_lean = 1.0`, `top_driver_share = 0.50`. Base = High; narrative `no_view` → **High**.
- **Equity** `deterministic −0.20`, `rag +0.30`: drivers lean against the bearish call (`net_lean` low/negative) → base ≤ Medium; narrative `disagrees` → drop → **Low**.

Matches the treatments shown in brainstorming (Medium / High / Low).

## Snapshot schema

Each entry in `economies[name].signals[asset]` gains:

```json
"conviction": {
  "band": "medium",
  "net_lean": 0.29,
  "top_driver_share": 0.37,
  "top_driver": "policy_surprise",
  "narrative": "no_view"
}
```

- `band` ∈ `"high" | "medium" | "low" | "na"`
- `narrative` ∈ `"agrees" | "disagrees" | "no_view"`
- Composite (`economies[name].composite`) gets **no** conviction block in v1.

## Frontend rendering

`static/index.html`, additive + one reorganization. All colors via existing CSS variables (dark-mode safe); no hardcoded hex.

**Detail panel, per asset block — reorganized so conviction is the first read:**

1. Line 1 (headline): `FX  ▼ Bearish −0.91   ◐ Medium conviction`
2. Line 2 (breakdown, plain words): `3 of 5 drivers support · leans on policy (37%) · no narrative view`
   - "support" count from aligned vs opposing driver counts; `net_lean` sub-50%/negative phrased as e.g. "drivers lean against the call" for Low.
   - narrative clause: `narrative agrees` / `narrative disagrees (+0.30 vs −0.20)` / `no narrative view`
3. **Demoted to a collapsible/secondary line:** the existing `Final … · deterministic … · RAG …` text and the top-positive/top-negative contribution lists.

**Map / heatmap hover tooltip:** add one line under the existing value: `Conviction: ◐ Medium`. Tooltip uses the selected view's signal; Composite view shows no conviction line (no composite band).

**Band display:** word + a single neutral glyph for scanability (`band` → label/glyph/color via a small `convictionBadge()` helper mirroring `signalVerdict`). `"na"` renders `—` with no color.

## Testing

`tests/test_signal_engine.py` (extend):

- **Breadth:** all-aligned → `net_lean ≈ 1`; split → `net_lean ≈ 0`; opposing-majority → `net_lean < 0`; single dominant driver → high `top_driver_share`; no drivers → `"na"`.
- **Narrative:** `rag == 0` → `no_view`; same sign → `agrees`; opposite sign → `disagrees`.
- **Band rollup:** each base branch; `disagrees` drops a band; `agrees`/`no_view` no change; Neutral (`|final| < 0.10`) → `"na"`.
- **Schema:** every asset signal in a generated snapshot carries a well-formed `conviction` block; composite carries none.
- **Regression on committed snapshot:** US FX/Rates/Equity bands = Medium/High/Low.

Frontend is verified manually via the dashboard (panel chip + tooltip line) per the project's existing practice; no JS test harness exists.

## Out of scope (YAGNI)

- Cross-asset coherence (C) as a market-state indicator — separate concept, overlaps Regime tab.
- Heatmap-cell visual encoding (hatched/faded low-conviction cells).
- Composite-level conviction.
- Any change to signal values, weights, or the blend formula.
