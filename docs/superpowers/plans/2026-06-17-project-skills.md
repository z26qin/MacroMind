# MacroMind Project Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two committed project-level Claude Skills — `refresh-snapshot` and `add-data-source` — that capture MacroMind's recurring dev/ops procedures.

**Architecture:** Each skill is a single `.claude/skills/<name>/SKILL.md` with YAML frontmatter (`name`, `description` with trigger phrases) plus a short checklist body that references the repo's existing files **by path** as the canonical template. Thin/procedural — no embedded templates, no scripts, no runtime code change. Skills are authored with the `superpowers:writing-skills` skill and verified by frontmatter-trigger check + a dry run on a real instance.

**Tech Stack:** Markdown + YAML frontmatter (Claude Skills format). No Python change. Verification touches the existing engine (`signal_engine.py --source live`), `snapshot_models.py`, and the existing adapters/tests under `data_sources/` and `tests/`.

**Reference spec:** `docs/superpowers/specs/2026-06-17-project-skills-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `.claude/skills/refresh-snapshot/SKILL.md` | Procedure: rebuild `snapshot.json` from live data, validate provenance/schema, diff, test, commit. |
| `.claude/skills/add-data-source/SKILL.md` | Procedure: scaffold `data_sources/<name>.py`, wire the live overlay + provenance in `signal_engine.py`, add a no-network test, update README. |

Both are self-contained. Neither imports the other. No existing file is modified.

---

## Task 1: `refresh-snapshot` skill

**Files:**
- Create: `.claude/skills/refresh-snapshot/SKILL.md`

- [ ] **Step 1: Author the SKILL.md (use `superpowers:writing-skills` for structure/format)**

Write exactly this content, letting writing-skills validate frontmatter shape and naming:

````markdown
---
name: refresh-snapshot
description: Use when refreshing or rebuilding the MacroMind live macro snapshot — triggers like "refresh the live snapshot", "rebuild snapshot.json from live data", "update the macro snapshot". Runs the live signal engine, validates provenance and schema, summarizes the diff, runs tests, and commits.
---

# Refresh Snapshot

Local interactive equivalent of the daily `refresh.yml` GitHub Action, plus the
validation the Action skips. Rebuilds `snapshot.json` from live data and verifies
it before committing.

## Checklist

- [ ] Run the live engine: `python signal_engine.py --source live` (use `python3`
      if that is how your shell exposes Python — see the README Run section).
- [ ] **Schema check:** confirm `snapshot.json` loads and conforms to
      `snapshot_models.py`.
- [ ] **Provenance read-out:** from the snapshot's `provenance` block, report which
      inputs resolved **live** vs stayed **mock**. Flag any column that *should* be
      live — World Bank macro, IMF consensus, Yahoo FX/equity, GDELT news — but came
      back mock. Live columns are all-or-nothing and silently fall back to mock on an
      API hiccup, so catching a silent fallback is the point of this step.
- [ ] **Diff summary:** show what changed vs the committed `snapshot.json` (git diff
      plus a value-level summary of which signals moved).
- [ ] **Run tests:** `pytest tests/test_signal_engine.py`. Hand off test discipline
      to verification-loop.
- [ ] **Commit only if changed**, using the repo convention:
      `git commit -m "Refresh mock macro snapshot"`.

## Notes

- The engine writes `snapshot.json` as the backend→frontend contract; the frontend
  only renders it.
- `policy_rate`, `pmi`, and the real-estate columns have **no live source yet** (see
  README Current Limitations) — they are *expected* to stay mock. Do not flag those
  as silent fallbacks.
````

- [ ] **Step 2: Verify the frontmatter triggers**

Confirm the `description` contains the natural trigger phrases. Sanity test by stating a phrase like "refresh the live snapshot" and confirming this skill is the obvious match (no overlap with existing skills).

Expected: description includes "refresh the live snapshot" / "rebuild snapshot.json from live data" / "update the macro snapshot".

- [ ] **Step 3: Dry run on a real instance**

Execute the skill's own checklist once against the live repo:

Run: `python signal_engine.py --source live` (or `python3 …`)
Then: `pytest tests/test_signal_engine.py -q`
Expected: engine writes `snapshot.json` without error; tests PASS; you can read the `provenance` block and name which inputs are live vs mock.

Keep the working tree clean for this skills-only branch — discard the refreshed snapshot after verifying:

Run: `git checkout -- snapshot.json`
Expected: `git status` shows only the new SKILL.md as untracked.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/refresh-snapshot/SKILL.md
git commit -m "feat(skills): add refresh-snapshot project skill"
```

---

## Task 2: `add-data-source` skill

**Files:**
- Create: `.claude/skills/add-data-source/SKILL.md`

- [ ] **Step 1: Author the SKILL.md (use `superpowers:writing-skills` for structure/format)**

Write exactly this content, letting writing-skills validate frontmatter shape and naming:

