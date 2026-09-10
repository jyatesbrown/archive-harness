"""Configuration-driven JSON adapter.

records_path: dotted path to the array of record objects ("" = top-level array)
key_fields:   field names whose values compose the record_key (in order)
ignore_fields: fields excluded from the value hash (e.g. a server-side
               fetch timestamp or view counter that changes on every request)
key_date_field: optional field whose first 10 characters (an ISO date) are
               prepended to the key, so a rolling window can be keyed on it
               (`window_key_part=0`); an integer value is read as epoch ms

Pagination (publisher caps the page size below the record set):
page_mode="next_url": follow the URL found at `next_url_path` in each page
page_mode="offset":   append `<offset_param>=<n*limit>` until a page holds
                      fewer than `limit` records
Pages are stored verbatim in one payload; see harness.adapters.base.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from harness.adapter import ExtractionError, FetchResult, Fingerprint, compose_key, value_hash
from harness.adapters.base import HttpAdapter, missing_fields, split_pages
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


def parse_json(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except ValueError as e:
        raise ExtractionError(f"invalid JSON: {e}") from e


def with_query(url: str, **params: str) -> str:
    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q.update(params)
    return urlunsplit(parts._replace(query=urlencode(q, safe="$:'[],")))


def date_part(v: object) -> str:
    if isinstance(v, bool) or v is None:
        raise ExtractionError(f"cannot derive a date from {v!r}")
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    s = str(v)
    if len(s) < 10:
        raise ExtractionError(f"cannot derive a date from {s!r}")
    return s[:10]


class GenericJsonAdapter(HttpAdapter):
    version = "0.1"
    accept = "application/json, */*"

    def __init__(
        self,
        url: str,
        key_fields: Sequence[str],
        records_path: str = "",
        ignore_fields: Sequence[str] = (),
        key_date_field: str | None = None,
        page_mode: str = "none",
        next_url_path: str = "next_page_url",
        offset_param: str = "offset",
        limit: int = 0,
        client: HttpClient | None = None,
        timeout_s: float | None = None,
    ):
        super().__init__(url, client, timeout_s)
        self.key_fields = tuple(key_fields)
        self.records_path = records_path
        self.ignore_fields = frozenset(ignore_fields)
        self.key_date_field = key_date_field
        self.page_mode = page_mode
        self.next_url_path = next_url_path
        self.offset_param = offset_param
        self.limit = limit

    # ---- fetch ---------------------------------------------------------------
    def fetch(self) -> FetchResult:
        if self.page_mode == "next_url":
            return self.fetch_paged(self._next_by_url)
        if self.page_mode == "offset":
            return self.fetch_paged(self._next_by_offset)
        return super().fetch()

    def _next_by_url(self, i: int, last: FetchResult) -> str | None:
        doc = parse_json(last.body)
        nxt = doc.get(self.next_url_path) if isinstance(doc, dict) else None
        return str(nxt) if nxt else None

    def _next_by_offset(self, i: int, last: FetchResult) -> str | None:
        if len(self._page_records(last.body)) < self.limit:
            return None
        return with_query(self.url, **{self.offset_param: str(i * self.limit)})

    # ---- extract -------------------------------------------------------------
    def _page_records(self, page: bytes) -> list[dict[str, Any]]:
        recs = resolve_path(parse_json(page), self.records_path)
        if not isinstance(recs, list):
            raise ExtractionError(f"records_path {self.records_path!r} is not an array")
        if any(not isinstance(r, dict) for r in recs):
            raise ExtractionError("records array contains non-object entries")
        return recs

    def _records(self, raw: bytes) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for page in split_pages(raw):
            out.extend(self._page_records(page))
        return out

    def record_key(self, rec: dict[str, Any]) -> str:
        parts = [rec[f] for f in self.key_fields]
        if self.key_date_field:
            if self.key_date_field not in rec:
                raise ExtractionError(f"record missing key_date_field {self.key_date_field!r}")
            parts.insert(0, date_part(rec[self.key_date_field]))
        return compose_key(parts)

    def record_values(self, rec: dict[str, Any]) -> list[tuple[str, str]]:
        return [
            (k, json.dumps(rec[k], sort_keys=True))
            for k in sorted(rec)
            if k not in self.key_fields and k not in self.ignore_fields
        ]

    def extract(self, raw: bytes) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for i, rec in enumerate(self._records(raw)):
            miss = missing_fields(rec, self.key_fields)
            if miss:
                raise ExtractionError(f"record {i} missing key fields {miss}")
            key = self.record_key(rec)
            if key in seen:
                raise ExtractionError(f"duplicate record_key {key!r}")
            seen.add(key)
            out.append((key, value_hash(self.record_values(rec))))
        return out

    def fingerprint(self, raw: bytes) -> Fingerprint:
        recs = self._records(raw)
        fields: set[str] = set()
        for r in recs:
            fields.update(r.keys())
        key = ",".join(self.key_fields)
        if self.key_date_field:
            key = f"date({self.key_date_field})," + key
        return Fingerprint(
            record_count=len(recs),
            field_names=tuple(sorted(fields)),
            selectors=(f"json:{self.records_path or '$'}", f"key:{key}", f"pages:{self.page_mode}"),
        )
