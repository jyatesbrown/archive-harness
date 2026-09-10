"""Configuration-driven CSV adapter (header row required)."""

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
        client: HttpClient | None = None,
        timeout_s: float | None = None,
    ):
        super().__init__(url, client, timeout_s)
        self.key_fields = tuple(key_fields)
        self.ignore_fields = frozenset(ignore_fields)
        self.encoding = encoding
        self.delimiter = delimiter
        self.skip_rows = skip_rows

    def _rows(self, raw: bytes) -> tuple[list[str], list[dict[str, str]]]:
        try:
            text = raw.decode(self.encoding)
        except UnicodeDecodeError as e:
            raise ExtractionError(f"cannot decode as {self.encoding}: {e}") from e
        lines = text.splitlines()[self.skip_rows :]
        reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter=self.delimiter)
        if not reader.fieldnames:
            raise ExtractionError("no header row")
        header = list(reader.fieldnames)
        rows = []
        for i, row in enumerate(reader):
            if None in row or any(v is None for v in row.values()):
                raise ExtractionError(f"row {i} has a different column count than the header")
            rows.append(row)
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
                f"csv:delim={self.delimiter!r};skip={self.skip_rows}",
                f"key:{','.join(self.key_fields)}",
            ),
        )
