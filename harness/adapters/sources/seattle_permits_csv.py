"""#19 Seattle Building Permits — Socrata CSV [Tier B]. Key: permitnum (export header is lower-case)."""

from __future__ import annotations

from harness.adapters.generic_csv import GenericCsvAdapter
from harness.fetch import HttpClient


class SeattlePermitsCsv(GenericCsvAdapter):
    version = "1.0"

    def __init__(self, url: str, client: HttpClient | None = None, timeout_s: float | None = None):
        super().__init__(url, key_fields=("permitnum",), client=client, timeout_s=timeout_s)
