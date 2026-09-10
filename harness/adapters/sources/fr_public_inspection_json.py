"""#13 Federal Register Public Inspection (current) — JSON [EXIT-CONDITION -> #6].

Key: document_number. `page_views` is a hit counter that changes on every
request and is excluded from the value hash.
"""

from __future__ import annotations

from harness.adapters.generic_json import GenericJsonAdapter
from harness.fetch import HttpClient


class FrPublicInspectionJson(GenericJsonAdapter):
    version = "1.0"

    def __init__(self, url: str, client: HttpClient | None = None, timeout_s: float | None = None):
        super().__init__(
            url,
            key_fields=("document_number",),
            records_path="results",
            ignore_fields=("page_views",),
            client=client,
            timeout_s=timeout_s,
        )
