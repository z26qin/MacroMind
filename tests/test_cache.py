import json

import pytest

from data_sources.cache import TTLCache


def _clock(value):
    box = {"t": value}
    return box, (lambda: box["t"])


def test_get_returns_none_on_miss(tmp_path):
    cache = TTLCache(tmp_path / "c.json", ttl_seconds=100)
    assert cache.get("absent") is None


def test_set_then_get_returns_value(tmp_path):
    cache = TTLCache(tmp_path / "c.json", ttl_seconds=100)
    cache.set("k", {"a": 1})
    assert cache.get("k") == {"a": 1}


def test_entry_expires_at_ttl_boundary(tmp_path):
    box, now = _clock(1000.0)
    cache = TTLCache(tmp_path / "c.json", ttl_seconds=100, now=now)
    cache.set("k", "v")
    box["t"] = 1099.0
    assert cache.get("k") == "v"      # still inside TTL
    box["t"] = 1100.0
    assert cache.get("k") is None     # now - stored_at >= ttl -> expired


def test_corrupt_file_reads_as_empty(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json", encoding="utf-8")
    cache = TTLCache(path, ttl_seconds=100)
    assert cache.get("k") is None


def test_persists_across_instances(tmp_path):
    path = tmp_path / "c.json"
    TTLCache(path, ttl_seconds=100).set("k", [1, "x"])
    reopened = TTLCache(path, ttl_seconds=100)
    assert reopened.get("k") == [1, "x"]


def test_set_writes_valid_json(tmp_path):
    path = tmp_path / "c.json"
    cache = TTLCache(path, ttl_seconds=100)
    cache.set("k", "v")
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["k"]["value"] == "v"
    assert "stored_at" in on_disk["k"]


def test_creates_parent_directory(tmp_path):
    path = tmp_path / "nested" / "dir" / "c.json"
    cache = TTLCache(path, ttl_seconds=100)
    cache.set("k", "v")
    assert path.exists()


def test_exposes_path_and_ttl(tmp_path):
    path = tmp_path / "c.json"
    cache = TTLCache(path, ttl_seconds=42)
    assert cache.path == path
    assert cache.ttl_seconds == 42


def test_set_with_non_serializable_value_raises(tmp_path):
    cache = TTLCache(tmp_path / "c.json", ttl_seconds=100)
    with pytest.raises(TypeError):
        cache.set("k", object())


def test_non_dict_json_root_reads_as_empty(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    cache = TTLCache(path, ttl_seconds=100)
    assert cache.get("k") is None
