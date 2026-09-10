"""#3 DOL Unemployment Insurance Weekly Claims news release — PDF.

Records (all keyed by week-ending date, ISO):
  sa_weekly|<date>            rows of the "Seasonally Adjusted US Weekly UI
                              Claims (in thousands)" table (7 numeric columns)
  national|b<n>|<label>|<date>
                              cells of the n-th "WEEK ENDING" summary block on
                              the data page, one record per label x dated
                              column (derived Change / Prior Year columns
                              skipped); n disambiguates labels repeated across
                              blocks ("4-Wk Moving Average (SA)")
  state|<state>|<measure>|<date>
                              "Advance State Claims" table, Advance and Prior
                              Wk columns for initial claims and insured
                              unemployment
Dates without a year take the year of the release date on page 1.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from harness.adapter import ExtractionError
from harness.adapters.pdf_text import PdfTextAdapter

RELEASE_RE = re.compile(
    r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+([A-Z][a-z]+ \d{1,2}, \d{4})"
)
FULL_DATE_RE = re.compile(r"^([A-Z][a-z]+ \d{1,2}, \d{4})\s+(.*)$")
MONTH_DAY_RE = re.compile(r"[A-Z][a-z]+ \d{1,2}")
NUM_RE = re.compile(r"^[+-]?[\d,]+(?:\.\d+)?%?$")
STATE_HDR_RE = re.compile(
    r"Initial Claims Filed During Week Ended ([A-Z][a-z]+ \d{1,2})\s+"
    r"Insured Unemployment For Week Ended ([A-Z][a-z]+ \d{1,2})"
)


def _iso(month_day: str, year: int, release: date) -> str:
    d = datetime.strptime(f"{month_day} {year}", "%B %d %Y").date()
    if d > release:
        d = d.replace(year=year - 1)
    return d.isoformat()


def _split_cols(line: str) -> list[str]:
    return [c for c in re.split(r"\s{2,}", line.strip()) if c]


class DolUiClaimsPdf(PdfTextAdapter):
    version = "1.0"
    selectors = (
        "sa_weekly:Seasonally Adjusted US Weekly UI Claims",
        "national:WEEK ENDING",
        "state:Advance State Claims",
    )

    def records(self, text: str) -> list[tuple[str, list[tuple[str, str]]]]:
        m = RELEASE_RE.search(text)
        if not m:
            raise ExtractionError("release date not found on page 1")
        release = datetime.strptime(m.group(1), "%B %d, %Y").date()
        lines = text.splitlines()
        out: list[tuple[str, list[tuple[str, str]]]] = []
        out += self._national(lines, release)
        out += self._states(lines, release)
        out += self._sa_weekly(lines)
        if not out:
            raise ExtractionError("no tables recognized")
        return out

    def _national(self, lines: list[str], release: date) -> list[tuple[str, list[tuple[str, str]]]]:
        out = []
        i = 0
        block = 0
        while i < len(lines):
            line = lines[i]
            if line.strip().startswith("WEEK ENDING"):
                block += 1
                cols = _split_cols(line)[1:]
                dated = [(j, _iso(c, release.year, release)) for j, c in enumerate(cols) if MONTH_DAY_RE.fullmatch(c)]
                i += 1
                while i < len(lines) and lines[i].strip():
                    cells = _split_cols(lines[i])
                    if len(cells) == len(cols) + 1 and all(NUM_RE.match(c) for c in cells[1:]):
                        label = re.sub(r"\d+$", "", cells[0]).strip()
                        for j, d in dated:
                            out.append((f"national|b{block}|{label}|{d}", [("value", cells[1 + j])]))
                    i += 1
            i += 1
        return out

    def _states(self, lines: list[str], release: date) -> list[tuple[str, list[tuple[str, str]]]]:
        out = []
        for i, line in enumerate(lines):
            m = STATE_HDR_RE.search(line)
            if not m:
                continue
            d_init = _iso(m.group(1), release.year, release)
            d_ins = _iso(m.group(2), release.year, release)
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("STATE"):
                j += 1
            j += 1
            while j < len(lines):
                cells = _split_cols(lines[j])
                if not cells:
                    j += 1
                    if j < len(lines) and not lines[j].strip():
                        break
                    continue
                if len(cells) == 7 and all(NUM_RE.match(c) for c in cells[1:]):
                    st = cells[0].rstrip("*").strip()
                    out.append((f"state|{st}|initial|{d_init}", [("value", cells[1])]))
                    out.append((f"state|{st}|insured|{d_ins}", [("value", cells[4])]))
                elif cells[0].startswith("Note"):
                    break
                j += 1
            break
        return out

    def _sa_weekly(self, lines: list[str]) -> list[tuple[str, list[tuple[str, str]]]]:
        out = []
        for line in lines:
            m = FULL_DATE_RE.match(line.strip())
            if not m:
                continue
            cells = _split_cols(m.group(2))
            if len(cells) == 7 and all(NUM_RE.match(c) for c in cells):
                d = datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
                names = ("initial", "initial_chg", "initial_4wk", "insured", "insured_chg", "insured_4wk", "iur")
                out.append((f"sa_weekly|{d}", list(zip(names, cells, strict=True))))
        return out
