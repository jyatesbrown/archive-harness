"""#2 Treasury Daily Par Yield Curve — HTML [FORMAT-CONTROL with #1]. Key: Date."""

from __future__ import annotations

from harness.adapters.html_table import HtmlTableAdapter
from harness.fetch import HttpClient


class TreasuryYieldHtml(HtmlTableAdapter):
    version = "1.0"

    def __init__(self, url: str, client: HttpClient | None = None, timeout_s: float | None = None):
        super().__init__(
            url,
            key_fields=("Date",),
            table_attr="class=views-table",
            client=client,
            timeout_s=timeout_s,
        )
