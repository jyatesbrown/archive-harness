"""Shared base for HTTP-backed adapters.

Paged sources: when a publisher caps the page size below the record set, the
adapter fetches every page in one run and stores the verbatim page bodies as a
single raw payload, joined by PAGE_SEP. `split_pages()` recovers the original
bodies. The FetchResult metadata (status, headers) is that of the first page.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from harness.adapter import FetchResult
from harness.fetch import FetchError, HttpClient

PAGE_SEP = b"\n--archive-harness-page--\n"


def split_pages(raw: bytes) -> list[bytes]:
    return raw.split(PAGE_SEP)


class HttpAdapter:
    version = "0.1"
    accept = "*/*"
    max_pages = 50

    def __init__(self, url: str, client: HttpClient | None = None, timeout_s: float | None = None):
        self.url = url
        self.client = client or HttpClient()
        self.timeout_s = timeout_s

    def fetch(self) -> FetchResult:
        return self.client.fetch(self.url, timeout_s=self.timeout_s, accept=self.accept)

    def fetch_paged(self, next_url: Callable[[int, FetchResult], str | None]) -> FetchResult:
        """Fetch self.url then follow next_url(page_index, last_result) until it
        returns None or max_pages is reached."""
        t0 = time.monotonic()
        first = self.client.fetch(self.url, timeout_s=self.timeout_s, accept=self.accept)
        bodies = [first.body]
        last = first
        for i in range(1, self.max_pages):
            nxt = next_url(i, last)
            if not nxt:
                break
            last = self.client.fetch(nxt, timeout_s=self.timeout_s, accept=self.accept)
            bodies.append(last.body)
        else:
            raise FetchError(f"pagination exceeded {self.max_pages} pages")
        return FetchResult(
            body=PAGE_SEP.join(bodies),
            http_status=first.http_status,
            url=first.url,
            content_type=first.content_type,
            headers=dict(first.headers, **{"x-harness-pages": str(len(bodies))}),
            duration_s=time.monotonic() - t0,
        )


def missing_fields(row: dict[str, object], fields: Sequence[str]) -> list[str]:
    return [f for f in fields if f not in row]
