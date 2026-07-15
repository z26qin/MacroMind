"""Point-in-time evidence ledger for narrative research.

The ledger stores every observed revision and only returns evidence that was
available at a requested decision timestamp. SQLite is deliberately used here:
it is durable, transactional, available in the Python standard library, and can
later be replaced behind the same small interface by a production data service.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_EVIDENCE_DB = Path(".cache") / "evidence.sqlite3"
DEFAULT_SEED_PATH = Path("evidence") / "seed.jsonl"


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def normalize_timestamp(value: str, field: str = "timestamp") -> str:
    """Return a UTC ISO-8601 timestamp with a stable ``Z`` suffix."""
    return _parse_timestamp(value, field).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    event_time: str
    observed_at: str
    source: str
    revision: str
    vintage: str
    country: str
    asset: str
    horizon: str
    title: str
    content: str
    source_uri: str

    def __post_init__(self) -> None:
        required = asdict(self)
        empty = sorted(key for key, value in required.items() if not str(value).strip())
        if empty:
            raise ValueError(f"Evidence record has empty required fields: {empty}")
        event = _parse_timestamp(self.event_time, "event_time")
        observed = _parse_timestamp(self.observed_at, "observed_at")
        if observed < event:
            raise ValueError("observed_at cannot be earlier than event_time")

    @classmethod
    def from_dict(cls, payload: dict) -> "EvidenceRecord":
        data = dict(payload)
        data["event_time"] = normalize_timestamp(data.get("event_time", ""), "event_time")
        data["observed_at"] = normalize_timestamp(data.get("observed_at", ""), "observed_at")
        return cls(**data)

    def to_dict(self) -> dict:
        return asdict(self)


class EvidenceStore:
    """Durable revision-aware evidence store with point-in-time retrieval."""

    def __init__(self, path: str | Path = DEFAULT_EVIDENCE_DB) -> None:
        self.path = Path(path) if str(path) != ":memory:" else Path(":memory:")
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path))
        self._connection.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS evidence (
                evidence_id TEXT NOT NULL,
                event_time TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                source TEXT NOT NULL,
                revision TEXT NOT NULL,
                vintage TEXT NOT NULL,
                country TEXT NOT NULL,
                asset TEXT NOT NULL,
                horizon TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                source_uri TEXT NOT NULL,
                PRIMARY KEY (evidence_id, revision, vintage)
            );
            CREATE INDEX IF NOT EXISTS evidence_pit_lookup
                ON evidence(country, asset, horizon, observed_at, event_time);
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "EvidenceStore":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def put(self, record: EvidenceRecord) -> None:
        self.put_many((record,))

    def put_many(self, records: Iterable[EvidenceRecord]) -> None:
        rows = [record.to_dict() for record in records]
        if not rows:
            return
        columns = tuple(EvidenceRecord.__dataclass_fields__)
        placeholders = ", ".join(f":{column}" for column in columns)
        self._connection.executemany(
            f"INSERT OR REPLACE INTO evidence ({', '.join(columns)}) VALUES ({placeholders})",
            rows,
        )
        self._connection.commit()

    def ingest_jsonl(self, path: str | Path) -> int:
        """Ingest JSONL revisions idempotently and return the row count read."""
        records: list[EvidenceRecord] = []
        for line_number, line in enumerate(
            Path(path).read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                records.append(EvidenceRecord.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid evidence JSONL at {path}:{line_number}: {exc}") from exc
        self.put_many(records)
        return len(records)

    def query(
        self,
        *,
        country: str,
        asset: str,
        horizon: str,
        as_of: str,
        limit: int = 20,
    ) -> list[EvidenceRecord]:
        """Return the latest known revision of matching evidence at ``as_of``.

        Both event time and observation time are filtered. The observation-time
        check is the critical point-in-time guard for delayed feeds and revisions.
        """
        decision_time = normalize_timestamp(as_of, "as_of")
        rows = self._connection.execute(
            """
            WITH available AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY evidence_id
                    ORDER BY observed_at DESC, revision DESC, vintage DESC
                ) AS revision_rank
                FROM evidence
                WHERE country = :country
                  AND asset IN (:asset, 'macro', 'cross_asset')
                  AND horizon IN (:horizon, 'all')
                  AND event_time <= :decision_time
                  AND observed_at <= :decision_time
            )
            SELECT evidence_id, event_time, observed_at, source, revision, vintage,
                   country, asset, horizon, title, content, source_uri
            FROM available
            WHERE revision_rank = 1
            ORDER BY observed_at DESC, event_time DESC, evidence_id
            LIMIT :limit
            """,
            {
                "country": country,
                "asset": asset,
                "horizon": horizon,
                "decision_time": decision_time,
                "limit": int(limit),
            },
        ).fetchall()
        return [EvidenceRecord.from_dict(dict(row)) for row in rows]

    def count(self) -> int:
        return int(self._connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0])


def open_default_evidence_store(
    path: str | Path = DEFAULT_EVIDENCE_DB,
    seed_path: str | Path = DEFAULT_SEED_PATH,
) -> EvidenceStore:
    """Open the runtime ledger and idempotently ingest the repository seed set."""
    store = EvidenceStore(path)
    seed = Path(seed_path)
    if seed.exists():
        store.ingest_jsonl(seed)
    return store


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Manage the point-in-time evidence ledger.")
    parser.add_argument("--db", default=str(DEFAULT_EVIDENCE_DB), help="SQLite database path")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest", help="Idempotently ingest an evidence JSONL file")
    ingest.add_argument("path")
    query = commands.add_parser("query", help="Query evidence known at a decision timestamp")
    query.add_argument("--country", required=True)
    query.add_argument("--asset", required=True)
    query.add_argument("--horizon", required=True)
    query.add_argument("--as-of", required=True)
    query.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    with EvidenceStore(args.db) as store:
        if args.command == "ingest":
            count = store.ingest_jsonl(args.path)
            print(json.dumps({"ingested": count, "stored_revisions": store.count()}))
        else:
            records = store.query(
                country=args.country,
                asset=args.asset,
                horizon=args.horizon,
                as_of=args.as_of,
                limit=args.limit,
            )
            print(json.dumps([record.to_dict() for record in records], indent=2))


if __name__ == "__main__":
    main()
