import json

import pytest
from fastapi.testclient import TestClient

import main
import run_manager
import snapshot_store


@pytest.fixture()
def client(tmp_path, monkeypatch):
    signal = tmp_path / "snapshot.json"
    regime = tmp_path / "regime_snapshot.json"
    signal.write_text(json.dumps(
        {"as_of": "2026-07-14", "methodology_version": "1.0", "economies": {}}
    ))
    regime.write_text(json.dumps({"methodology_version": "1.0", "countries": []}))
    monkeypatch.setattr(snapshot_store, "SNAPSHOTS_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(snapshot_store, "SIGNAL_PATH", signal)
    monkeypatch.setattr(snapshot_store, "REGIME_PATH", regime)
    run_manager._reset_for_tests()
    return TestClient(main.app)


def test_snapshots_endpoint_seeds_baseline(client):
    body = client.get("/api/snapshots").json()
    assert len(body) == 1
    assert body[0]["meta"]["source"] == "baseline"


def test_changes_insufficient_then_available(client):
    body = client.get("/api/changes").json()
    assert body["insufficient"] is True and body["changes"] == []
    snapshot_store.archive_current(source="live", snapshot_id="2026-07-16T120000Z")
    body = client.get("/api/changes").json()
    assert body["insufficient"] is False
    assert body["target"]["id"] == "2026-07-16T120000Z"


def test_changes_with_explicit_base(client):
    client.get("/api/snapshots")  # seed
    snapshot_store.archive_current(source="live", snapshot_id="2026-07-16T120000Z")
    snapshot_store.archive_current(source="live", snapshot_id="2026-07-17T120000Z")
    body = client.get("/api/changes", params={"base": "2026-07-16T120000Z"}).json()
    assert body["base"]["id"] == "2026-07-16T120000Z"
    assert body["target"]["id"] == "2026-07-17T120000Z"


def test_changes_unknown_base_is_404(client):
    client.get("/api/snapshots")
    snapshot_store.archive_current(source="live", snapshot_id="2026-07-16T120000Z")
    assert client.get("/api/changes", params={"base": "nope"}).status_code == 404


def test_run_endpoint_starts_and_conflicts(client, monkeypatch):
    calls = []
    monkeypatch.setattr(run_manager, "start_run", lambda source: calls.append(source) or True)
    assert client.post("/api/run", json={"source": "mock"}).status_code == 202
    assert calls == ["mock"]
    monkeypatch.setattr(run_manager, "start_run", lambda source: False)
    assert client.post("/api/run", json={"source": "mock"}).status_code == 409


def test_run_rejects_bad_source(client):
    assert client.post("/api/run", json={"source": "prod"}).status_code == 422


def test_run_status_shape(client):
    body = client.get("/api/run/status").json()
    assert body["state"] == "idle"
