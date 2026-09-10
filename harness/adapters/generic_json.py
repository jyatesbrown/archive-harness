"""Configuration-driven JSON adapter.

records_path: dotted path to the array of record objects ("" = top-level array)
key_fields:   field names whose values compose the record_key (in order)
ignore_fields: fields excluded from the value hash (e.g. a server-side
               fetch timestamp that changes on every request)
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from harness.adapter import ExtractionError, Fingerprint, compose_key, value_hash
from harness.adapters.base import HttpAdapter, missing_fields
from harness.fetch import HttpClient


def resolve_path(doc: Any, path: str) -> Any:
    cur = doc
    for part in [p for p in path.split(".") if p]:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            raise ExtractionError(f"path {path!r} not found at {part!r}")
    return cur


class GenericJsonAdapter(HttpAdapter):
    version = "0.1"
    accept = "application/json, */*"

    def __init__(
        self,
        url: str,
        key_fields: Sequence[str],
        records_path: str = "",
        ignore_fields: Sequence[str] = (),
        client: HttpClient | None = None,
        timeout_s: float | None = None,
    ):
        super().__init__(url, client, timeout_s)
        self.key_fields = tuple(key_fields)
        self.records_path = records_path
        self.ignore_fields = frozenset(ignore_fields)

    def _records(self, raw: bytes) -> list[dict[str, Any]]:
        try:
            doc = json.loads(raw)
        except ValueError as e:
            raise ExtractionError(f"invalid JSON: {e}") from e
        recs = resolve_path(doc, self.records_path)
        if not isinstance(recs, list):
            raise ExtractionError(f"records_path {self.records_path!r} is not an array")
        if any(not isinstance(r, dict) for r in recs):
            raise ExtractionError("records array contains non-object entries")
        return recs

    def extract(self, raw: bytes) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for i, rec in enumerate(self._records(raw)):
            miss = missing_fields(rec, self.key_fields)
            if miss:
                raise ExtractionError(f"record {i} missing key fields {miss}")
            key = compose_key([rec[f] for f in self.key_fields])
            if key in seen:
                raise ExtractionError(f"duplicate record_key {key!r}")
            seen.add(key)
            vals = [
                (k, json.dumps(rec[k], sort_keys=True))
                for k in sorted(rec)
                if k not in self.key_fields and k not in self.ignore_fields
            ]
            out.append((key, value_hash(vals)))
        return out

    def fingerprint(self, raw: bytes) -> Fingerprint:
        recs = self._records(raw)
        fields: set[str] = set()
        for r in recs:
            fields.update(r.keys())
        return Fingerprint(
            record_count=len(recs),
            field_names=tuple(sorted(fields)),
            selectors=(f"json:{self.records_path or '$'}", f"key:{','.join(self.key_fields)}"),
        )
