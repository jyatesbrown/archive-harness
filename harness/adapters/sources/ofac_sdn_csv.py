"""#12 OFAC SDN List — CSV. No header row; ent_num is column 1 (c0). 12 columns."""

from __future__ import annotations

from harness.adapters.generic_csv import GenericCsvAdapter
from harness.fetch import HttpClient


class OfacSdnCsv(GenericCsvAdapter):
    version = "1.0"

    def __init__(self, url: str, client: HttpClient | None = None, timeout_s: float | None = None):
        super().__init__(url, key_fields=("c0",), has_header=False, columns=12, client=client, timeout_s=timeout_s)
