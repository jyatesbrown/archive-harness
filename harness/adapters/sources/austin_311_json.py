"""#18 Austin 311 Public Data — Socrata JSON [ROLLING-WINDOW, Tier B].

Key: <date of sr_created_date>|sr_number; window keyed on part 0.
"""

from __future__ import annotations

from harness.adapters.generic_json import GenericJsonAdapter
from harness.fetch import HttpClient


class Austin311Json(GenericJsonAdapter):
    version = "1.0"

    def __init__(self, url: str, client: HttpClient | None = None, timeout_s: float | None = None):
        super().__init__(
            url, key_fields=("sr_number",), key_date_field="sr_created_date", client=client, timeout_s=timeout_s
        )
