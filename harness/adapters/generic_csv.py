"""Configuration-driven CSV adapter.

has_header=True:  column names come from the first (non-skipped) row.
has_header=False: columns are named by position ("c0", "c1", ...); `columns`
                  must give the expected column count, and key_fields refer to
                  positional names. Trailing control characters (an EOF marker
                  such as 0x1A) are ignored.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence

from harness.adapter import ExtractionError, Fingerprint, compose_key, value_hash
from harness.adapters.base import HttpAdapter, missing_fields
from harness.fetch import HttpClient


class GenericCsvAdapter(HttpAdapter):
    version = "0.1"
    accept = "text/csv, */*"

    def __init__(
        self,
        url: str,
        key_fields: Sequence[str],
        ignore_fields: Sequence[str] = (),
        encoding: str = "utf-8-sig",
        delimiter: str = ",",
        skip_rows: int = 0,
        has_header: bool = True,
        columns: int = 0,
        client: HttpClient | None = None,
        timeout_s: float | None = None,
    ):
        super().__init__(url, client, timeout_s)
        self.key_fields = tuple(key_fields)
        self.ignore_fields = frozenset(ignore_fields)
        self.encoding = encoding
        self.delimiter = delimiter
        self.skip_rows = skip_rows
        self.has_header = has_header
        self.columns = columns

    def _rows(self, raw: bytes) -> tuple[list[str], list[dict[str, str]]]:
        try:
            text = raw.decode(self.encoding)
        except UnicodeDecodeError as e:
            raise ExtractionError(f"cannot decode as {self.encoding}: {e}") from e
        text = text.rstrip("\x1a\r\n \t")
        lines = text.splitlines()[self.skip_rows :]
        reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=self.delimiter)
        if self.has_header:
            try:
                header = next(reader)
            except StopIteration:
                raise ExtractionError("no header row") from None
        else:
            if self.columns <= 0:
                raise ExtractionError("has_header=False requires columns > 0")
            header = [f"c{i}" for i in range(self.columns)]
        rows = []
        for i, rec in enumerate(reader):
            if len(rec) != len(header):
                raise ExtractionError(f"row {i} has {len(rec)} columns, expected {len(header)}")
            rows.append(dict(zip(header, rec, strict=True)))
        return header, rows

    def extract(self, raw: bytes) -> list[tuple[str, str]]:
        header, rows = self._rows(raw)
        miss = missing_fields(dict.fromkeys(header), self.key_fields)
        if miss:
            raise ExtractionError(f"header missing key fields {miss}")
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
                f"csv:delim={self.delimiter!r};skip={self.skip_rows};header={self.has_header}",
                f"key:{','.join(self.key_fields)}",
            ),
        )
