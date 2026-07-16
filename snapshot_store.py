"""Snapshot archive store: point-in-time copies of the working snapshots.

Each successful pipeline run archives ``snapshot.json`` + ``regime_snapshot.json``
into ``data/snapshots/<UTC timestamp>/`` together with a small ``meta.json``.
The archive directory (committed to git) is the only data source for diffs.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOTS_DIR = Path("data/snapshots")
SIGNAL_PATH = Path("snapshot.json")
REGIME_PATH = Path("regime_snapshot.json")

SIGNAL_FILE = "snapshot.json"
REGIME_FILE = "regime_snapshot.json"
META_FILE = "meta.json"


def _utc_stamp(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H%M%SZ")


def list_snapshots(snapshots_dir: Path = SNAPSHOTS_DIR) -> list[dict]:
    """Complete archives sorted oldest-first: [{id, as_of, meta}, ...]."""
    if not snapshots_dir.exists():
        return []
    entries = []
    for child in sorted(snapshots_dir.iterdir()):
        if child.name.startswith("."):
            continue  # staging dirs from interrupted archives
        signal_file = child / SIGNAL_FILE
        if not (child.is_dir() and signal_file.exists() and (child / REGIME_FILE).exists()):
            continue
        meta_file = child / META_FILE
        entries.append(
            {
                "id": child.name,
                "as_of": json.loads(signal_file.read_text())["as_of"],
                "meta": json.loads(meta_file.read_text()) if meta_file.exists() else {},
            }
        )
    return entries


def load_snapshot(snapshot_id: str, snapshots_dir: Path = SNAPSHOTS_DIR) -> dict:
    """Load one archive in the diff-engine input shape {"id", "signal", "regime"}."""
    root = snapshots_dir / snapshot_id
    return {
        "id": snapshot_id,
        "signal": json.loads((root / SIGNAL_FILE).read_text()),
        "regime": json.loads((root / REGIME_FILE).read_text()),
    }


def latest_pair(snapshots_dir: Path = SNAPSHOTS_DIR) -> tuple[dict, dict] | None:
    """(base_entry, target_entry) — the two newest archives, or None when < 2."""
    entries = list_snapshots(snapshots_dir)
    if len(entries) < 2:
        return None
    return entries[-2], entries[-1]


def archive_current(
    source: str,
    *,
    snapshots_dir: Path = SNAPSHOTS_DIR,
    signal_path: Path = SIGNAL_PATH,
    regime_path: Path = REGIME_PATH,
    now: datetime | None = None,
    extra_meta: dict | None = None,
    snapshot_id: str | None = None,
) -> str:
    """Archive the working snapshots; returns the new snapshot id.

    Copies into a dot-prefixed staging dir and renames at the end, so an
    interrupted archive can never be listed as a complete snapshot.
    """
    snapshot_id = snapshot_id or _utc_stamp(now)
    target = snapshots_dir / snapshot_id
    if target.exists():
        raise FileExistsError(f"snapshot archive already exists: {target}")
    staging = snapshots_dir / f".tmp-{snapshot_id}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copyfile(signal_path, staging / SIGNAL_FILE)
    shutil.copyfile(regime_path, staging / REGIME_FILE)
    meta = {
        "id": snapshot_id,
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **(extra_meta or {}),
    }
    (staging / META_FILE).write_text(json.dumps(meta, indent=2))
    staging.rename(target)
    return snapshot_id


def seed_baseline_if_empty(
    *,
    snapshots_dir: Path = SNAPSHOTS_DIR,
    signal_path: Path = SIGNAL_PATH,
    regime_path: Path = REGIME_PATH,
) -> str | None:
    """Archive the committed working snapshots as a baseline when none exist.

    Returns the new id, or None when archives already exist or the working
    files are missing. The id derives from the signal snapshot's as_of date so
    seeding is deterministic across machines.
    """
    if list_snapshots(snapshots_dir):
        return None
    if not (signal_path.exists() and regime_path.exists()):
        return None
    as_of = json.loads(signal_path.read_text())["as_of"]
    return archive_current(
        source="baseline",
        snapshots_dir=snapshots_dir,
        signal_path=signal_path,
        regime_path=regime_path,
        snapshot_id=f"{as_of[:10]}T000000Z-baseline",
        extra_meta={"seeded": True},
    )
