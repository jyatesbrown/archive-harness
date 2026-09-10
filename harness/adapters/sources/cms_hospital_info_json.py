"""#9 CMS Hospital General Information — DKAN datastore JSON.

The datastore caps `limit` at 1500 (it accepted 10000 during Task 1
screening), so pages are fetched by offset. Key: facility_id.
"""

from __future__ import annotations

from harness.adapters.generic_json import GenericJsonAdapter
from harness.fetch import HttpClient


class CmsHospitalInfoJson(GenericJsonAdapter):
    version = "1.0"

    def __init__(self, url: str, client: HttpClient | None = None, timeout_s: float | None = None):
        super().__init__(
            url,
            key_fields=("facility_id",),
            records_path="results",
            page_mode="offset",
            offset_param="offset",
            limit=1500,
            client=client,
            timeout_s=timeout_s,
        )
