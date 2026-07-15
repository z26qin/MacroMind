"""Durable raw observation ledger with point-in-time retrieval."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from pipeline.contracts import (
    Observation,
    PipelineRunContext,
    SourceBatch,
)


DEFAULT_OBSERVATION_DB = Path(".cache") / "pipeline_raw.sqlite3"


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _utc(value, "timestamp").isoformat().replace("+00:00", "Z")


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _json(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class ImmutableStoreConflict(ValueError):
    """Raised when an existing raw identity is rewritten with different data."""


@dataclass(frozen=True)
class StoreWriteResult:
    run_id: str
    inserted_runs: int
    inserted_batches: int
    inserted_observations: int
    inserted_errors: int


@dataclass(frozen=True)
class StoredObservation:
    run_id: str
    observation: Observation

    def to_dict(self) -> dict[str, object]:
        return {"run_id": self.run_id, **self.observation.to_dict()}


class ObservationStore:
    """Append-only run, batch, error, and observation store.

    Rewriting the exact same run is idempotent. Reusing a stored identity with
    different content raises ``ImmutableStoreConflict`` instead of mutating raw
    history. All batches in ``write_run`` commit or roll back together.
    """

    def __init__(self, path: str | Path = DEFAULT_OBSERVATION_DB) -> None:
        self.path = Path(path) if str(path) != ":memory:" else Path(":memory:")
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                run_id TEXT PRIMARY KEY,
                as_of TEXT NOT NULL,
                started_at TEXT NOT NULL,
                mode TEXT NOT NULL,
                methodology_version TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                context_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS source_batches (
                run_id TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                expected_observation_count INTEGER NOT NULL,
                observation_count INTEGER NOT NULL,
                coverage REAL NOT NULL,
                requested_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                batch_json TEXT NOT NULL,
                PRIMARY KEY (run_id, source),
                FOREIGN KEY (run_id) REFERENCES pipeline_runs(run_id)
            );

            CREATE TABLE IF NOT EXISTS source_errors (
                run_id TEXT NOT NULL,
                source TEXT NOT NULL,
                error_index INTEGER NOT NULL,
                code TEXT NOT NULL,
                message TEXT NOT NULL,
                retryable INTEGER NOT NULL,
                country TEXT,
                metric TEXT,
                error_json TEXT NOT NULL,
                PRIMARY KEY (run_id, source, error_index),
                FOREIGN KEY (run_id, source) REFERENCES source_batches(run_id, source)
            );

            CREATE TABLE IF NOT EXISTS observations (
                run_id TEXT NOT NULL,
                source TEXT NOT NULL,
                country TEXT NOT NULL,
                metric TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT NOT NULL,
                frequency TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                event_time TEXT,
                observed_at TEXT NOT NULL,
                revision TEXT NOT NULL,
                vintage TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (
                    run_id, source, country, metric, period_start,
                    period_end, revision, vintage
                ),
                FOREIGN KEY (run_id, source) REFERENCES source_batches(run_id, source)
            );

            CREATE INDEX IF NOT EXISTS observations_pit_lookup
                ON observations(country, metric, source, observed_at, event_time);
            CREATE INDEX IF NOT EXISTS observations_series_lookup
                ON observations(source, country, metric, period_start, period_end, observed_at);
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "ObservationStore":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    @staticmethod
    def _validate_batches(
        context: PipelineRunContext,
        batches: tuple[SourceBatch, ...],
    ) -> None:
        if not isinstance(context, PipelineRunContext):
            raise TypeError("context must be a PipelineRunContext")
        if not batches:
            raise ValueError("batches must contain at least one SourceBatch")
        if any(not isinstance(batch, SourceBatch) for batch in batches):
            raise TypeError("batches must contain SourceBatch values")
        if any(batch.run_id != context.run_id for batch in batches):
            raise ValueError("every batch run_id must match the PipelineRunContext")
        sources = [batch.source for batch in batches]
        if len(set(sources)) != len(sources):
            raise ValueError("a run cannot contain duplicate source batches")

        for batch in batches:
            keys = [
                (
                    observation.source,
                    observation.country,
                    observation.metric,
                    _iso(observation.period_start),
                    _iso(observation.period_end),
                    observation.revision,
                    observation.vintage,
                )
                for observation in batch.observations
            ]
            if len(set(keys)) != len(keys):
                raise ValueError(f"batch {batch.source!r} contains duplicate observation identities")

    def _insert_immutable(
        self,
        *,
        table: str,
        identity_where: str,
        identity_params: dict[str, object],
        payload_column: str,
        payload: str,
        insert_sql: str,
        insert_params: dict[str, object],
    ) -> bool:
        existing = self._connection.execute(
            f"SELECT {payload_column} FROM {table} WHERE {identity_where}",
            identity_params,
        ).fetchone()
        if existing is not None:
            if existing[payload_column] != payload:
                raise ImmutableStoreConflict(
                    f"Immutable {table} identity already exists with different content: {identity_params!r}"
                )
            return False
        self._connection.execute(insert_sql, insert_params)
        return True

    def write_run(
        self,
        context: PipelineRunContext,
        batches: Iterable[SourceBatch],
    ) -> StoreWriteResult:
        """Atomically persist one run and every normalized source result."""
        source_batches = tuple(batches)
        self._validate_batches(context, source_batches)
        context_payload = _json(context.to_dict())
        inserted_runs = 0
        inserted_batches = 0
        inserted_observations = 0
        inserted_errors = 0

        with self._connection:
            inserted_runs += self._insert_immutable(
                table="pipeline_runs",
                identity_where="run_id = :run_id",
                identity_params={"run_id": context.run_id},
                payload_column="context_json",
                payload=context_payload,
                insert_sql="""
                    INSERT INTO pipeline_runs (
                        run_id, as_of, started_at, mode,
                        methodology_version, config_hash, context_json
                    ) VALUES (
                        :run_id, :as_of, :started_at, :mode,
                        :methodology_version, :config_hash, :context_json
                    )
                """,
                insert_params={**context.to_dict(), "context_json": context_payload},
            )

            for batch in source_batches:
                batch_payload = _json(batch.to_dict())
                inserted_batches += self._insert_immutable(
                    table="source_batches",
                    identity_where="run_id = :run_id AND source = :source",
                    identity_params={"run_id": context.run_id, "source": batch.source},
                    payload_column="batch_json",
                    payload=batch_payload,
                    insert_sql="""
                        INSERT INTO source_batches (
                            run_id, source, status, expected_observation_count,
                            observation_count, coverage, requested_at,
                            completed_at, batch_json
                        ) VALUES (
                            :run_id, :source, :status, :expected_observation_count,
                            :observation_count, :coverage, :requested_at,
                            :completed_at, :batch_json
                        )
                    """,
                    insert_params={
                        "run_id": batch.run_id,
                        "source": batch.source,
                        "status": batch.status.value,
                        "expected_observation_count": batch.expected_observation_count,
                        "observation_count": len(batch.observations),
                        "coverage": batch.coverage,
                        "requested_at": _iso(batch.requested_at),
                        "completed_at": _iso(batch.completed_at),
                        "batch_json": batch_payload,
                    },
                )

                for error_index, error in enumerate(batch.errors):
                    error_dict = error.to_dict()
                    error_payload = _json(error_dict)
                    cursor = self._connection.execute(
                        """
                        INSERT OR IGNORE INTO source_errors (
                            run_id, source, error_index, code, message, retryable,
                            country, metric, error_json
                        ) VALUES (
                            :run_id, :source, :error_index, :code, :message, :retryable,
                            :country, :metric, :error_json
                        )
                        """,
                        {
                            "run_id": batch.run_id,
                            "source": batch.source,
                            "error_index": error_index,
                            **error_dict,
                            "retryable": int(error.retryable),
                            "error_json": error_payload,
                        },
                    )
                    inserted_errors += cursor.rowcount

                for observation in batch.observations:
                    payload_dict = observation.to_dict()
                    payload = _json(payload_dict)
                    identity = {
                        "run_id": batch.run_id,
                        "source": observation.source,
                        "country": observation.country,
                        "metric": observation.metric,
                        "period_start": _iso(observation.period_start),
                        "period_end": _iso(observation.period_end),
                        "revision": observation.revision,
                        "vintage": observation.vintage,
                    }
                    inserted_observations += self._insert_immutable(
                        table="observations",
                        identity_where=(
                            "run_id = :run_id AND source = :source AND country = :country "
                            "AND metric = :metric AND period_start = :period_start "
                            "AND period_end = :period_end AND revision = :revision "
                            "AND vintage = :vintage"
                        ),
                        identity_params=identity,
                        payload_column="payload_json",
                        payload=payload,
                        insert_sql="""
                            INSERT INTO observations (
                                run_id, source, country, metric, value, unit,
                                frequency, period_start, period_end, event_time,
                                observed_at, revision, vintage, payload_json
                            ) VALUES (
                                :run_id, :source, :country, :metric, :value, :unit,
                                :frequency, :period_start, :period_end, :event_time,
                                :observed_at, :revision, :vintage, :payload_json
                            )
                        """,
                        insert_params={
                            **identity,
                            "value": observation.value,
                            "unit": observation.unit,
                            "frequency": observation.frequency.value,
                            "event_time": _iso(observation.event_time),
                            "observed_at": _iso(observation.observed_at),
                            "payload_json": payload,
                        },
                    )

        return StoreWriteResult(
            run_id=context.run_id,
            inserted_runs=inserted_runs,
            inserted_batches=inserted_batches,
            inserted_observations=inserted_observations,
            inserted_errors=inserted_errors,
        )

    def query_as_of(
        self,
        as_of: datetime,
        *,
        source: str | None = None,
        country: str | None = None,
        metric: str | None = None,
        latest_only: bool = True,
        limit: int | None = None,
    ) -> tuple[StoredObservation, ...]:
        """Return only revisions that MacroMind could have known at ``as_of``."""
        decision_time = _iso(_utc(as_of, "as_of"))
        if limit is not None and (not isinstance(limit, int) or limit <= 0):
            raise ValueError("limit must be a positive integer")

        conditions = [
            "observed_at <= :decision_time",
            "(event_time IS NULL OR event_time <= :decision_time)",
            """EXISTS (
                SELECT 1
                FROM source_batches AS availability_batch
                JOIN pipeline_runs AS availability_run
                  ON availability_run.run_id = availability_batch.run_id
                WHERE availability_batch.run_id = observations.run_id
                  AND availability_batch.source = observations.source
                  AND availability_batch.completed_at <= :decision_time
                  AND availability_run.started_at <= :decision_time
            )""",
        ]
        params: dict[str, object] = {"decision_time": decision_time}
        for field_name, value in (("source", source), ("country", country), ("metric", metric)):
            if value is not None:
                normalized = str(value).strip()
                if not normalized:
                    raise ValueError(f"{field_name} must be non-empty when provided")
                conditions.append(f"{field_name} = :{field_name}")
                params[field_name] = normalized

        columns = (
            "run_id, source, country, metric, value, unit, frequency, "
            "period_start, period_end, event_time, observed_at, revision, vintage"
        )
        where = " AND ".join(conditions)
        if latest_only:
            sql = f"""
                WITH available AS (
                    SELECT {columns}, ROW_NUMBER() OVER (
                        PARTITION BY source, country, metric, period_start, period_end
                        ORDER BY observed_at DESC, vintage DESC, revision DESC, run_id DESC
                    ) AS revision_rank
                    FROM observations
                    WHERE {where}
                )
                SELECT {columns}
                FROM available
                WHERE revision_rank = 1
                ORDER BY country, metric, period_end DESC, observed_at DESC
            """
        else:
            sql = f"""
                SELECT {columns}
                FROM observations
                WHERE {where}
                ORDER BY country, metric, period_end DESC, observed_at DESC,
                         vintage DESC, revision DESC, run_id DESC
            """
        if limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = limit

        rows = self._connection.execute(sql, params).fetchall()
        return tuple(self._stored_observation(row) for row in rows)

    @staticmethod
    def _stored_observation(row: sqlite3.Row) -> StoredObservation:
        return StoredObservation(
            run_id=row["run_id"],
            observation=Observation(
                metric=row["metric"],
                value=row["value"],
                unit=row["unit"],
                country=row["country"],
                frequency=row["frequency"],
                period_start=_datetime(row["period_start"]),
                period_end=_datetime(row["period_end"]),
                event_time=None if row["event_time"] is None else _datetime(row["event_time"]),
                observed_at=_datetime(row["observed_at"]),
                source=row["source"],
                revision=row["revision"],
                vintage=row["vintage"],
            ),
        )

    def run_manifest(self, run_id: str) -> dict[str, object] | None:
        """Return the exact stored context and raw batch payloads for audit."""
        run = self._connection.execute(
            "SELECT context_json FROM pipeline_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if run is None:
            return None
        batches = self._connection.execute(
            "SELECT batch_json FROM source_batches WHERE run_id = ? ORDER BY source",
            (run_id,),
        ).fetchall()
        return {
            "context": json.loads(run["context_json"]),
            "batches": [json.loads(batch["batch_json"]) for batch in batches],
        }

    def counts(self) -> dict[str, int]:
        return {
            table: int(self._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("pipeline_runs", "source_batches", "source_errors", "observations")
        }
