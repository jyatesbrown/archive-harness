"""HTML table extraction (stdlib parser, no JavaScript).

`parse_tables()` returns every <table> as rows of whitespace-normalized cell
text plus its caption and attributes. `HtmlTableAdapter` is a configurable
one-table-per-page adapter: pick a table (by index or attribute substring),
name columns from a header row, and key rows by column names. Sources with
several tables or wide (one column per date) layouts subclass it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser

from harness.adapter import ExtractionError, Fingerprint, compose_key, value_hash
from harness.adapters.base import HttpAdapter, split_pages
from harness.fetch import HttpClient


@dataclass
class Table:
    attrs: dict[str, str]
    caption: str = ""
    rows: list[list[str]] = field(default_factory=list)


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[Table] = []
        self._stack: list[Table] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._caption: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            t = Table({k: v or "" for k, v in attrs})
            self.tables.append(t)
            self._stack.append(t)
        elif tag == "caption" and self._stack:
            self._caption = []
        elif tag == "tr" and self._stack:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._stack:
            self._stack[-1].rows.append(self._row)
            self._row = None
        elif tag == "caption" and self._caption is not None and self._stack:
            self._stack[-1].caption = " ".join("".join(self._caption).split())
            self._caption = None
        elif tag == "table" and self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
        elif self._caption is not None:
            self._caption.append(data)


def decode_html(raw: bytes) -> str:
    m = re.search(rb'charset=["\']?([A-Za-z0-9_-]+)', raw[:4096])
    enc = m.group(1).decode("ascii") if m else "utf-8"
    try:
        return raw.decode(enc, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def parse_tables(raw: bytes) -> list[Table]:
    p = _TableParser()
    p.feed(decode_html(raw))
    p.close()
    return p.tables


def select_table(tables: list[Table], index: int | None, attr_match: str | None) -> Table:
    if attr_match:
        k, _, v = attr_match.partition("=")
        hits = [t for t in tables if v in t.attrs.get(k, "")]
        if not hits:
            raise ExtractionError(f"no table with {attr_match!r} (tables={len(tables)})")
        if len(hits) > 1 and index is None:
            raise ExtractionError(f"{len(hits)} tables match {attr_match!r}")
        return hits[index or 0]
    i = index or 0
    if i >= len(tables):
        raise ExtractionError(f"table index {i} out of range (tables={len(tables)})")
    return tables[i]


def dedupe(names: list[str]) -> list[str]:
    """Make repeated column names unique by position ("X", "X#2", ...)."""
    out: list[str] = []
    for n in names:
        c = n
        k = 2
        while c in out:
            c = f"{n}#{k}"
            k += 1
        out.append(c)
    return out


class HtmlTableAdapter(HttpAdapter):
    version = "0.1"
    accept = "text/html, */*"

    def __init__(
        self,
        url: str,
        key_fields: Sequence[str],
        table_index: int | None = None,
        table_attr: str | None = None,
        header_row: int = 0,
        ignore_fields: Sequence[str] = (),
        client: HttpClient | None = None,
        timeout_s: float | None = None,
    ):
        super().__init__(url, client, timeout_s)
        self.key_fields = tuple(key_fields)
        self.table_index = table_index
        self.table_attr = table_attr
        self.header_row = header_row
        self.ignore_fields = frozenset(ignore_fields)

    def _table(self, page: bytes) -> tuple[list[str], list[dict[str, str]]]:
        t = select_table(parse_tables(page), self.table_index, self.table_attr)
        if len(t.rows) <= self.header_row:
            raise ExtractionError("table has no header row")
        header = dedupe(t.rows[self.header_row])
        rows = []
        for r in t.rows[self.header_row + 1 :]:
            if len(r) != len(header):
                raise ExtractionError(f"row has {len(r)} cells, header has {len(header)}")
            rows.append(dict(zip(header, r, strict=True)))
        return header, rows

    def _rows(self, raw: bytes) -> tuple[list[str], list[dict[str, str]]]:
        header: list[str] | None = None
        rows: list[dict[str, str]] = []
        for page in split_pages(raw):
            h, r = self._table(page)
            if header is None:
                header = h
            elif h != header:
                raise ExtractionError("header differs between pages")
            rows.extend(r)
        assert header is not None
        return header, rows

    def extract(self, raw: bytes) -> list[tuple[str, str]]:
        header, rows = self._rows(raw)
        miss = [f for f in self.key_fields if f not in header]
        if miss:
            raise ExtractionError(f"header missing key fields {miss}: {header}")
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for row in rows:
            key = compose_key([row[f] for f in self.key_fields])
            if key in seen:
                raise ExtractionError(f"duplicate record_key {key!r}")
            seen.add(key)
            vals = [(k, row[k]) for k in header if k not in self.key_fields and k not in self.ignore_fields]
            out.append((key, value_hash(vals)))
        return out

    def fingerprint(self, raw: bytes) -> Fingerprint:
        header, rows = self._rows(raw)
        return Fingerprint(
            record_count=len(rows),
            field_names=tuple(header),
            selectors=(
                f"table:{self.table_attr or ''}#{self.table_index if self.table_index is not None else 0}",
                f"header_row:{self.header_row}",
                f"key:{','.join(self.key_fields)}",
            ),
        )
