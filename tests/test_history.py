import history


def _snap(as_of, econ_vals):
    """Build a minimal snapshot dict. econ_vals: {economy: {view: final}}."""
    economies = {}
    for econ, vals in econ_vals.items():
        economies[econ] = {
            "composite": {"final": vals.get("composite")},
            "signals": {v: {"final": vals[v]} for v in ("fx", "rates", "equity", "real_estate") if v in vals},
        }
    return {"as_of": as_of, "economies": economies}


def test_build_history_dedups_by_date_keeping_last_and_orders_ascending():
    snaps = [
        _snap("2026-06-02", {"United States of America": {"composite": 0.10, "fx": 0.20}}),
        _snap("2026-06-02", {"United States of America": {"composite": 0.15, "fx": 0.25}}),  # same date, later wins
        _snap("2026-06-03", {"United States of America": {"composite": 0.30, "fx": 0.40}}),
    ]
    out = history.build_history(snaps)
    assert out["as_of"] == "2026-06-03"
    assert out["views"] == ["composite", "fx", "rates", "equity", "real_estate"]
    assert out["history"]["United States of America"]["composite"] == [
        {"date": "2026-06-02", "value": 0.15},
        {"date": "2026-06-03", "value": 0.30},
    ]


def test_build_history_skips_none_and_missing_views():
    out = history.build_history([_snap("2026-06-02", {"Japan": {"composite": None, "fx": 0.5}})])
    japan = out["history"]["Japan"]
    assert "composite" not in japan                 # None is dropped
    assert japan["fx"] == [{"date": "2026-06-02", "value": 0.5}]


def test_build_history_tolerates_malformed_snapshots():
    out = history.build_history([{"foo": "bar"}, {"as_of": "2026-06-02"}])  # no economies / no as_of
    assert out["history"] == {}
    assert out["as_of"] == "2026-06-02"


def test_load_snapshots_from_git_reads_blobs_in_log_order():
    calls = []

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["git", "log"]:
            return "aaa\nbbb\n"
        if args[1] == "show":
            sha = args[2].split(":")[0]
            return '{"as_of": "2026-06-02"}' if sha == "aaa" else '{"as_of": "2026-06-03"}'
        raise AssertionError(args)

    snaps = history.load_snapshots_from_git("snapshot.json", run=fake_run)
    assert [s["as_of"] for s in snaps] == ["2026-06-02", "2026-06-03"]
    assert calls[0][:2] == ["git", "log"]
    assert "--reverse" in calls[0]


def test_load_snapshots_from_git_skips_unparseable_blobs():
    def fake_run(args):
        if args[:2] == ["git", "log"]:
            return "aaa\nbbb\n"
        return "not json" if args[2].startswith("aaa") else '{"as_of": "2026-06-03"}'

    snaps = history.load_snapshots_from_git("snapshot.json", run=fake_run)
    assert [s["as_of"] for s in snaps] == ["2026-06-03"]


def test_compute_history_wires_git_and_build():
    def fake_run(args):
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return "headsha\n"
        if args[:2] == ["git", "log"]:
            return "aaa\n"
        return '{"as_of": "2026-06-02", "economies": {"Brazil": {"composite": {"final": 0.2}, "signals": {}}}}'

    history._HISTORY_CACHE.clear()
    out = history.compute_history("snapshot.json", run=fake_run)
    assert out["history"]["Brazil"]["composite"] == [{"date": "2026-06-02", "value": 0.2}]


def test_compute_history_caches_on_head_sha():
    log_calls = []

    def fake_run(args):
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return "headsha1\n"
        if args[:2] == ["git", "log"]:
            log_calls.append(args)
            return "aaa\n"
        return '{"as_of": "2026-06-02", "economies": {"Brazil": {"composite": {"final": 0.2}, "signals": {}}}}'

    history._HISTORY_CACHE.clear()
    out1 = history.compute_history("snapshot.json", run=fake_run)
    out2 = history.compute_history("snapshot.json", run=fake_run)
    assert out1 == out2
    assert len(log_calls) == 1  # second call served from cache, no git walk


def test_compute_history_busts_cache_when_head_changes():
    head = ["sha1"]
    log_calls = []

    def fake_run(args):
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return head[0]
        if args[:2] == ["git", "log"]:
            log_calls.append(args)
            return "aaa\n"
        return '{"as_of": "2026-06-02", "economies": {}}'

    history._HISTORY_CACHE.clear()
    history.compute_history("snapshot.json", run=fake_run)
    head[0] = "sha2"  # a new snapshot commit landed
    history.compute_history("snapshot.json", run=fake_run)
    assert len(log_calls) == 2  # cache busted, git re-walked
