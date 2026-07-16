"""Background pipeline run state machine for the dashboard Run button.

Single-user tool: one in-memory run at a time, guarded by a lock. The API
process must run as a single uvicorn worker (documented in README).
"""

from __future__ import annotations

import copy
import subprocess
import sys
import threading
from datetime import datetime, timezone

import snapshot_diff
import snapshot_store

PHASE_TIMEOUT_S = 600
LOG_TAIL_LINES = 50
PHASES = ("signal_pipeline", "regime_engine", "archive", "diff")
QUALITY_OUT_PATH = ".cache/last_quality.json"

# Commands for the subprocess phases; tests monkeypatch entries.
PHASE_COMMANDS = {
    "signal_pipeline": lambda source: [
        sys.executable, "signal_engine.py", "--source", source,
        "--quality-out", QUALITY_OUT_PATH,
    ],
    "regime_engine": lambda source: [sys.executable, "regime_engine.py"],
}

_lock = threading.Lock()
_worker: threading.Thread | None = None
_IDLE = {
    "state": "idle", "run_id": None, "source": None, "phase": None,
    "started_at": None, "finished_at": None, "log_tail": [],
    "error": None, "result": None,
}
_status = copy.deepcopy(_IDLE)


def get_status() -> dict:
    with _lock:
        return copy.deepcopy(_status)


def start_run(source: str) -> bool:
    """Kick off a run; False when one is already in flight."""
    global _worker
    with _lock:
        if _status["state"] == "running":
            return False
        now = datetime.now(timezone.utc)
        _status.update(copy.deepcopy(_IDLE))
        _status.update(
            state="running",
            run_id=now.strftime("%Y-%m-%dT%H%M%SZ"),
            source=source,
            started_at=now.isoformat(),
        )
    _worker = threading.Thread(target=_run, args=(source,), daemon=True)
    _worker.start()
    return True


def _reset_for_tests() -> None:
    global _worker
    if _worker is not None and _worker.is_alive():
        _worker.join(timeout=5)
    with _lock:
        _status.clear()
        _status.update(copy.deepcopy(_IDLE))
    _worker = None


def _append_log(text: str) -> None:
    with _lock:
        for line in text.splitlines():
            if line.strip():
                _status["log_tail"].append(line)
        del _status["log_tail"][:-LOG_TAIL_LINES]


def _set_phase(index: int) -> None:
    with _lock:
        _status["phase"] = {"index": index, "total": len(PHASES), "name": PHASES[index]}
    _append_log(f"[phase {index + 1}/{len(PHASES)}] {PHASES[index]}")


def _run_subprocess(name: str, source: str) -> None:
    proc = subprocess.run(
        PHASE_COMMANDS[name](source),
        capture_output=True, text=True, timeout=PHASE_TIMEOUT_S,
    )
    if proc.stdout:
        _append_log(proc.stdout)
    if proc.stderr:
        _append_log(proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError(f"{name} exited with code {proc.returncode}")


def _read_quality_meta() -> dict:
    import json
    from pathlib import Path

    path = Path(QUALITY_OUT_PATH)
    if not path.exists():
        return {}
    try:
        quality = json.loads(path.read_text())
    except ValueError:
        return {}
    return {"quality": quality} if quality else {}


def _archive_and_diff(source: str) -> dict:
    """Phases 3+4; separated so tests can stub filesystem effects."""
    snapshot_id = snapshot_store.archive_current(source=source, extra_meta=_read_quality_meta())
    result = {"snapshot_id": snapshot_id, "headline_count": 0}
    pair = snapshot_store.latest_pair()
    if pair is not None:
        base_entry, target_entry = pair
        diff = snapshot_diff.compute_diff(
            snapshot_store.load_snapshot(base_entry["id"]),
            snapshot_store.load_snapshot(target_entry["id"]),
        )
        result["headline_count"] = sum(1 for c in diff["changes"] if c["level"] == 1)
    return result


def _run(source: str) -> None:
    try:
        _set_phase(0)
        _run_subprocess("signal_pipeline", source)
        _set_phase(1)
        _run_subprocess("regime_engine", source)
        _set_phase(2)  # archive+diff run inside _archive_and_diff; the two
        _set_phase(3)  # phase labels just tick through quickly in the UI
        result = _archive_and_diff(source)
        with _lock:
            _status.update(
                state="succeeded", phase=None,
                finished_at=datetime.now(timezone.utc).isoformat(),
                result=result,
            )
    except Exception as exc:  # noqa: BLE001 — 所有失败进状态机,不炸线程
        _append_log(f"ERROR: {exc}")
        with _lock:
            _status.update(
                state="failed", phase=None,
                finished_at=datetime.now(timezone.utc).isoformat(),
                error=str(exc),
            )
