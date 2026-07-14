import json

import pytest

from evidence_store import EvidenceRecord, EvidenceStore


def _record(**overrides):
    values = {
        "evidence_id": "event-1",
        "event_time": "2026-06-01T10:00:00Z",
        "observed_at": "2026-06-01T10:05:00Z",
        "source": "central_bank",
        "revision": "1",
        "vintage": "2026-06-01",
        "country": "Japan",
        "asset": "fx",
        "horizon": "3m",
        "title": "Policy statement",
        "content": "Policy normalization is cautious.",
        "source_uri": "https://example.test/event-1",
    }
    values.update(overrides)
    return EvidenceRecord.from_dict(values)


def test_record_requires_all_point_in_time_dimensions():
    payload = _record().to_dict()
    payload["vintage"] = ""
    with pytest.raises(ValueError, match="vintage"):
        EvidenceRecord.from_dict(payload)


def test_observed_at_cannot_precede_event_time():
    with pytest.raises(ValueError, match="observed_at"):
        _record(observed_at="2026-06-01T09:59:00Z")


def test_query_excludes_unobserved_and_future_events():
    with EvidenceStore(":memory:") as store:
        store.put_many(
            [
                _record(evidence_id="known"),
                _record(
                    evidence_id="delayed",
                    event_time="2026-06-01T09:00:00Z",
                    observed_at="2026-06-01T12:00:00Z",
                ),
                _record(
                    evidence_id="future",
                    event_time="2026-06-02T09:00:00Z",
                    observed_at="2026-06-02T09:01:00Z",
                ),
            ]
        )
        found = store.query(
            country="Japan",
            asset="fx",
            horizon="3m",
            as_of="2026-06-01T11:00:00Z",
        )
    assert [record.evidence_id for record in found] == ["known"]


def test_query_returns_latest_revision_known_at_decision_time():
    with EvidenceStore(":memory:") as store:
        store.put_many(
            [
                _record(revision="1", vintage="2026-06-01", content="Initial release."),
                _record(
                    revision="2",
                    vintage="2026-06-02",
                    observed_at="2026-06-02T10:00:00Z",
                    content="Revised release.",
                ),
            ]
        )
        before = store.query(
            country="Japan", asset="fx", horizon="3m", as_of="2026-06-01T23:00:00Z"
        )
        after = store.query(
            country="Japan", asset="fx", horizon="3m", as_of="2026-06-02T11:00:00Z"
        )
    assert before[0].revision == "1"
    assert before[0].content == "Initial release."
    assert after[0].revision == "2"
    assert after[0].content == "Revised release."


def test_query_includes_country_macro_evidence_for_an_asset():
    with EvidenceStore(":memory:") as store:
        store.put(_record(asset="macro", horizon="all"))
        found = store.query(
            country="Japan", asset="rates", horizon="6m", as_of="2026-06-02T00:00:00Z"
        )
    assert len(found) == 1


def test_jsonl_ingestion_is_idempotent(tmp_path):
    path = tmp_path / "evidence.jsonl"
    path.write_text(json.dumps(_record().to_dict()) + "\n", encoding="utf-8")
    with EvidenceStore(tmp_path / "evidence.sqlite3") as store:
        assert store.ingest_jsonl(path) == 1
        assert store.ingest_jsonl(path) == 1
        assert store.count() == 1
