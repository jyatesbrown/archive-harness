"""#10 FDIC Failed Bank List — HTML [APPEND-ONLY-CONTROL].

Pages of at most 100 rows (`items_per_page` above 100 returns no table).
Key: Cert. Sort-link text is stripped from header cells.
"""

from __future__ import annotations

import re

from harness.adapter import ExtractionError, FetchResult
from harness.adapters.base import split_pages
from harness.adapters.generic_json import with_query
from harness.adapters.html_table import HtmlTableAdapter, dedupe, parse_tables, select_table
from harness.fetch import HttpClient

PAGE_SIZE = 100
SORT_RE = re.compile(r"\s*Sort (ascending|descending)$")


class FdicFailedBanksHtml(HtmlTableAdapter):
    version = "1.0"

    def __init__(self, url: str, client: HttpClient | None = None, timeout_s: float | None = None):
        super().__init__(url, key_fields=("Cert",), table_attr="class=usa-table", client=client, timeout_s=timeout_s)

    def fetch(self) -> FetchResult:
        return self.fetch_paged(self._next)

    def _next(self, i: int, last: FetchResult) -> str | None:
        _, rows = self._table(last.body)
        if len(rows) < PAGE_SIZE:
            return None
        return with_query(self.url, page=str(i))

    def _table(self, page: bytes) -> tuple[list[str], list[dict[str, str]]]:
        tables = parse_tables(page)
        if not any("usa-table" in t.attrs.get("class", "") for t in tables):
            return [], []
        t = select_table(tables, self.table_index, self.table_attr)
        header = dedupe([SORT_RE.sub("", c) for c in t.rows[0]])
        rows = []
        for r in t.rows[1:]:
            if len(r) != len(header):
                raise ExtractionError(f"row has {len(r)} cells, header has {len(header)}")
            rows.append(dict(zip(header, r, strict=True)))
        return header, rows

    def _rows(self, raw: bytes) -> tuple[list[str], list[dict[str, str]]]:
        header: list[str] = []
        rows: list[dict[str, str]] = []
        for page in split_pages(raw):
            h, r = self._table(page)
            if not h:
                continue
            if not header:
                header = h
            elif h != header:
                raise ExtractionError("header differs between pages")
            rows.extend(r)
        if not header:
            raise ExtractionError("no usa-table found on any page")
        return header, rows
