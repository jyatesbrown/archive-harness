"""Shared base for HTTP-backed adapters."""

from __future__ import annotations

from collections.abc import Sequence

from harness.adapter import FetchResult
from harness.fetch import HttpClient


class HttpAdapter:
    version = "0.1"
    accept = "*/*"

    def __init__(self, url: str, client: HttpClient | None = None, timeout_s: float | None = None):
        self.url = url
        self.client = client or HttpClient()
        self.timeout_s = timeout_s

    def fetch(self) -> FetchResult:
        return self.client.fetch(self.url, timeout_s=self.timeout_s, accept=self.accept)


def missing_fields(row: dict[str, object], fields: Sequence[str]) -> list[str]:
    return [f for f in fields if f not in row]
