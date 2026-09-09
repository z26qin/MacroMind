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
- [ ] **Schema check:** confirm `snapshot.json` conforms to the Pydantic contract in
      `snapshot_models.py`:
      `python -c "import json; from snapshot_models import SignalSnapshotModel; SignalSnapshotModel(**json.load(open('snapshot.json')))"`
- [ ] **Provenance read-out:** from the snapshot's `provenance` block, report which
      inputs resolved **live** vs stayed **mock**. Flag any column that *should* be
      live — World Bank macro, IMF consensus, Yahoo FX/equity, GDELT news — but came
      back mock. Live columns are all-or-nothing and silently fall back to mock on an
      API hiccup, so catching a silent fallback is the point of this step.
- [ ] **Diff summary:** show what changed vs the committed `snapshot.json`
      (`git diff snapshot.json`, focusing on changed `final`/`deterministic` values
      per economy).
- [ ] **Run tests:** `pytest tests/test_signal_engine.py`. Hand off test discipline
      to verification-loop.
- [ ] **Commit only if changed**, using the same message as the GitHub Action:
      `git commit -m "Refresh mock macro snapshot"`.

## Notes

- The engine writes `snapshot.json` as the backend→frontend contract; the frontend
  only renders it.
- `policy_rate`, `pmi`, and the real-estate columns have **no live source yet** (see
  README Current Limitations) — they are *expected* to stay mock. Do not flag those
  as silent fallbacks.
- Macro columns go live **all-or-nothing per column**: a column (e.g. `inflation_yoy`
  with its `inflation_consensus`) only switches to live when *every* economy has both
  a World Bank actual and an IMF forecast. A macro/consensus column can therefore stay
  mock legitimately — check the paired actual column before flagging a consensus
  column as a silent fallback.
