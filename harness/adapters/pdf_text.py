"""PDF adapters: text is recovered with poppler's `pdftotext -layout`.

Subclasses implement `records(text) -> list[(key, value_fields)]`. The
pdftotext binary and version are part of the fingerprint selectors, since a
poppler upgrade can change layout output without any change at the source.
"""

from __future__ import annotations

import functools
import shutil
import subprocess

from harness.adapter import ExtractionError, Fingerprint, value_hash
from harness.adapters.base import HttpAdapter


@functools.cache
def pdftotext_version() -> str:
    exe = shutil.which("pdftotext")
    if not exe:
        raise ExtractionError("pdftotext not installed")
    out = subprocess.run([exe, "-v"], capture_output=True, text=True, check=False)
    first = (out.stderr or out.stdout).strip().splitlines()
    return first[0] if first else "pdftotext"


def pdf_to_text(raw: bytes) -> str:
    if not raw.startswith(b"%PDF"):
        raise ExtractionError("payload is not a PDF")
    exe = shutil.which("pdftotext")
    if not exe:
        raise ExtractionError("pdftotext not installed")
    proc = subprocess.run(
        [exe, "-layout", "-enc", "UTF-8", "-", "-"],
        input=raw,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ExtractionError(f"pdftotext failed: {proc.stderr.decode(errors='replace')[:200]}")
    return proc.stdout.decode("utf-8", errors="replace")


class PdfTextAdapter(HttpAdapter):
    version = "0.1"
    accept = "application/pdf, */*"
    field_names: tuple[str, ...] = ()
    selectors: tuple[str, ...] = ()

    def records(self, text: str) -> list[tuple[str, list[tuple[str, str]]]]:
        raise NotImplementedError

    def extract(self, raw: bytes) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for key, vals in self.records(pdf_to_text(raw)):
            if key in seen:
                raise ExtractionError(f"duplicate record_key {key!r}")
            seen.add(key)
            out.append((key, value_hash(vals)))
        return out

    def fingerprint(self, raw: bytes) -> Fingerprint:
        recs = self.records(pdf_to_text(raw))
        fields: set[str] = set(self.field_names)
        for _, vals in recs:
            fields.update(k for k, _ in vals)
        return Fingerprint(
            record_count=len(recs),
            field_names=tuple(sorted(fields)),
            selectors=(pdftotext_version(), *self.selectors),
        )
