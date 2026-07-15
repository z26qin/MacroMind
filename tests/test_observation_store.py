from datetime import datetime, timedelta, timezone

import pytest

from pipeline.contracts import (
    DataFrequency,
    Observation,
    PipelineRunContext,
    RunMode,
    SourceBatch,
    SourceError,
)
from pipeline.store import ImmutableStoreConflict, ObservationStore


UTC = timezone.utc
BASE = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _context(run_id: str, *, started_at: datetime = BASE) -> PipelineRunContext:
    return PipelineRunContext(
        run_id=run_id,
        as_of=started_at,
        started_at=started_at,
        mode=RunMode.LIVE,
        methodology_version="v0.2",
        config_hash="test-config",
    )


def _observation(
    source: str,
    metric: str,
    *,
    country: str = "Canada",
    value: float = 1.0,
    observed_at: datetime = BASE,
    event_time: datetime | None = BASE - timedelta(days=1),
    revision: str = "1",
    vintage: str | None = None,
) -> Observation:
    return Observation(
        metric=metric,
        value=value,
        unit="percent",
        country=country,
        frequency=DataFrequency.ANNUAL,
        period_start=datetime(2025, 1, 1, tzinfo=UTC),
        period_end=datetime(2025, 12, 31, tzinfo=UTC),
        event_time=event_time,
        observed_at=observed_at,
        source=source,
        revision=revision,
        vintage=vintage or observed_at.isoformat(),
    )


def _batch(
    context: PipelineRunContext,
    source: str,
    observations: tuple[Observation, ...] = (),
    *,
    errors: tuple[SourceError, ...] = (),
    expected_count: int | None = None,
) -> SourceBatch:
    completed_at = max(
        (observation.observed_at for observation in observations),
        default=context.started_at,
    )
    return SourceBatch(
        run_id=context.run_id,
        source=source,
        expected_observation_count=expected_count or max(1, len(observations)),
        requested_at=context.started_at,
        completed_at=completed_at,
        observations=observations,
        errors=errors,
    )


def test_write_run_persists_all_four_sources_and_raw_manifest():
    context = _context("run-all-sources")
    batches = (
        _batch(context, "world_bank", (_observation("world_bank", "gdp_growth"),)),
        _batch(context, "imf_weo", (_observation("imf_weo", "gdp_growth"),)),
        _batch(context, "yahoo", (_observation("yahoo", "equity_3m_return"),)),
        _batch(
            context,
            "gdelt",
            (_observation("gdelt", "news_pressure"),),
            errors=(
                SourceError(
                    code="missing_series",
                    message="Japan missing",
                    country="Japan",
                    metric="news_pressure",
                ),
            ),
            expected_count=2,
        ),
    )

    with ObservationStore(":memory:") as store:
        result = store.write_run(context, batches)
        counts = store.counts()
        manifest = store.run_manifest(context.run_id)

    assert result.inserted_runs == 1
    assert result.inserted_batches == 4
    assert result.inserted_observations == 4
    assert result.inserted_errors == 1
    assert counts == {
        "pipeline_runs": 1,
        "source_batches": 4,
        "source_errors": 1,
        "observations": 4,
    }
    assert manifest is not None
    assert manifest["context"]["run_id"] == context.run_id
    assert [batch["source"] for batch in manifest["batches"]] == [
        "gdelt",
        "imf_weo",
        "world_bank",
        "yahoo",
    ]
    gdelt_batch = manifest["batches"][0]
    assert gdelt_batch["errors"][0]["code"] == "missing_series"
    assert gdelt_batch["observations"][0]["metric"] == "news_pressure"


def test_rewriting_identical_run_is_idempotent(tmp_path):
    context = _context("run-idempotent")
    batch = _batch(context, "world_bank", (_observation("world_bank", "gdp_growth"),))
    path = tmp_path / "raw.sqlite3"

    with ObservationStore(path) as store:
        first = store.write_run(context, (batch,))
    with ObservationStore(path) as store:
        second = store.write_run(context, (batch,))
        counts = store.counts()

    assert first.inserted_observations == 1
    assert second.inserted_runs == 0
    assert second.inserted_batches == 0
    assert second.inserted_observations == 0
    assert counts["observations"] == 1


def test_immutable_identity_rejects_changed_raw_content():
    context = _context("run-conflict")
    original = _batch(
        context,
        "world_bank",
        (_observation("world_bank", "gdp_growth", value=1.0),),
    )
    changed = _batch(
        context,
        "world_bank",
        (_observation("world_bank", "gdp_growth", value=9.0),),
    )

    with ObservationStore(":memory:") as store:
        store.write_run(context, (original,))
        with pytest.raises(ImmutableStoreConflict, match="source_batches"):
            store.write_run(context, (changed,))
        found = store.query_as_of(BASE, source="world_bank")

    assert found[0].observation.value == 1.0


def test_multi_batch_conflict_rolls_back_the_entire_write():
    context = _context("run-atomic")
    original = _batch(
        context,
        "world_bank",
        (_observation("world_bank", "gdp_growth", value=1.0),),
    )
    yahoo = _batch(
        context,
        "yahoo",
        (_observation("yahoo", "equity_3m_return", value=2.0),),
    )
    changed_world_bank = _batch(
        context,
        "world_bank",
        (_observation("world_bank", "gdp_growth", value=9.0),),
    )

    with ObservationStore(":memory:") as store:
        store.write_run(context, (original,))
        with pytest.raises(ImmutableStoreConflict):
            store.write_run(context, (yahoo, changed_world_bank))
        counts = store.counts()

    assert counts["source_batches"] == 1
    assert counts["observations"] == 1


