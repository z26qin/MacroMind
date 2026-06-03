from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from signal_engine import SNAPSHOT_PATH, generate_snapshot


app = FastAPI(title="Cross-Asset Macro Dashboard")
app.mount("/static", StaticFiles(directory="static"), name="static")


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
