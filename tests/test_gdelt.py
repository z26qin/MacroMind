from urllib.parse import parse_qs, urlparse

from data_sources import gdelt


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
