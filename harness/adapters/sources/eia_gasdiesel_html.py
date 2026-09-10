"""#5 EIA Gasoline and Diesel Fuel Update — HTML.

Three captioned tables (regular gasoline by region, states, diesel by
region), each wide: one column per week-ending date. Record key:
<caption>|<row label>|<MM/DD/YY>; value: the price cell. "Change from"
columns are derived and skipped.
"""

from __future__ import annotations

import re

from harness.adapter import ExtractionError, Fingerprint, compose_key, value_hash
from harness.adapters.html_table import HtmlTableAdapter, parse_tables
from harness.fetch import HttpClient

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{2}$")


class EiaGasDieselHtml(HtmlTableAdapter):
    version = "1.0"

    def __init__(self, url: str, client: HttpClient | None = None, timeout_s: float | None = None):
        super().__init__(
            url,
            key_fields=("caption", "row", "date"),
            table_attr="class=basic-table",
            client=client,
            timeout_s=timeout_s,
        )

    def _cells(self, raw: bytes) -> tuple[list[str], list[tuple[str, str]]]:
        tables = [t for t in parse_tables(raw) if "basic-table" in t.attrs.get("class", "")]
        if not tables:
            raise ExtractionError("no basic-table tables found")
        captions: list[str] = []
        out: list[tuple[str, str]] = []
        for t in tables:
            cap = re.sub(r"\*.*$", "", t.caption).strip() or f"table{len(captions)}"
            captions.append(cap)
            date_row = next((r for r in t.rows if sum(bool(DATE_RE.match(c)) for c in r) >= 2), None)
            if date_row is None:
                raise ExtractionError(f"no date header row in table {cap!r}")
            dated = [(j, c) for j, c in enumerate(date_row) if DATE_RE.match(c)]
            for r in t.rows:
                if r is date_row or len(r) != len(date_row) or not r[0]:
                    continue
                for j, d in dated:
                    out.append((compose_key([cap, r[0], d]), r[j]))
        return captions, out

    def extract(self, raw: bytes) -> list[tuple[str, str]]:
        _, cells = self._cells(raw)
        seen: set[str] = set()
        out = []
        for k, v in cells:
            if k in seen:
                raise ExtractionError(f"duplicate record_key {k!r}")
            seen.add(k)
            out.append((k, value_hash([("value", v)])))
        return out

    def fingerprint(self, raw: bytes) -> Fingerprint:
        captions, cells = self._cells(raw)
        return Fingerprint(
            record_count=len(cells),
            field_names=tuple(captions),
            selectors=("table:class=basic-table", "key:caption,row,date"),
        )
