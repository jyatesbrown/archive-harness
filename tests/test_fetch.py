"""HttpClient behaviour with a fake transport: robots semantics (Amendment A §1.1),
retry/backoff, 401/403 refusal, crawl delay."""

from __future__ import annotations

import pytest

from harness.adapter import FetchResult
from harness.fetch import MAX_ATTEMPTS, FetchError, HttpClient, RobotsDisallowed, RobotsUnavailable


class FakeTransport:
    def __init__(self, responses: dict[str, list[FetchResult | Exception]]):
        self.responses = responses
        self.calls: list[str] = []

    def __call__(self, url: str, timeout_s=None, accept="*/*") -> FetchResult:  # type: ignore[no-untyped-def]
        self.calls.append(url)
        q = self.responses[url]
        item = q.pop(0) if len(q) > 1 else q[0]
        if isinstance(item, Exception):
            raise item
        return item


def resp(status: int, body: bytes = b"", url: str = "") -> FetchResult:
    return FetchResult(body=body, http_status=status, url=url)


def make(responses):  # type: ignore[no-untyped-def]
    sleeps: list[float] = []
    c = HttpClient(sleep=sleeps.append)
    t = FakeTransport(responses)
    c._request = t  # type: ignore[method-assign]
    return c, t, sleeps


DATA = "https://example.gov/data.json"
ROBOTS = "https://example.gov/robots.txt"


@pytest.mark.parametrize("status", [404, 403, 410])
def test_robots_4xx_means_absent_all_allowed(status):
    c, t, _ = make({ROBOTS: [resp(status)], DATA: [resp(200, b"ok")]})
    assert c.fetch(DATA).body == b"ok"
    assert c.robots(DATA).allowed and c.robots(DATA).status == status


def test_robots_200_empty_all_allowed():
    c, t, _ = make({ROBOTS: [resp(200, b"  \n")], DATA: [resp(200, b"ok")]})
    assert c.fetch(DATA).body == b"ok"


def test_robots_200_disallow_blocks():
    c, t, _ = make({ROBOTS: [resp(200, b"User-agent: *\nDisallow: /\n")], DATA: [resp(200, b"ok")]})
    with pytest.raises(RobotsDisallowed):
        c.fetch(DATA)
    assert DATA not in t.calls


def test_robots_200_allow_with_crawl_delay():
    c, t, sleeps = make({ROBOTS: [resp(200, b"User-agent: *\nCrawl-delay: 7\nAllow: /\n")], DATA: [resp(200, b"ok")]})
    c.fetch(DATA)
    c.fetch(DATA)
    assert c.robots(DATA).crawl_delay == 7.0
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 7.0


@pytest.mark.parametrize("status", [500, 503])
def test_robots_5xx_is_ineligible(status):
    c, t, _ = make({ROBOTS: [resp(status)], DATA: [resp(200, b"ok")]})
    with pytest.raises(RobotsUnavailable):
        c.fetch(DATA)
    assert DATA not in t.calls


def test_robots_network_failure_is_ineligible():
    c, t, _ = make({ROBOTS: [OSError("dns")], DATA: [resp(200, b"ok")]})
    with pytest.raises(RobotsUnavailable):
        c.fetch(DATA)


@pytest.mark.parametrize("status", [401, 403])
def test_endpoint_refusal_is_not_retried(status):
    c, t, sleeps = make({ROBOTS: [resp(404)], DATA: [resp(status, b"no")]})
    with pytest.raises(FetchError) as ei:
        c.fetch(DATA)
    assert ei.value.http_status == status and ei.value.body == b"no"
    assert t.calls.count(DATA) == 1 and sleeps == []


def test_transient_errors_retry_with_backoff_then_fail():
    c, t, sleeps = make({ROBOTS: [resp(404)], DATA: [resp(503), resp(503), resp(503)]})
    with pytest.raises(FetchError) as ei:
        c.fetch(DATA)
    assert ei.value.http_status == 503
    assert t.calls.count(DATA) == MAX_ATTEMPTS
    assert sleeps == [1, 2]


def test_transient_then_success():
    c, t, sleeps = make({ROBOTS: [resp(404)], DATA: [OSError("reset"), resp(200, b"ok")]})
    assert c.fetch(DATA).body == b"ok"
    assert sleeps == [1]


def test_robots_cached_per_host():
    c, t, _ = make({ROBOTS: [resp(404)], DATA: [resp(200, b"ok")]})
    c.fetch(DATA)
    c.fetch(DATA)
    assert t.calls.count(ROBOTS) == 1
