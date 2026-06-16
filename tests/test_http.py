import httpx
import pytest

from data_sources import http


def _response(status_code, payload):
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("GET", "https://example.test/data"),
    )


def test_fetch_json_retries_retryable_status_then_returns_json(monkeypatch):
    responses = [
        _response(429, {"error": "rate limited"}),
        _response(200, {"ok": True}),
    ]
    sleeps = []
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return responses.pop(0)

    monkeypatch.setattr(http.httpx, "get", fake_get)

    out = http.fetch_json(
        "https://example.test/data",
        headers={"User-Agent": "test"},
        retries=1,
        sleep=sleeps.append,
    )

    assert out == {"ok": True}
    assert len(calls) == 2
    assert calls[0][1]["headers"] == {"User-Agent": "test"}
    assert sleeps == [1.5]


def test_fetch_json_raises_after_retry_budget(monkeypatch):
    def fake_get(url, **kwargs):
        return _response(503, {"error": "unavailable"})

    monkeypatch.setattr(http.httpx, "get", fake_get)

    with pytest.raises(httpx.HTTPStatusError):
        http.fetch_json("https://example.test/data", retries=1, sleep=lambda _: None)


def test_fetch_json_rejects_negative_retries():
    with pytest.raises(ValueError, match="retries"):
        http.fetch_json("https://example.test/data", retries=-1)
