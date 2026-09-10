"""HTTP fetching: robots.txt (RFC 9309 semantics per Amendment A §1.1), retries
with exponential backoff, one request per host at a time, contactable UA.
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from harness.adapter import FetchResult

USER_AGENT = "archive-harness/0.1 (+mailto:jyatesbrown@gmail.com)"
DEFAULT_TIMEOUT_S = 30.0
MAX_ATTEMPTS = 3
MAX_REDIRECTS = 5
TRANSIENT_STATUSES = {408, 429, 500, 502, 503, 504}


class RobotsUnavailable(Exception):
    """robots.txt returned 5xx or a network failure: assume complete disallow."""


class RobotsDisallowed(Exception):
    """robots.txt (200, parseable) disallows the path for our user agent."""


class FetchError(Exception):
    def __init__(self, message: str, http_status: int | None = None, body: bytes = b""):
        super().__init__(message)
        self.http_status = http_status
        self.body = body


@dataclass
class RobotsDecision:
    status: int | None
    allowed: bool
    crawl_delay: float
    reason: str


class _RedirectLimiter(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS


def _agent() -> str:
    return USER_AGENT.split("/")[0]


class HttpClient:
    def __init__(
        self,
        user_agent: str = USER_AGENT,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        sleep: Callable[[float], None] = time.sleep,
        opener: urllib.request.OpenerDirector | None = None,
    ):
        self.user_agent = user_agent
        self.timeout_s = timeout_s
        self._sleep = sleep
        self._opener = opener or urllib.request.build_opener(_RedirectLimiter)
        self._host_locks: dict[str, threading.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._robots_cache: dict[str, RobotsDecision | RobotFileParser] = {}
        self._lock = threading.Lock()

    # ---- low level -------------------------------------------------------
    def _request(self, url: str, timeout_s: float | None = None, accept: str = "*/*") -> FetchResult:
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": accept})
        t0 = time.monotonic()
        try:
            with self._opener.open(req, timeout=timeout_s or self.timeout_s) as resp:
                body = resp.read()
                return FetchResult(
                    body=body,
                    http_status=resp.status,
                    url=resp.geturl(),
                    content_type=resp.headers.get("Content-Type", ""),
                    headers={k.lower(): v for k, v in resp.headers.items()},
                    duration_s=time.monotonic() - t0,
                )
        except urllib.error.HTTPError as e:
            body = e.read() if e.fp else b""
            return FetchResult(
                body=body,
                http_status=e.code,
                url=e.geturl() or url,
                content_type=e.headers.get("Content-Type", "") if e.headers else "",
                headers={k.lower(): v for k, v in (e.headers.items() if e.headers else [])},
                duration_s=time.monotonic() - t0,
            )

    def _host_lock(self, host: str) -> threading.Lock:
        with self._lock:
            return self._host_locks.setdefault(host, threading.Lock())

    # ---- robots ----------------------------------------------------------
    def robots(self, url: str) -> RobotsDecision:
        parts = urlsplit(url)
        host_key = f"{parts.scheme}://{parts.netloc}"
        cached: RobotsDecision | RobotFileParser | None = self._robots_cache.get(host_key)
        if cached is None:
            robots_url = f"{host_key}/robots.txt"
            try:
                res = self._request(robots_url, timeout_s=min(self.timeout_s, 15.0))
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                failed = RobotsDecision(None, False, 0.0, f"robots network failure: {e}")
                self._robots_cache[host_key] = failed
                return failed
            if res.http_status >= 500:
                cached = RobotsDecision(res.http_status, False, 0.0, f"robots {res.http_status}: assume disallow")
            elif 400 <= res.http_status < 500:
                cached = RobotsDecision(res.http_status, True, 0.0, f"robots {res.http_status}: absent, all allowed")
            elif not res.body.strip():
                cached = RobotsDecision(res.http_status, True, 0.0, "robots 200 empty: all allowed")
            else:
                rp = RobotFileParser()
                rp.parse(res.body.decode("utf-8", errors="replace").splitlines())
                cached = rp
            self._robots_cache[host_key] = cached
        if isinstance(cached, RobotsDecision):
            return cached
        agent = _agent()
        allowed = cached.can_fetch(agent, url)
        delay = cached.crawl_delay(agent) or cached.crawl_delay("*") or 0
        return RobotsDecision(200, allowed, float(delay), "robots 200 parsed")

    # ---- public ----------------------------------------------------------
    def fetch(self, url: str, timeout_s: float | None = None, accept: str = "*/*") -> FetchResult:
        """Fetch with robots check, per-host serialization, crawl delay, and retries.

        Raises RobotsUnavailable / RobotsDisallowed / FetchError. HTTP 4xx other
        than 408/429 is returned as a FetchError without retry (401/403 are an
        active refusal of our client).
        """
        host = urlsplit(url).netloc
        with self._host_lock(host):
            decision = self.robots(url)
            if decision.status is None or (decision.status >= 500):
                raise RobotsUnavailable(decision.reason)
            if not decision.allowed:
                raise RobotsDisallowed(decision.reason)
            last_exc: Exception | None = None
            for attempt in range(1, MAX_ATTEMPTS + 1):
                self._respect_delay(host, decision.crawl_delay)
                try:
                    res = self._request(url, timeout_s=timeout_s, accept=accept)
                except (urllib.error.URLError, TimeoutError, OSError) as e:
                    last_exc = FetchError(f"network error: {e}")
                else:
                    self._host_last[host] = time.monotonic()
                    if 200 <= res.http_status < 300:
                        return res
                    if res.http_status in TRANSIENT_STATUSES:
                        last_exc = FetchError(f"HTTP {res.http_status}", res.http_status, res.body)
                    else:
                        raise FetchError(f"HTTP {res.http_status}", res.http_status, res.body)
                if attempt < MAX_ATTEMPTS:
                    self._sleep(2 ** (attempt - 1))
            assert last_exc is not None
            raise last_exc

    def _respect_delay(self, host: str, crawl_delay: float) -> None:
        if crawl_delay <= 0:
            return
        last = self._host_last.get(host)
        if last is None:
            return
        wait = crawl_delay - (time.monotonic() - last)
        if wait > 0:
            self._sleep(wait)
