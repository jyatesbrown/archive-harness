"""#17 CISA Known Exploited Vulnerabilities catalog — JSON. Key: cveID."""

from __future__ import annotations

from harness.adapters.generic_json import GenericJsonAdapter
from harness.fetch import HttpClient


class CisaKevJson(GenericJsonAdapter):
    version = "1.0"

    def __init__(self, url: str, client: HttpClient | None = None, timeout_s: float | None = None):
        super().__init__(url, key_fields=("cveID",), records_path="vulnerabilities", client=client, timeout_s=timeout_s)
