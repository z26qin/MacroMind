from fastapi.testclient import TestClient

import main


def test_cited_local_document_is_served():
    response = TestClient(main.app).get("/documents/us_equity.txt")
    assert response.status_code == 200
    assert "AI infrastructure spending" in response.text
