"""#15 Treasury Fiscal Data — DTS Operating Cash Balance API — JSON.

Full history (~16.6k rows) paged with page[number] at page[size]=10000.
Key: record_date|account_type.
"""

from __future__ import annotations

from harness.adapter import FetchResult
from harness.adapters.generic_json import GenericJsonAdapter, with_query
from harness.fetch import HttpClient


class FiscalDataDtsJson(GenericJsonAdapter):
    version = "1.0"

    def __init__(self, url: str, client: HttpClient | None = None, timeout_s: float | None = None):
        super().__init__(
            url,
            key_fields=("record_date", "account_type"),
            records_path="data",
            page_mode="page",
            limit=10000,
            client=client,
            timeout_s=timeout_s,
        )

    def fetch(self) -> FetchResult:
        return self.fetch_paged(self._next)

    def _next(self, i: int, last: FetchResult) -> str | None:
        if len(self._page_records(last.body)) < self.limit:
            return None
        return with_query(self.url, **{"page[number]": str(i + 1)})
