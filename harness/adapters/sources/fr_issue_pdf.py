"""#7 Federal Register daily issue — GovInfo PDF (date-templated endpoint).

Key: <issue date>|<FR Doc number>, issue date taken from the URL
(FR-YYYY-MM-DD). Value: the "[FR Doc. ... Filed ...]" marker and the
billing code that follows it. Configured as a 1-day rolling window so the
previous issue's keys age out rather than count as removals.
"""

from __future__ import annotations

import re

from harness.adapter import ExtractionError
from harness.adapters.pdf_text import PdfTextAdapter

URL_DATE_RE = re.compile(r"FR-(\d{4}-\d{2}-\d{2})")
DOC_RE = re.compile(r"\[FR Doc\.\s+([\dA-Z]+[\u2013\u2014-][\d]+)\s+Filed\s+([^\]]*)\]")
BILLING_RE = re.compile(r"BILLING CODE\s+([\dA-Z\u2013-]+)")


class FrIssuePdf(PdfTextAdapter):
    version = "1.0"
    field_names = ("filed", "billing_code")
    selectors = ("marker:[FR Doc. N Filed ...]", "key:url_date,doc_number")

    def records(self, text: str) -> list[tuple[str, list[tuple[str, str]]]]:
        m = URL_DATE_RE.search(self.url)
        if not m:
            raise ExtractionError(f"issue date not in URL {self.url!r}")
        issue = m.group(1)
        out = []
        flat = " ".join(text.split())
        for dm in DOC_RE.finditer(flat):
            num = dm.group(1).replace("\u2013", "-").replace("\u2014", "-")
            tail = flat[dm.end() : dm.end() + 200]
            bm = BILLING_RE.search(tail)
            billing = bm.group(1).replace("\u2013", "-") if bm else ""
            out.append((f"{issue}|{num}", [("filed", " ".join(dm.group(2).split())), ("billing_code", billing)]))
        if not out:
            raise ExtractionError("no [FR Doc.] markers found")
        return out
