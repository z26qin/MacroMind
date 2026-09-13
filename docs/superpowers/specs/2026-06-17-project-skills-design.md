# MacroMind Project Skills — Design

**Date:** 2026-06-17
**Status:** Approved (pending spec review)
**Scope:** Two project-level Claude Skills committed under `.claude/skills/`, capturing recurring MacroMind dev/ops procedures: `add-data-source` and `refresh-snapshot`. Thin, procedural skills that reference the repo's existing files as their template. No change to the engine, config, or any runtime code — purely additive tooling/knowledge.

## Goal

Two MacroMind workflows are repeated often and keep losing time to forgotten steps and re-derived conventions:

1. **Adding a live data adapter.** There are already four built identically (`world_bank`, `imf_weo`, `market`, `gdelt`) and the README's TODO / Future Data Sources list names more to come (policy rate, PMI, BIS real estate, FRED/OECD). Each one touches five places — the adapter module, `signal_engine.py` live wiring, a test, provenance, and the README — and missing one (most often the README bookkeeping or the all-or-nothing rule) is easy.
2. **Refreshing the snapshot.** The daily `refresh.yml` Action runs the live engine and commits, but a local interactive refresh has no codified validation: which inputs actually resolved live vs silently fell back to mock, schema conformance, and what changed.

The goal is to capture both as **thin project-level skills** committed to the repo, so Claude executes them consistently and new contributors (and every worktree) inherit the conventions. This is dev/ops acceleration, not a runtime capability.

## Decisions (from brainstorming)

- **Skills, not MCP.** The goal chosen is accelerating development of this repo, not exposing the engine to other agents. An MCP server (`get_signal`, `get_regime`, `run_live_refresh`, …) is a separate effort with a different consumer and is explicitly out of scope here.
- **Two skills in v1:** `add-data-source` (highest ROI — most repeated, most stable pattern, forward-looking) and `refresh-snapshot` (cheapest, high-frequency, good first build). `add-or-edit-signal` is deferred as a fast-follow once these prove out; `regime-tuning` is dropped (low frequency, more one-off tuning than repeated procedure).
- **Thin procedural structure (Approach A).** Each skill is a short markdown checklist that points at the repo's existing files as the canonical template ("mirror `data_sources/gdelt.py`") rather than embedding its own copy of the pattern. The repo's own code is always the freshest template, so the skill cannot drift from it. No embedded templates and no generator scripts in v1 (Approaches B/C rejected as higher-maintenance).
- **Location: `.claude/skills/<name>/SKILL.md`, committed to the repo.** Every worktree and collaborator gets them, consistent with how `docs/superpowers/` is already shared.
- **Compose with existing skills, don't duplicate them.** Both skills hand off the test/verify step to `tdd-workflow` / `verification-loop` rather than re-specifying test discipline; `add-data-source` defers network-code review to `security-review`. They do **not** replace the brainstorming → plan flow for net-new features — they are for templated, known-shape tasks.
- **No runtime code change.** The skills only encode procedure and knowledge.

## Skill 1: `refresh-snapshot`

**Purpose:** local interactive equivalent of the daily `refresh.yml` Action, plus the validation the Action skips.

**Frontmatter description (trigger phrases):** "refresh the live snapshot", "rebuild `snapshot.json` from live data", "update the macro snapshot".

**Procedure (checklist body):**

1. Run `python signal_engine.py --source live` (use `python3` if that is how the shell exposes Python — README documents this caveat).
2. **Schema check:** `snapshot.json` loads and conforms to `snapshot_models.py`.
3. **Provenance read-out:** report which inputs resolved **live** vs stayed **mock** from the snapshot's `provenance` block; flag any column that *should* be live (World Bank macro, IMF consensus, Yahoo FX/equity, GDELT news) but came back mock — because live columns are all-or-nothing and **silently fall back to mock** on an API hiccup, this is the skill's value over the bare Action.
4. **Diff summary:** what signals/values changed vs the committed `snapshot.json` (git diff plus a value-level summary).
5. Run the snapshot tests: `pytest tests/test_signal_engine.py` (hand off discipline to `verification-loop`).
6. Commit only if changed, using the repo's conventional message (`Refresh mock macro snapshot`).

