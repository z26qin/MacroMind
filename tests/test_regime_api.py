from fastapi.testclient import TestClient

import main


def test_api_regime_returns_snapshot():
    client = TestClient(main.app)
    resp = client.get("/api/regime")
    assert resp.status_code == 200
    data = resp.json()
    assert {"as_of", "methodology_version", "regime_universe", "countries"} <= set(data)
    assert len(data["countries"]) == 6
    argentina = data["countries"]["Argentina"]
    assert argentina["verdict"] == "Repricing"
    assert argentina["best_expressions"]
    assert set(argentina["cross_asset_confirmation"])  # non-empty
