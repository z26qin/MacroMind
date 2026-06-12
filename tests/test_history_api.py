from fastapi.testclient import TestClient

import main


def test_api_history_returns_series():
    client = TestClient(main.app)
    resp = client.get("/api/history")
    assert resp.status_code == 200
    data = resp.json()
    assert {"as_of", "views", "history"} <= set(data)
    assert data["views"] == ["composite", "fx", "rates", "equity", "real_estate"]
    assert isinstance(data["history"], dict)
    # The repo's snapshot.json has many commits, so at least one economy has a
    # non-empty composite series.
    assert any("composite" in econ and econ["composite"] for econ in data["history"].values())
