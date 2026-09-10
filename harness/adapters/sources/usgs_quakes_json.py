"""#16 USGS Earthquakes all_day GeoJSON [ROLLING-WINDOW, 1 day].

Key: <UTC date of properties.time>|<id>; window keyed on part 0.
Value: properties and geometry. metadata.generated is document-level.
"""

from __future__ import annotations

from typing import Any

from harness.adapter import ExtractionError, Fingerprint, compose_key
from harness.adapters.generic_json import GenericJsonAdapter, date_part
from harness.fetch import HttpClient


class UsgsQuakesJson(GenericJsonAdapter):
    version = "1.0"

    def __init__(self, url: str, client: HttpClient | None = None, timeout_s: float | None = None):
        super().__init__(url, key_fields=("id",), records_path="features", client=client, timeout_s=timeout_s)

    def record_key(self, rec: dict[str, Any]) -> str:
        props = rec.get("properties")
        if not isinstance(props, dict) or "time" not in props:
            raise ExtractionError("feature without properties.time")
        return compose_key([date_part(props["time"]), rec["id"]])

    def fingerprint(self, raw: bytes) -> Fingerprint:
        recs = self._records(raw)
        fields: set[str] = set()
        for r in recs:
            fields.update(r.keys())
            props = r.get("properties")
            if isinstance(props, dict):
                fields.update(f"properties.{k}" for k in props)
        return Fingerprint(
            record_count=len(recs),
            field_names=tuple(sorted(fields)),
            selectors=("json:features", "key:date(properties.time),id"),
        )
