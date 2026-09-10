"""#1 Treasury Daily Par Yield Curve — CSV [FORMAT-CONTROL with #2]. Key: Date."""

from __future__ import annotations

from harness.adapters.generic_csv import GenericCsvAdapter
from harness.fetch import HttpClient


class TreasuryYieldCsv(GenericCsvAdapter):
    version = "1.0"

    def __init__(self, url: str, client: HttpClient | None = None, timeout_s: float | None = None):
        super().__init__(url, key_fields=("Date",), client=client, timeout_s=timeout_s)
