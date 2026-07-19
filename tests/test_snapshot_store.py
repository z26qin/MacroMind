import json

import pytest

import snapshot_store


@pytest.fixture()
def working_files(tmp_path):
    signal = tmp_path / "snapshot.json"
    regime = tmp_path / "regime_snapshot.json"
    signal.write_text(json.dumps({"as_of": "2026-07-14", "economies": {}}))
    regime.write_text(json.dumps({"as_of": "2026-07-14", "countries": []}))
    return signal, regime


def _archive(tmp_path, working_files, snapshot_id):
    signal, regime = working_files
    return snapshot_store.archive_current(
        source="live",
        snapshots_dir=tmp_path / "snapshots",
        signal_path=signal,
        regime_path=regime,
        snapshot_id=snapshot_id,
    )


def test_archive_then_list_roundtrip(tmp_path, working_files):
    snapshot_id = _archive(tmp_path, working_files, "2026-07-15T120000Z")
    entries = snapshot_store.list_snapshots(tmp_path / "snapshots")
    assert snapshot_id == "2026-07-15T120000Z"
    assert [e["id"] for e in entries] == ["2026-07-15T120000Z"]
    assert entries[0]["as_of"] == "2026-07-14"
    assert entries[0]["meta"]["source"] == "live"


def test_list_ignores_partial_and_hidden_dirs(tmp_path, working_files):
    _archive(tmp_path, working_files, "2026-07-15T120000Z")
    partial = tmp_path / "snapshots" / "2026-07-16T000000Z"
    partial.mkdir()
    (partial / "snapshot.json").write_text("{}")  # regime missing -> partial
    hidden = tmp_path / "snapshots" / ".tmp-broken"
    hidden.mkdir()
    assert [e["id"] for e in snapshot_store.list_snapshots(tmp_path / "snapshots")] == [
        "2026-07-15T120000Z"
    ]


def test_archive_refuses_duplicate_id(tmp_path, working_files):
    _archive(tmp_path, working_files, "2026-07-15T120000Z")
    with pytest.raises(FileExistsError):
        _archive(tmp_path, working_files, "2026-07-15T120000Z")


def test_latest_pair_needs_two(tmp_path, working_files):
    assert snapshot_store.latest_pair(tmp_path / "snapshots") is None
    _archive(tmp_path, working_files, "2026-07-15T120000Z")
    assert snapshot_store.latest_pair(tmp_path / "snapshots") is None
    _archive(tmp_path, working_files, "2026-07-16T120000Z")
    base, target = snapshot_store.latest_pair(tmp_path / "snapshots")
    assert base["id"] == "2026-07-15T120000Z"
    assert target["id"] == "2026-07-16T120000Z"


def test_load_snapshot_shape(tmp_path, working_files):
    _archive(tmp_path, working_files, "2026-07-15T120000Z")
    loaded = snapshot_store.load_snapshot(
        "2026-07-15T120000Z", tmp_path / "snapshots"
    )
    assert loaded["id"] == "2026-07-15T120000Z"
    assert loaded["signal"]["as_of"] == "2026-07-14"
    assert loaded["regime"]["countries"] == []


def test_seed_baseline_only_when_empty(tmp_path, working_files):
    signal, regime = working_files
    kwargs = dict(
        snapshots_dir=tmp_path / "snapshots", signal_path=signal, regime_path=regime
    )
    seeded = snapshot_store.seed_baseline_if_empty(**kwargs)
    assert seeded == "2026-07-14T000000Z-baseline"
    assert snapshot_store.seed_baseline_if_empty(**kwargs) is None  # 已有归档,不再种
    entries = snapshot_store.list_snapshots(tmp_path / "snapshots")
    assert entries[0]["meta"]["source"] == "baseline"