**Knowledge captured:** the `python`/`python3` caveat, the live-vs-mock provenance expectation and silent all-or-nothing fallback, the commit-message convention, and that the engine writes `snapshot.json` as the backend→frontend contract.

## Skill 2: `add-data-source`

**Purpose:** scaffold and wire a new live data adapter following the four existing ones.

**Frontmatter description (trigger phrases):** "add a new data source", "wire in a live feed for X", "add an adapter for FRED / policy rates / …".

**Procedure (checklist body) — the adapter contract, derived from `data_sources/gdelt.py` and `http.py`:**

1. Read the closest existing adapter (default exemplar: `data_sources/gdelt.py`) plus `data_sources/http.py` for the fetch contract.
2. Create `data_sources/<name>.py`:
   - module docstring naming the source and stating the explainable formula/level;
   - `from data_sources.http import fetch_json as http_fetch_json`;
   - a `_default_fetch_json(url)` wrapper (sets timeout/retries);
   - a public `load_<x>(economies, fetch_json=_default_fetch_json) -> dict[economy: (value, as_of_date)]` with an **injectable `fetch_json`** — this is the test seam.
3. Wire into `signal_engine.py`:
   - `from data_sources import <name>`;
   - an `overlay_<x>(df, provenance, *, source, fetch_json=None)` mirroring `overlay_news_pressure`: mock mode is a no-op, live mode records provenance as `f"<source>:<asof>"` and mutates `df`;
   - call it in the build function alongside the other overlays;
   - respect **all-or-nothing**: the column goes live only when *every* economy resolves a value, else keep its mock value;
   - add the column to the required-columns list if it is a new input.
4. Add `tests/test_<name>.py` mirroring `tests/test_gdelt.py`: inject a `fake_fetch` / `monkeypatch`, **no network** — cover URL/query building, payload parsing, the level/score computation, and the loader.
5. Update `README.md`: the Architecture bullet, Signal Methodology (if a new input), Current Limitations (what is now live vs still mock), and TODO / Future Data Sources.
6. Run `pytest`; hand off test discipline to `tdd-workflow` / `verification-loop` and network-code review to `security-review`.

**Knowledge captured:** the adapter contract (injectable `fetch_json` seam, `{economy: (value, as_of)}` return shape), `http.py` usage, the `overlay_*` + provenance wiring point, the all-or-nothing live rule, and the README bookkeeping that is easiest to forget.

## Skill structure (thin procedural)

Each skill is `.claude/skills/<name>/SKILL.md` with YAML frontmatter (`name`, `description`) plus a short checklist body that references repo files **by path**. No embedded templates, no scripts. The `description` is what makes the skill auto-trigger, so it must contain the natural trigger phrases listed above.

## Authoring + verification

- The `SKILL.md` files are authored using the **`writing-skills`** skill during plan execution (it carries its own structure/verification checklist) — not by hand.
- Verify each skill by: (a) confirming the frontmatter `description` triggers on its natural phrase, and (b) a **dry run** on a real instance — for `refresh-snapshot`, perform one actual refresh; for `add-data-source`, trace every step against an existing adapter (or scaffold a throwaway one) and confirm no step is missing.

## Out of scope (YAGNI)

- **MCP server** exposing engine data/actions to agents — separate effort, different goal (letting agents *use* the project vs helping *develop* it). Revisit when there is an agent consumer.
- **`add-or-edit-signal` skill** — fast-follow after these two prove out.
- **`regime-tuning` skill** — low frequency.
- **Generator scripts / embedded templates** (Approaches B/C) — the repo's files are the template.
- **Any change to engine, config, or runtime code.**
