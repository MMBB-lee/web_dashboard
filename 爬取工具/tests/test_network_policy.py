from urllib.error import HTTPError
from urllib.request import Request

import pytest

from anhui_crawler.common import CrawlerError
from anhui_crawler.network import Fetcher, Response, RestrictedRedirect


def response(url, body):
    return Response(url, url, body, "text/plain", 200, "2026-10-15T00:00:00+08:00")


def test_robots_denial_stops_before_fetching_document(monkeypatch):
    fetcher = Fetcher({"example.invalid"})
    calls = []

    def request(url):
        calls.append(url)
        return response(url, b"User-agent: *\nDisallow: /private")

    monkeypatch.setattr(fetcher, "_request", request)
    with pytest.raises(CrawlerError, match="disallows"):
        fetcher.fetch("https://example.invalid/private/document.pdf")
    assert calls == ["https://example.invalid/robots.txt"]


def test_robots_404_is_recorded_not_an_ignored_403(monkeypatch):
    fetcher = Fetcher({"example.invalid"})

    def request(url):
        if url.endswith("robots.txt"):
            raise HTTPError(url, 404, "not found", {}, None)
        return response(url, b"public")

    monkeypatch.setattr(fetcher, "_request", request)
    assert fetcher.fetch("https://example.invalid/public").body == b"public"
    assert next(iter(fetcher.robots.values()))[1]["http_status"] == 404


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_robots_restrictions_are_not_bypassed(monkeypatch, status):
    fetcher = Fetcher({"example.invalid"})

    def request(url):
        raise HTTPError(url, status, "blocked", {}, None)

    monkeypatch.setattr(fetcher, "_request", request)
    with pytest.raises(CrawlerError, match="robots.txt unavailable"):
        fetcher.fetch("https://example.invalid/public")


@pytest.mark.parametrize("body", [b"<html>access denied</html>", b'{"error":"not allowed"}'])
def test_robots_200_error_page_not_treated_as_allow_all(monkeypatch, body):
    fetcher = Fetcher({"example.invalid"})
    monkeypatch.setattr(fetcher, "_request", lambda url: response(url, body))
    with pytest.raises(CrawlerError):
        fetcher.fetch("https://example.invalid/public")


def test_redirect_into_disallowed_path_rejected(monkeypatch):
    fetcher = Fetcher({"example.invalid"})
    monkeypatch.setattr(fetcher, "_request", lambda url: response(url, b"User-agent: *\nDisallow: /private"))
    fetcher._robot_policy("https://example.invalid/public")
    handler = RestrictedRedirect(fetcher.hosts, fetcher._check_redirect_policy)
    with pytest.raises(CrawlerError, match="redirected"):
        handler.redirect_request(Request("https://example.invalid/public"), None, 302, "redirect", {}, "https://example.invalid/private")


def test_redirect_cross_host_is_not_automatically_trusted():
    handler = RestrictedRedirect({"example.invalid", "other.invalid"})
    with pytest.raises(CrawlerError, match="Cross-host"):
        handler.redirect_request(Request("https://example.invalid/public"), None, 302, "redirect", {}, "https://other.invalid/private")


def test_large_retry_after_does_not_trigger_immediate_retry(monkeypatch):
    fetcher = Fetcher({"example.invalid"})
    monkeypatch.setattr(fetcher, "_robot_policy", lambda _: (type("Robot", (), {"can_fetch": lambda *args: True})(), {}))
    calls = []

    def request(url):
        calls.append(url)
        raise HTTPError(url, 429, "wait", {"Retry-After": "120"}, None)

    monkeypatch.setattr(fetcher, "_request", request)
    with pytest.raises(CrawlerError, match="deferred"):
        fetcher.fetch("https://example.invalid/public")
    assert len(calls) == 1
