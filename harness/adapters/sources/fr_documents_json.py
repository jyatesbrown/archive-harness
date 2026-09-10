"""#6 Federal Register Documents API — JSON (30-day publication window).

Follows `next_page_url` (per_page is capped at 1000). Key: document_number,
shared with #13 (Public Inspection), whose exited documents land here.
"""

from __future__ import annotations

from harness.adapters.generic_json import GenericJsonAdapter
from harness.fetch import HttpClient


class FrDocumentsJson(GenericJsonAdapter):
    version = "1.0"

    def __init__(self, url: str, client: HttpClient | None = None, timeout_s: float | None = None):
        super().__init__(
            url,
            key_fields=("document_number",),
            records_path="results",
            page_mode="next_url",
            next_url_path="next_page_url",
            client=client,
            timeout_s=timeout_s,
        )
