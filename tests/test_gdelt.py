from urllib.parse import parse_qs, urlparse

from data_sources import gdelt
from data_sources.cache import TTLCache


def test_build_query_combines_economy_and_pressure_terms():
    query = gdelt.build_query("Brazil", gdelt.STRESS_TERMS)
    assert "Brazil" in query
    assert "inflation" in query
    assert "OR" in query


def test_build_url_uses_doc_article_list_mode():
    url = gdelt.build_url("Canada inflation", lookback="3d", max_records=25)
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert parsed.netloc == "api.gdeltproject.org"
    assert params["mode"] == ["artlist"]
    assert params["format"] == ["json"]
    assert params["timespan"] == ["3d"]
    assert params["maxrecords"] == ["25"]


def test_article_count_counts_articles_from_payload():
    captured = {}

    def fake_fetch(url):
        captured["url"] = url
        return {"articles": [{"url": "a"}, {"url": "b"}]}

    assert gdelt.article_count("Japan recession", fetch_json=fake_fetch) == 2
    assert "Japan" in captured["url"]


def test_article_count_accepts_total_count_payloads():
    assert gdelt.article_count("q", fetch_json=lambda url: {"totalArticles": 7}) == 7
    assert gdelt.article_count("q", fetch_json=lambda url: {"count": 3}) == 3


def test_pressure_score_scales_stress_minus_relief():
    assert gdelt.pressure_score(9, 0) == 3.0
    assert gdelt.pressure_score(1, 5) < 0
    assert gdelt.pressure_score(0, 0) == 0.0


def test_load_news_pressure_returns_score_and_date(monkeypatch):
    real_date = gdelt.date

    class FakeDate:
        @staticmethod
        def today():
            return real_date(2026, 6, 16)

    monkeypatch.setattr(gdelt, "date", FakeDate)

    def fake_fetch(url):
        query = parse_qs(urlparse(url).query)["query"][0]
        if "policy uncertainty" in query:
            return {"articles": [{}, {}, {}, {}]}
        return {"articles": [{}]}

    out = gdelt.load_news_pressure(("Canada",), fetch_json=fake_fetch)
    assert out["Canada"] == (1.3416, "2026-06-16")


def test_terms_version_is_stable_8_char_hex():
    version = gdelt.terms_version()
    assert version == gdelt.terms_version()
    assert len(version) == 8
    int(version, 16)  # hex-parseable


def test_terms_version_changes_when_terms_change(monkeypatch):
    before = gdelt.terms_version()
    monkeypatch.setattr(gdelt, "STRESS_TERMS", gdelt.STRESS_TERMS + ("newterm",))
    assert gdelt.terms_version() != before


def test_cache_key_includes_economy_lookback_and_terms():
    key = gdelt.cache_key("Brazil")
    assert key.startswith("Brazil|")
    assert gdelt.LOOKBACK in key
    assert key.endswith(gdelt.terms_version())


def _fake_date(monkeypatch):
    real_date = gdelt.date

    class FakeDate:
        @staticmethod
        def today():
            return real_date(2026, 6, 16)

    monkeypatch.setattr(gdelt, "date", FakeDate)


def _counting_fetch(calls):
    def fetch(url):
        calls["n"] += 1
        query = parse_qs(urlparse(url).query)["query"][0]
        if "policy uncertainty" in query:
            return {"articles": [{}, {}, {}, {}]}
        return {"articles": [{}]}

    return fetch


def test_load_news_pressure_serves_from_cache_within_ttl(monkeypatch, tmp_path):
    _fake_date(monkeypatch)
    calls = {"n": 0}
    fetch = _counting_fetch(calls)
    clock = {"t": 1000.0}
    cache = TTLCache(tmp_path / "news.json", ttl_seconds=100, now=lambda: clock["t"])

    first = gdelt.load_news_pressure(("Canada",), fetch_json=fetch, cache=cache)
    assert first["Canada"] == (1.3416, "2026-06-16")
    assert calls["n"] == 2  # stress + relief

    second = gdelt.load_news_pressure(("Canada",), fetch_json=fetch, cache=cache)
    assert second["Canada"] == (1.3416, "2026-06-16")
    assert calls["n"] == 2  # served from cache, no new requests


def test_load_news_pressure_refetches_after_ttl(monkeypatch, tmp_path):
    _fake_date(monkeypatch)
    calls = {"n": 0}
    fetch = _counting_fetch(calls)
    clock = {"t": 1000.0}
    cache = TTLCache(tmp_path / "news.json", ttl_seconds=100, now=lambda: clock["t"])

    gdelt.load_news_pressure(("Canada",), fetch_json=fetch, cache=cache)
    assert calls["n"] == 2
    clock["t"] = 1200.0  # past the 100s TTL
    gdelt.load_news_pressure(("Canada",), fetch_json=fetch, cache=cache)
    assert calls["n"] == 4  # expired -> refetched


def test_load_news_pressure_does_not_cache_failures(monkeypatch, tmp_path):
    _fake_date(monkeypatch)

    def failing_fetch(url):
        raise RuntimeError("boom")

    cache = TTLCache(tmp_path / "news.json", ttl_seconds=1000, now=lambda: 0.0)
    out = gdelt.load_news_pressure(("Canada",), fetch_json=failing_fetch, cache=cache)
    assert "Canada" not in out
    assert cache.get(gdelt.cache_key("Canada")) is None


def test_load_news_pressure_misses_when_terms_change(monkeypatch, tmp_path):
    _fake_date(monkeypatch)
    calls = {"n": 0}
    fetch = _counting_fetch(calls)
    cache = TTLCache(tmp_path / "news.json", ttl_seconds=1000, now=lambda: 0.0)

    gdelt.load_news_pressure(("Canada",), fetch_json=fetch, cache=cache)
    assert calls["n"] == 2
    monkeypatch.setattr(gdelt, "STRESS_TERMS", gdelt.STRESS_TERMS + ("newterm",))
    gdelt.load_news_pressure(("Canada",), fetch_json=fetch, cache=cache)
    assert calls["n"] == 4  # new terms_version -> new key -> refetch


def test_load_news_pressure_partial_cache_hit(monkeypatch, tmp_path):
    _fake_date(monkeypatch)
    calls = {"n": 0}
    fetch = _counting_fetch(calls)
    cache = TTLCache(tmp_path / "news.json", ttl_seconds=1000, now=lambda: 0.0)

    # Warm only Canada.
    gdelt.load_news_pressure(("Canada",), fetch_json=fetch, cache=cache)
    assert calls["n"] == 2

    # Ask for both; Canada is served from cache, only Japan is fetched.
    out = gdelt.load_news_pressure(("Canada", "Japan"), fetch_json=fetch, cache=cache)
    assert calls["n"] == 4  # 2 new fetches for Japan only
    assert "Canada" in out
    assert "Japan" in out


def test_load_news_pressure_treats_malformed_cache_entry_as_miss(monkeypatch, tmp_path):
    _fake_date(monkeypatch)
    calls = {"n": 0}
    fetch = _counting_fetch(calls)
    cache = TTLCache(tmp_path / "news.json", ttl_seconds=1000, now=lambda: 0.0)
    # A structurally-valid file with a malformed entry (wrong arity) must not
    # break generation — it is treated as a miss and refetched.
    cache.set(gdelt.cache_key("Canada"), [1.0, "2026-06-16", "extra"])
    out = gdelt.load_news_pressure(("Canada",), fetch_json=fetch, cache=cache)
    assert calls["n"] == 2
    assert out["Canada"] == (1.3416, "2026-06-16")
