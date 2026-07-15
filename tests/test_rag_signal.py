from evidence_store import EvidenceRecord, EvidenceStore
from rag_signal import compute_rag_signal


def _put(store, *, content, event_time="2026-06-01T10:00:00Z", observed_at="2026-06-01T10:01:00Z"):
    store.put(
        EvidenceRecord.from_dict(
            {
                "evidence_id": "us-eq-1",
                "event_time": event_time,
                "observed_at": observed_at,
                "source": "earnings",
                "revision": "1",
                "vintage": "2026-06-01",
                "country": "United States of America",
                "asset": "equity",
                "horizon": "3m",
                "title": "Earnings update",
                "content": content,
                "source_uri": "https://example.test/earnings",
            }
        )
    )


def test_no_evidence_means_no_view_and_zero_weighting_confidence():
    with EvidenceStore(":memory:") as store:
        result = compute_rag_signal(
            "Canada", "fx", store=store, as_of="2026-06-02", horizon="3m"
        )
    assert result["signal"] == 0.0
    assert result["confidence"] == 0.0
    assert result["analysis"]["direction"] == "no_view"
    assert result["analysis"]["citations"] == ()


def test_extraction_is_structured_and_cited():
    content = "Earnings are resilient, margins are improving, and investor demand is strong."
    with EvidenceStore(":memory:") as store:
        _put(store, content=content)
        result = compute_rag_signal(
            "United States of America",
            "equity",
            store=store,
            as_of="2026-06-02",
        )
    analysis = result["analysis"]
    assert analysis["direction"] == "bullish"
    assert analysis["positive_factors"]
    assert analysis["negative_factors"] == ()
    assert analysis["evidence_count"] == 1
    citation = analysis["citations"][0]
    assert citation["evidence_id"] == "us-eq-1"
    assert citation["revision"] == "1"
    assert citation["excerpt"] in content
    assert result["sources"] == ["https://example.test/earnings"]


def test_extraction_cannot_see_evidence_observed_after_as_of():
    with EvidenceStore(":memory:") as store:
        _put(
            store,
            content="Earnings are strong.",
            observed_at="2026-06-03T10:01:00Z",
        )
        result = compute_rag_signal(
            "United States of America",
            "equity",
            store=store,
            as_of="2026-06-02",
        )
    assert result["analysis"]["direction"] == "no_view"