````markdown
---
name: add-data-source
description: Use when adding a new live data adapter to MacroMind — triggers like "add a new data source", "wire in a live feed for X", "add an adapter for FRED / policy rates / PMI". Scaffolds data_sources/<name>.py, wires the live overlay and provenance in signal_engine.py, adds a no-network test, and updates the README.
---

# Add Data Source

Scaffold and wire a new live data adapter for MacroMind, following the four existing
ones (`world_bank`, `imf_weo`, `market`, `gdelt`). The repo's existing adapters are
the canonical template — mirror the closest one rather than inventing a new shape.

## Checklist

- [ ] Read the closest existing adapter (default exemplar: `data_sources/gdelt.py`)
      and `data_sources/http.py` for the fetch contract.
- [ ] Create `data_sources/<name>.py`:
  - module docstring naming the source and stating the explainable formula/level;
  - `from data_sources.http import fetch_json as http_fetch_json`;
  - a `_default_fetch_json(url)` wrapper that sets timeout/retries;
  - a public `load_<x>(economies, fetch_json=_default_fetch_json) -> dict[economy: (value, as_of_date)]`
    with an **injectable `fetch_json`** — this is the test seam.
- [ ] Wire into `signal_engine.py`:
  - `from data_sources import <name>`;
  - an `overlay_<x>(df, provenance, *, source, fetch_json=None)` mirroring
    `overlay_news_pressure`: mock mode is a no-op; live mode records provenance as
    `f"<source>:<asof>"` and mutates `df`;
  - call it in the build function alongside the other overlays;
  - **all-or-nothing:** the column goes live only when *every* economy resolves a
    value, else keep its mock value;
  - add the column to the required-columns list if it is a new input.
- [ ] Add `tests/test_<name>.py` mirroring `tests/test_gdelt.py`: inject a
      `fake_fetch` / `monkeypatch`, **no network**. Cover URL/query building, payload
      parsing, the level/score computation, and the loader.
- [ ] Update `README.md`: the Architecture bullet, Signal Methodology (if it is a new
      input), Current Limitations (what is now live vs still mock), and TODO / Future
      Data Sources.
- [ ] Run `pytest`. Hand off test discipline to tdd-workflow / verification-loop, and
      network-code review to security-review.

## Notes

- Never call the network in tests — always inject `fetch_json`.
- Return shape is `{economy: (value, as_of_date)}`; the overlay records provenance per
  cell as `"<source>:<asof>"`.
- A live column is all-or-nothing: one missing economy keeps the whole column on mock.
````

- [ ] **Step 2: Verify the frontmatter triggers**

Confirm the `description` contains the natural trigger phrases.

Expected: description includes "add a new data source" / "wire in a live feed" / "add an adapter for …".

- [ ] **Step 3: Dry run — trace every referenced anchor against the live repo**

The skill cites specific files/functions as its template. Confirm each one still exists so the skill won't send an executor to a dead reference:

Run: `ls data_sources/gdelt.py data_sources/http.py tests/test_gdelt.py`
Expected: all three exist.

Run: `grep -n "def overlay_news_pressure\|def load_news_pressure\|fetch_json" signal_engine.py data_sources/gdelt.py | head`
Expected: `overlay_news_pressure` in `signal_engine.py`, `load_news_pressure` + injectable `fetch_json` in `gdelt.py` — confirming the overlay pattern, the loader return-shape, and the injectable-fetch seam the skill describes all match reality.

Run: `grep -n "provenance\[economy\]\[.*\] = f\"" signal_engine.py | head`
Expected: at least one `provenance[economy][...] = f"<source>:<asof>"` line, confirming the provenance convention the skill cites.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/add-data-source/SKILL.md
git commit -m "feat(skills): add add-data-source project skill"
```

---

## Self-Review

**1. Spec coverage:**
- Two skills `refresh-snapshot` + `add-data-source` → Tasks 1 and 2. ✔
- Thin procedural, repo files as template → both SKILL.md bodies reference paths, embed no templates. ✔
- Location `.claude/skills/<name>/SKILL.md`, committed → both tasks create that exact path and commit. ✔
- No runtime code change → File Structure modifies no existing file. ✔
- Author via writing-skills; verify by trigger + dry run → Step 1 uses writing-skills, Steps 2–3 verify. ✔
- Compose with existing skills (verification-loop / tdd-workflow / security-review) → named in both SKILL bodies. ✔
- Deferred/dropped (`add-or-edit-signal`, `regime-tuning`, MCP) → correctly absent. ✔

**2. Placeholder scan:** No TBD/TODO-as-placeholder. The `<name>` / `<x>` tokens in the `add-data-source` body are intentional skill parameters (the future adapter's name), not plan placeholders — the SKILL.md content itself is complete and final.

**3. Type consistency:** Skill names (`refresh-snapshot`, `add-data-source`), file paths, and the cited engine anchors (`overlay_news_pressure`, `load_news_pressure`, `_default_fetch_json`, `provenance[economy][col]`) match the spec and the actual repo verified during brainstorming.
