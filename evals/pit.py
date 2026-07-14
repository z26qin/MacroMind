"""Point-in-time leakage detection for retrieved evidence."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence

from evidence_store import EvidenceRecord, normalize_timestamp


@dataclass(frozen=True)
class LeakViolation:
    evidence_id: str
    field: str
    value: str
    decision_time: str


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(normalize_timestamp(value).replace("Z", "+00:00"))


def _as_dict(record: EvidenceRecord | Mapping) -> Mapping:
    return record.to_dict() if isinstance(record, EvidenceRecord) else record


def find_lookahead_violations(
    records: Sequence[EvidenceRecord | Mapping], decision_time_iso: str
) -> list[LeakViolation]:
    cutoff = _timestamp(decision_time_iso)
    violations: list[LeakViolation] = []
    for item in records:
        record = _as_dict(item)
        evidence_id = str(record.get("evidence_id", "?"))
        for field in ("event_time", "observed_at"):
            value = record.get(field)
            if value and _timestamp(str(value)) > cutoff:
                violations.append(
                    LeakViolation(evidence_id, field, str(value), decision_time_iso)
                )
    return violations


def assert_no_lookahead(
    records: Sequence[EvidenceRecord | Mapping], decision_time_iso: str
) -> None:
    violations = find_lookahead_violations(records, decision_time_iso)
    assert not violations, f"Point-in-time leakage: {violations}"