def test_pit_query_returns_latest_revision_known_at_decision_time():
    first_seen = BASE
    revised_at = BASE + timedelta(days=2)
    first_context = _context("run-revision-1", started_at=first_seen)
    revised_context = _context("run-revision-2", started_at=revised_at)
    initial = _observation(
        "world_bank",
        "gdp_growth",
        value=1.0,
        observed_at=first_seen,
        revision="1",
    )
    revised = _observation(
        "world_bank",
        "gdp_growth",
        value=1.5,
        observed_at=revised_at,
        revision="2",
    )

    with ObservationStore(":memory:") as store:
        store.write_run(first_context, (_batch(first_context, "world_bank", (initial,)),))
        store.write_run(revised_context, (_batch(revised_context, "world_bank", (revised,)),))
        before = store.query_as_of(BASE + timedelta(days=1), metric="gdp_growth")
        after = store.query_as_of(BASE + timedelta(days=3), metric="gdp_growth")
        history = store.query_as_of(
            BASE + timedelta(days=3),
            metric="gdp_growth",
            latest_only=False,
        )

    assert len(before) == 1
    assert before[0].observation.revision == "1"
    assert before[0].observation.value == 1.0
    assert len(after) == 1
    assert after[0].observation.revision == "2"
    assert after[0].observation.value == 1.5
    assert [record.observation.revision for record in history] == ["2", "1"]


def test_pit_query_excludes_delayed_observations_until_observed():
    observed_at = BASE + timedelta(days=3)
    context = _context("run-delayed", started_at=observed_at)
    delayed = _observation(
        "world_bank",
        "gdp_growth",
        event_time=BASE,
        observed_at=observed_at,
    )

    with ObservationStore(":memory:") as store:
        store.write_run(context, (_batch(context, "world_bank", (delayed,)),))
        hidden = store.query_as_of(BASE + timedelta(days=2))
        visible = store.query_as_of(BASE + timedelta(days=4))

    assert hidden == ()
    assert len(visible) == 1


def test_pit_query_uses_observed_at_when_event_time_is_unknown():
    observed_at = BASE + timedelta(days=1)
    context = _context("run-unknown-event", started_at=observed_at)
    observation = _observation(
        "imf_weo",
        "gdp_growth",
        event_time=None,
        observed_at=observed_at,
    )

    with ObservationStore(":memory:") as store:
        store.write_run(context, (_batch(context, "imf_weo", (observation,)),))
        hidden = store.query_as_of(BASE)
        visible = store.query_as_of(observed_at)

    assert hidden == ()
    assert visible[0].observation.event_time is None


def test_pit_query_cannot_see_backdated_row_before_run_and_batch_exist():
    future_ingestion = BASE + timedelta(days=3)
    context = _context("run-backdated", started_at=future_ingestion)
    backdated = _observation(
        "world_bank",
        "gdp_growth",
        event_time=BASE - timedelta(days=1),
        observed_at=BASE,
    )
    batch = SourceBatch(
        run_id=context.run_id,
        source="world_bank",
        expected_observation_count=1,
        requested_at=future_ingestion,
        completed_at=future_ingestion,
        observations=(backdated,),
    )

    with ObservationStore(":memory:") as store:
        store.write_run(context, (batch,))
        before_ingestion = store.query_as_of(BASE + timedelta(days=1))
        after_ingestion = store.query_as_of(BASE + timedelta(days=4))

    assert before_ingestion == ()
    assert len(after_ingestion) == 1


def test_pit_query_excludes_future_event_even_if_raw_row_was_inserted():
    event_time = BASE + timedelta(days=2)
    observed_at = BASE + timedelta(days=3)
    context = _context("run-future-event", started_at=observed_at)
    observation = _observation(
        "gdelt",
        "news_pressure",
        event_time=event_time,
        observed_at=observed_at,
    )

    with ObservationStore(":memory:") as store:
        store.write_run(context, (_batch(context, "gdelt", (observation,)),))
        before_event = store.query_as_of(BASE + timedelta(days=1))
        after_observation = store.query_as_of(BASE + timedelta(days=4))

    assert before_event == ()
    assert len(after_observation) == 1


def test_query_filters_and_positive_limit_are_enforced():
    context = _context("run-filters")
    batch = _batch(
        context,
        "world_bank",
        (
            _observation("world_bank", "gdp_growth", country="Canada"),
            _observation("world_bank", "inflation_yoy", country="Japan"),
        ),
        expected_count=2,
    )

    with ObservationStore(":memory:") as store:
        store.write_run(context, (batch,))
        canada = store.query_as_of(BASE, country="Canada", limit=1)
        with pytest.raises(ValueError, match="positive"):
            store.query_as_of(BASE, limit=0)

    assert len(canada) == 1
    assert canada[0].observation.metric == "gdp_growth"


def test_store_rejects_duplicate_sources_and_observation_identities():
    context = _context("run-duplicates")
    observation = _observation("world_bank", "gdp_growth")
    batch = _batch(context, "world_bank", (observation,))
    duplicate_observations = _batch(
        context,
        "world_bank",
        (observation, observation),
        expected_count=2,
    )

    with ObservationStore(":memory:") as store:
        with pytest.raises(ValueError, match="duplicate source"):
            store.write_run(context, (batch, batch))
        with pytest.raises(ValueError, match="duplicate observation"):
            store.write_run(context, (duplicate_observations,))
        assert store.counts()["pipeline_runs"] == 0


def test_store_rejects_batch_from_another_run():
    context = _context("run-a")
    other_context = _context("run-b")
    batch = _batch(
        other_context,
        "world_bank",
        (_observation("world_bank", "gdp_growth"),),
    )

    with ObservationStore(":memory:") as store:
        with pytest.raises(ValueError, match="run_id"):
            store.write_run(context, (batch,))
