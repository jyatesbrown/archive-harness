"""#11 OFAC Recent Actions — HTML list (Drupal view, 10 per page).

Pages are fetched until an entry is older than `window_days` (rolling
window keyed on the action date) or max_pages is hit. Key: <date>|<path>;
value: title and category link text.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from harness.adapter import ExtractionError, FetchResult, Fingerprint, value_hash
from harness.adapters.base import HttpAdapter, split_pages
from harness.adapters.generic_json import with_query
from harness.adapters.html_table import decode_html
from harness.fetch import HttpClient

ROW_RE = re.compile(
    r'<div class="[^"]*views-row[^"]*">.*?<a href="(?P<href>/recent-actions/[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r".*?(?P<date>[A-Z][a-z]+ \d{2}, \d{4})\s*-\s*(?:<a [^>]*>(?P<cat>.*?)</a>)?",
    re.S,
)


def _clean(s: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", s).split())


class OfacRecentActionsHtml(HttpAdapter):
    version = "1.0"
    accept = "text/html, */*"
    max_pages = 40

    def __init__(
        self,
        url: str,
        window_days: int = 30,
        today: str | None = None,
        client: HttpClient | None = None,
        timeout_s: float | None = None,
    ):
        super().__init__(url, client, timeout_s)
        self.window_days = window_days
        self.today = date.fromisoformat(today) if today else date.today()

    def fetch(self) -> FetchResult:
        return self.fetch_paged(self._next)

    def _next(self, i: int, last: FetchResult) -> str | None:
        rows = self._page_rows(last.body)
        if not rows:
            return None
        oldest = min(date.fromisoformat(r[0]) for r in rows)
        if oldest < self.today - timedelta(days=self.window_days):
            return None
        return with_query(self.url, page=str(i))

    def _page_rows(self, page: bytes) -> list[tuple[str, str, str, str]]:
        html = decode_html(page)
        out = []
        for m in ROW_RE.finditer(html):
            d = datetime.strptime(m.group("date"), "%B %d, %Y").date().isoformat()
            out.append((d, m.group("href"), _clean(m.group("title")), _clean(m.group("cat") or "")))
        if not out and "views-row" in html:
            raise ExtractionError("views-row markup present but no entries matched")
        return out

    def _rows(self, raw: bytes) -> list[tuple[str, str, str, str]]:
        rows = []
        for page in split_pages(raw):
            rows.extend(self._page_rows(page))
        if not rows:
            raise ExtractionError("no recent-actions entries found")
        return rows

    def extract(self, raw: bytes) -> list[tuple[str, str]]:
        out = []
        seen: set[str] = set()
        for d, href, title, cat in self._rows(raw):
            key = f"{d}|{href}"
            if key in seen:
                raise ExtractionError(f"duplicate record_key {key!r}")
            seen.add(key)
            out.append((key, value_hash([("title", title), ("category", cat)])))
        return out

    def fingerprint(self, raw: bytes) -> Fingerprint:
        rows = self._rows(raw)
        return Fingerprint(
            record_count=len(rows),
            field_names=("date", "href", "title", "category"),
            selectors=("div.views-row a[href^=/recent-actions/]", "key:date,href"),
        )
