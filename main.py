from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from signal_engine import SNAPSHOT_PATH, generate_snapshot
from regime_engine import REGIME_SNAPSHOT_PATH, generate_regime_snapshot
from history import compute_history

import run_manager
import snapshot_diff
import snapshot_store


app = FastAPI(title="Cross-Asset Macro Dashboard")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/documents", StaticFiles(directory="documents"), name="documents")


@app.get("/")
def root():
    return RedirectResponse(url="/static/index.html")


@app.get("/api/signals")
def get_signals():
    path = Path(SNAPSHOT_PATH)
    if not path.exists():
        try:
            generate_snapshot(path)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"snapshot.json is missing and could not be generated: {exc}",
            ) from exc
    return FileResponse(path, media_type="application/json")


@app.get("/api/regime")
def get_regime():
    path = Path(REGIME_SNAPSHOT_PATH)
    if not path.exists():
        try:
            generate_regime_snapshot(path)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"regime_snapshot.json is missing and could not be generated: {exc}",
            ) from exc
    return FileResponse(path, media_type="application/json")


@app.get("/api/history")
def get_history():
    try:
        return JSONResponse(compute_history())
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"signal history could not be computed: {exc}",
        ) from exc


class RunRequest(BaseModel):
    source: Literal["live", "mock"] = "live"


@app.get("/api/snapshots")
def get_snapshots():
    snapshot_store.seed_baseline_if_empty()
    return JSONResponse(snapshot_store.list_snapshots())


@app.get("/api/changes")
def get_changes(base: str | None = None):
    snapshot_store.seed_baseline_if_empty()
    entries = snapshot_store.list_snapshots()
    if len(entries) < 2:
        return JSONResponse({
            "insufficient": True, "base": None, "target": None,
            "changes": [], "minor_count": 0, "unchanged_count": 0,
            "notes": ["fewer than two archived snapshots; run the pipeline"],
        })
    target_id = entries[-1]["id"]
    if base is None:
        base_id = entries[-2]["id"]
    else:
        known = {e["id"] for e in entries}
        if base not in known:
            raise HTTPException(status_code=404, detail=f"unknown snapshot id: {base}")
        base_id = base
    diff = snapshot_diff.compute_diff(
        snapshot_store.load_snapshot(base_id),
        snapshot_store.load_snapshot(target_id),
    )
    return JSONResponse({"insufficient": False, **diff})


@app.post("/api/run", status_code=202)
def post_run(request: RunRequest):
    if not run_manager.start_run(request.source):
        raise HTTPException(status_code=409, detail="a run is already in progress")
    return {"started": True, "source": request.source}


@app.get("/api/run/status")
def get_run_status():
    return JSONResponse(run_manager.get_status())
