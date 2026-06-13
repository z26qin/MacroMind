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


# Walking git history costs one subprocess per committed snapshot, so the result
# is memoized on the current HEAD sha: the daily CI commit moves HEAD, which is
# exactly when the series changes, so the cache invalidates precisely on new data.
_HISTORY_CACHE: dict[tuple[str, str], dict] = {}


def _head_sha(run: Callable[[list[str]], str]) -> str | None:
    try:
        return run(["git", "rev-parse", "HEAD"]).strip()
    except Exception:
        return None


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
    head = _head_sha(run)
    if head is not None:
        cached = _HISTORY_CACHE.get((snapshot_path, head))
        if cached is not None:
            return cached
    result = build_history(load_snapshots_from_git(snapshot_path, run=run))
    if head is not None:
        _HISTORY_CACHE[(snapshot_path, head)] = result
    return result


if __name__ == "__main__":
    print(json.dumps(compute_history(), indent=2))
