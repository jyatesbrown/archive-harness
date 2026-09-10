"""Diff and classification engine. Operates purely on {record_key: value_hash} maps."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from harness.adapter import KEY_SEP

APPEND_ONLY = "append_only"
MUTATING = "mutating"
DESTRUCTIVE = "destructive"
BASELINE = "baseline"


@dataclass(frozen=True)
class Window:
    """Rolling window keyed on record creation date.

    `key_part` is the index of the composite-key component that carries an
    ISO date (YYYY-MM-DD prefix). Keys whose date is older than
    `as_of - days` are outside the window and their disappearance is aging,
    not destruction.
    """

    days: int
    key_part: int

    def floor(self, as_of: datetime) -> date:
        return as_of.date() - timedelta(days=self.days)

    def is_outside(self, record_key: str, as_of: datetime) -> bool:
        parts = record_key.split(KEY_SEP)
        if self.key_part >= len(parts):
            return False
        try:
            d = date.fromisoformat(parts[self.key_part][:10])
        except ValueError:
            return False
        return d < self.floor(as_of)


@dataclass
class DiffResult:
    added: set[str] = field(default_factory=set)
    removed: set[str] = field(default_factory=set)
    mutated: set[str] = field(default_factory=set)
    aged_out: set[str] = field(default_factory=set)
    reappeared: set[str] = field(default_factory=set)
    unchanged: int = 0
    classification: str = BASELINE

    def counts(self) -> dict[str, int]:
        return {
            "added": len(self.added),
            "removed": len(self.removed),
            "mutated": len(self.mutated),
            "aged_out": len(self.aged_out),
            "reappeared": len(self.reappeared),
            "unchanged": self.unchanged,
        }


def classify(removed: int, mutated: int) -> str:
    if removed:
        return DESTRUCTIVE
    if mutated:
        return MUTATING
    return APPEND_ONLY


def diff(
    prev: Mapping[str, str] | None,
    curr: Mapping[str, str],
    *,
    as_of: datetime | None = None,
    window: Window | None = None,
    outstanding_removed: set[str] | None = None,
) -> DiffResult:
    """Compare the current record set with the prior successful one.

    `outstanding_removed` is the set of keys previously recorded as removed and
    not yet seen again; any of them present in `curr` is also reported as
    reappeared (transient disappearance).
    """
    r = DiffResult()
    if prev is None:
        return r

    prev_keys = set(prev)
    curr_keys = set(curr)
    r.added = curr_keys - prev_keys
    gone = prev_keys - curr_keys
    if window is not None and as_of is not None:
        r.aged_out = {k for k in gone if window.is_outside(k, as_of)}
        gone = gone - r.aged_out
    r.removed = gone
    both = prev_keys & curr_keys
    r.mutated = {k for k in both if prev[k] != curr[k]}
    r.unchanged = len(both) - len(r.mutated)
    if outstanding_removed:
        # Subset of `added`: keys that were previously removed and are back.
        r.reappeared = r.added & outstanding_removed
    r.classification = classify(len(r.removed), len(r.mutated))
    return r
