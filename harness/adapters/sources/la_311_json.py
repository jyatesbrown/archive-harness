"""#20 MyLA311 Service Request Data 2026 — Socrata JSON [ROLLING-WINDOW, Tier B].

Key: <date of createddate>|casenumber; window keyed on part 0. 120 s timeout
is set in sources.json.
"""

from __future__ import annotations

from harness.adapters.generic_json import GenericJsonAdapter
from harness.fetch import HttpClient


class La311Json(GenericJsonAdapter):
    version = "1.0"

    def __init__(self, url: str, client: HttpClient | None = None, timeout_s: float | None = None):
        super().__init__(
            url, key_fields=("casenumber",), key_date_field="createddate", client=client, timeout_s=timeout_s
        )
