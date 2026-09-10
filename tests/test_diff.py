from datetime import datetime, timezone

from harness.diff import APPEND_ONLY, BASELINE, DESTRUCTIVE, MUTATING, Window, classify, diff


def test_baseline_when_no_prior():
    r = diff(None, {"a": "1"})
    assert r.classification == BASELINE
    assert r.counts() == {"added": 0, "removed": 0, "mutated": 0, "aged_out": 0, "reappeared": 0, "unchanged": 0}


def test_added_removed_mutated_unchanged():
    prev = {"a": "1", "b": "2", "c": "3"}
    curr = {"a": "1", "b": "X", "d": "4"}
    r = diff(prev, curr)
    assert r.added == {"d"}
    assert r.removed == {"c"}
    assert r.mutated == {"b"}
    assert r.unchanged == 1
    assert r.classification == DESTRUCTIVE


def test_classification_rules():
    assert classify(0, 0) == APPEND_ONLY
    assert classify(0, 1) == MUTATING
    assert classify(1, 0) == DESTRUCTIVE
    assert classify(1, 5) == DESTRUCTIVE
    assert diff({"a": "1"}, {"a": "1", "b": "2"}).classification == APPEND_ONLY
    assert diff({"a": "1"}, {"a": "2"}).classification == MUTATING


def test_reappeared_is_subset_of_added():
    r = diff({"a": "1"}, {"a": "1", "b": "2", "c": "3"}, outstanding_removed={"b", "zzz"})
    assert r.added == {"b", "c"}
    assert r.reappeared == {"b"}


def test_window_ageout_excluded_from_removed():
    as_of = datetime(2026, 9, 10, tzinfo=timezone.utc)
    w = Window(days=7, key_part=0)
    prev = {"2026-08-01|k1": "h", "2026-09-05|k2": "h", "2026-09-06|k3": "h"}
    curr = {"2026-09-06|k3": "h"}
    r = diff(prev, curr, as_of=as_of, window=w)
    assert r.aged_out == {"2026-08-01|k1"}
    assert r.removed == {"2026-09-05|k2"}
    assert r.classification == DESTRUCTIVE


def test_window_only_ageout_is_append_only():
    as_of = datetime(2026, 9, 10, tzinfo=timezone.utc)
    w = Window(days=7, key_part=1)
    prev = {"k1|2026-08-01": "h", "k3|2026-09-06": "h"}
    curr = {"k3|2026-09-06": "h", "k4|2026-09-10": "h"}
    r = diff(prev, curr, as_of=as_of, window=w)
    assert r.aged_out == {"k1|2026-08-01"}
    assert not r.removed
    assert r.classification == APPEND_ONLY


def test_window_unparseable_date_counts_as_removed():
    as_of = datetime(2026, 9, 10, tzinfo=timezone.utc)
    w = Window(days=7, key_part=0)
    r = diff({"notadate|k": "h"}, {}, as_of=as_of, window=w)
    assert r.removed == {"notadate|k"}
    assert not r.aged_out
