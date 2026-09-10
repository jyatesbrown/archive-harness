"""#4 DOL ETA Weekly Claims Data (report r539cy) — HTML.

The landing page (claims.asp) is a form; the table is only returned to a
form POST on wkclaims/report.asp. Key: week-ending date (first column).
Two header rows are combined into column names ("Initial Claims N.S.A", ...).
"""

from __future__ import annotations

import re

from harness.adapter import ExtractionError, FetchResult
from harness.adapters.html_table import HtmlTableAdapter, dedupe, parse_tables, select_table
from harness.fetch import HttpClient

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")


class DolEtaWeeklyClaimsHtml(HtmlTableAdapter):
    version = "1.0"

    def __init__(
        self,
        url: str,
        form: dict[str, str],
        client: HttpClient | None = None,
        timeout_s: float | None = None,
    ):
        super().__init__(
            url, key_fields=("Week Ended",), table_attr="summary=r539cy", client=client, timeout_s=timeout_s
        )
        self.form = form

    def fetch(self) -> FetchResult:
        return self.client.fetch(self.url, timeout_s=self.timeout_s, accept=self.accept, form=self.form)

    def _table(self, page: bytes) -> tuple[list[str], list[dict[str, str]]]:
        t = select_table(parse_tables(page), self.table_index, self.table_attr)
        groups = next((r for r in t.rows if r and r[1:2] == ["Initial Claims"]), None)
        subs = next((r for r in t.rows if r and r[0] == "N.S.A"), None)
        if groups is None or subs is None:
            raise ExtractionError("header rows not recognized")
        header = ["Week Ended"]
        gi = 0
        spans = {"Initial Claims": 4, "Continued Claims": 4, "I.U.R": 2, "Covered Employment": 1}
        names = [g for g in groups[1:] if g]
        for g in names:
            for _ in range(spans.get(g, 1)):
                header.append(f"{g} {subs[gi]}" if gi < len(subs) else g)
                gi += 1
        header = dedupe(header)
        rows = []
        for r in t.rows:
            if r and DATE_RE.match(r[0]):
                if len(r) != len(header):
                    raise ExtractionError(f"row has {len(r)} cells, header has {len(header)}")
                rows.append(dict(zip(header, r, strict=True)))
        return header, rows
