"""Generic TTL cache backed by a single JSON file.

Stores ``{key: {"value": <json>, "stored_at": <epoch>}}``. A missing or corrupt
file reads as an empty cache, and an I/O write failure is swallowed so a caching
problem can never break the caller (a non-serializable value still raises, since
that is a programming error). The clock is injectable for testing.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable


class TTLCache:
    def __init__(
        self,
        path: str | os.PathLike[str],
        ttl_seconds: float,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._path = Path(path)
        self._ttl = float(ttl_seconds)
        self._now = now
        self._store = self._load()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def _load(self) -> dict[str, Any]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if not isinstance(entry, dict):
            return None
        stored_at = entry.get("stored_at")
        if not isinstance(stored_at, (int, float)):
            return None
        if self._now() - stored_at >= self._ttl:
            return None
        return entry.get("value")

    def set(self, key: str, value: Any) -> None:
        self._store[key] = {"value": value, "stored_at": self._now()}
        self._flush()

    def _flush(self) -> None:
        # Serialize first so a non-serializable value raises TypeError to the
        # caller (a programming error worth surfacing) before any disk work.
        payload = json.dumps(self._store)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(payload)
                os.replace(tmp, self._path)
            except OSError:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError:
            pass  # an I/O write failure must never break the caller
