"""End-to-end runs against the throwaway JSON and CSV fixtures."""

from __future__ import annotations

import json

from harness.adapter import ExtractionError
from harness.fetch import FetchError, RobotsDisallowed, RobotsUnavailable
from harness.report import findings, run_summary, verify_integrity
from tests.conftest import add_csv_source, add_json_source, fixture_bytes


def _ok_snaps(db, src):
    return [s for s in db.snapshots_for(src) if s.outcome == "ok"]


def test_json_baseline_then_diff(harness):
    runner, scripted, db, store, clock = harness
    src = add_json_source(db)
    runner.adapter_factory(src, runner.client, clock.now())
    scripted[src.name].push(fixture_bytes("records_v1.json"))
    scripted[src.name].push(fixture_bytes("records_v2.json"))

    r1 = runner.run()
    assert r1.results[0].outcome == "ok"
    assert r1.results[0].record_count == 5
    assert r1.results[0].diff.classification == "baseline"

    clock.advance()
    r2 = runner.run()
    res = r2.results[0]
    assert res.outcome == "ok"
    d = res.diff
    assert d.added == {"A-006"}
    assert d.removed == {"A-002"}
    assert d.mutated == {"A-004"}
    assert d.unchanged == 3
    assert d.classification == "destructive"

    rows = db.run_diffs_for(src)
    assert [r["classification"] for r in rows] == ["baseline", "destructive"]
    assert (rows[1]["added"], rows[1]["removed"], rows[1]["mutated"]) == (1, 1, 1)
    assert db.key_event_counts(src) == {"removed": 1}
    assert db.outstanding_removed_keys(src) == {"A-002"}

    # every snapshot has a retained payload and the prev_hash chain is intact
    snaps = db.snapshots_for(src)
    assert snaps[0].prev_hash is None
    assert snaps[1].prev_hash == snaps[0].content_hash
    assert all(store.exists(s.raw_path) for s in snaps)
    assert verify_integrity(db, store).ok
    assert "destructive" in run_summary(r2)


def test_csv_composite_key_and_mutation(harness):
    runner, scripted, db, store, clock = harness
    src = add_csv_source(db)
    runner.adapter_factory(src, runner.client, clock.now())
    scripted[src.name].push(fixture_bytes("table_v1.csv"))
    scripted[src.name].push(fixture_bytes("table_v2.csv"))
    runner.run()
    clock.advance()
    d = runner.run().results[0].diff
    assert d.added == {"2026-08-30|X", "2026-08-30|Y"}
    assert d.removed == {"2026-08-25|X", "2026-08-25|Y"}
    assert d.mutated == {"2026-08-28|Y"}
    assert d.classification == "destructive"


def test_csv_rolling_window_ageout_not_destruction(harness):
    """Same CSV pair, but declared as a 4-day window keyed on the date part:
    the 08-25 rows aging out are not removals, so the run is 'mutating'."""
    runner, scripted, db, store, clock = harness
    clock.t = clock.t.replace(day=1)  # 2026-09-01 -> floor 2026-08-28... use explicit dates below
    src = add_csv_source(db, window_days=4, window_key_part=0)
    runner.adapter_factory(src, runner.client, clock.now())
    scripted[src.name].push(fixture_bytes("table_v1.csv"))
    scripted[src.name].push(fixture_bytes("table_v2.csv"))
    from datetime import datetime, timezone

    clock.t = datetime(2026, 8, 29, 6, tzinfo=timezone.utc)
    runner.run()
    clock.t = datetime(2026, 8, 30, 6, tzinfo=timezone.utc)  # floor = 08-26; 08-25 rows are outside
    d = runner.run().results[0].diff
    assert d.aged_out == {"2026-08-25|X", "2026-08-25|Y"}
    assert d.removed == set()
    assert d.mutated == {"2026-08-28|Y"}
    assert d.classification == "mutating"
    row = db.run_diffs_for(src)[-1]
    assert (row["removed"], row["aged_out"]) == (0, 2)
    assert db.key_event_counts(src) == {"aged_out": 2}


def test_transient_vs_permanent_removal(harness):
    runner, scripted, db, store, clock = harness
    src = add_json_source(db)
    runner.adapter_factory(src, runner.client, clock.now())
    v1, v2 = fixture_bytes("records_v1.json"), fixture_bytes("records_v2.json")
    for body in (v1, v2, v1):  # A-002 vanishes in v2, returns in v1; A-006 vanishes and stays gone
        scripted[src.name].push(body)
        runner.run()
        clock.advance()
    ev = db.key_event_counts(src)
    assert ev == {"removed": 2, "reappeared": 1}
    assert db.outstanding_removed_keys(src) == {"A-006"}
    last = db.run_diffs_for(src)[-1]
    assert last["reappeared"] == 1 and last["added"] == 1 and last["removed"] == 1
    text = findings(db)
    assert "fixture_json" in text
    line = next(line for line in text.splitlines() if "fixture_json" in line)
    cols = line.split()
    # perm=1 (A-006), trans=1 (A-002)
    assert cols[7] == "1" and cols[8] == "1"


def test_drift_rejects_extraction_and_alerts(harness):
    runner, scripted, db, store, clock = harness
    src = add_json_source(db)
    runner.adapter_factory(src, runner.client, clock.now())
    scripted[src.name].push(fixture_bytes("records_v1.json"))
    scripted[src.name].push(fixture_bytes("records_v3_drift.json"))
    runner.run()
    clock.advance()
    res = runner.run().results[0]
    assert res.outcome == "validation_failed"
    assert res.drifted
    assert "colour" in res.detail and "color" in res.detail
    assert any("drift" in a for a in res.alerts)
    snaps = db.snapshots_for(src)
    assert len(snaps) == 2
    assert snaps[1].outcome == "validation_failed"
    assert store.exists(snaps[1].raw_path)  # raw retained
    assert db.record_count(snaps[1].id) == 0  # nothing indexed
    assert db.run_diffs_for(src)[-1]["snapshot_id"] == snaps[0].id  # no diff row for failed run
    health = db.health_for(src)
    assert [h[3] for h in health] == [0, 1]
    assert verify_integrity(db, store).ok


def test_count_deviation_and_zero_records(harness):
    runner, scripted, db, store, clock = harness
    src = add_json_source(db)
    runner.adapter_factory(src, runner.client, clock.now())
    doc = json.loads(fixture_bytes("records_v1.json"))
    scripted[src.name].push(fixture_bytes("records_v1.json"))
    doc["data"]["items"] = doc["data"]["items"][:2]  # 5 -> 2 = 60% drop
    scripted[src.name].push(json.dumps(doc).encode())
    doc["data"]["items"] = []
    scripted[src.name].push(json.dumps(doc).encode())
    doc["data"]["items"] = json.loads(fixture_bytes("records_v1.json"))["data"]["items"][:4]  # 5 -> 4 = 20%
    scripted[src.name].push(json.dumps(doc).encode())

    runner.run()
    outcomes = []
    for _ in range(3):
        clock.advance()
        r = runner.run().results[0]
        outcomes.append((r.outcome, r.detail.split(";")[0]))
    assert outcomes[0][0] == "validation_failed" and "deviates 60%" in outcomes[0][1]
    assert outcomes[1][0] == "validation_failed" and "zero records" in outcomes[1][1]
    assert outcomes[2][0] == "ok"  # compared against the last *ok* snapshot (5), 20% is within limit
    kinds = [a[3] for a in db.alerts_since(0)]
    assert "validation_failed" in kinds
    assert "consecutive_failures" in kinds  # two non-ok runs in a row


def test_fetch_failures_are_recorded_and_isolated(harness):
    runner, scripted, db, store, clock = harness
    a = add_json_source(db, name="a")
    b = add_csv_source(db, name="b")
    c = add_json_source(db, name="c")
    d = add_json_source(db, name="d")
    e = add_json_source(db, name="e")
    for s in (a, b, c, d, e):
        runner.adapter_factory(s, runner.client, clock.now())
    scripted["a"].push(FetchError("HTTP 403", 403, b"forbidden"))
    scripted["b"].push(fixture_bytes("table_v1.csv"))
    scripted["c"].push(RobotsDisallowed("robots 200 parsed"))
    scripted["d"].push(RobotsUnavailable("robots 503: assume disallow"))
    scripted["e"].push(RuntimeError("adapter bug"))

    run = runner.run()
    by = {r.source.name: r for r in run.results}
    assert by["a"].outcome == "fetch_failed" and any("endpoint_refused" in x for x in by["a"].alerts)
    assert by["b"].outcome == "ok"
    assert by["c"].outcome == "robots_disallowed"
    assert by["d"].outcome == "fetch_failed"
    assert by["e"].outcome == "fetch_failed" and "adapter bug" in by["e"].detail
    # failed fetch with a body still retains the raw payload
    sa = db.snapshots_for(a)[0]
    assert sa.http_status == 403 and store.exists(sa.raw_path)
    assert sum(1 for _ in db.all_snapshots()) == 5
    summary = run_summary(run)
    assert "1/5 sources ok" in summary and "ALERTS:" in summary


def test_extract_failure_retains_payload_and_indexes_nothing(harness):
    runner, scripted, db, store, clock = harness
    src = add_json_source(db)
    runner.adapter_factory(src, runner.client, clock.now())
    scripted[src.name].push(b"<html>maintenance</html>")
    res = runner.run().results[0]
    assert res.outcome == "extract_failed"
    snap = db.snapshots_for(src)[0]
    assert store.exists(snap.raw_path)
    assert db.record_count(snap.id) == 0


def test_scripted_adapter_raises_extraction_error_type():
    # guard: the fixture adapters raise ExtractionError, which the runner maps to extract_failed
    from harness.adapters.generic_json import GenericJsonAdapter

    try:
        GenericJsonAdapter(url="x", key_fields=["id"]).extract(b"nope")
    except ExtractionError:
        pass
    else:  # pragma: no cover
        raise AssertionError


def _items(*ids: str) -> bytes:
    return json.dumps(
        {"data": {"items": [{"id": i, "created": "2026-09-01", "color": "x", "qty": 1} for i in ids]}}
    ).encode()


def test_exit_condition_removals_are_checked_against_target(harness):
    """Amendment B §3/§6: keys leaving Public Inspection are looked up in the
    Documents API key space; hits are 'exited', misses stay outstanding."""
    runner, scripted, db, store, clock = harness
    docs = add_json_source(db, "docs")
    queue = add_json_source(db, "queue", exit_target="docs", control_group="EXIT-CONDITION")
    for s in (docs, queue):
        runner.adapter_factory(s, runner.client, clock.now())
    filler = [f"D-{i}" for i in range(10)]
    stay = [f"P{i}" for i in range(3, 10)]
    # day 1: queue holds P1..P9, docs has nothing relevant
    scripted["docs"].push(_items(*filler))
    scripted["queue"].push(_items("P1", "P2", *stay))
    runner.run()
    clock.advance()
    # day 2: P1 and P2 leave the queue; P1 is published in docs, P2 is not
    scripted["docs"].push(_items(*filler, "P1"))
    scripted["queue"].push(_items(*stay))
    runner.run()
    clock.advance()
    ev = db.key_event_counts(queue)
    assert ev["removed"] == 2 and ev["exited"] == 1
    assert db.outstanding_removed_keys(queue) == {"P2"}
    assert db.run_diffs_for(queue)[-1]["classification"] == "destructive"
    # day 3: P2 shows up in docs on a later run -> exited then, not permanent
    scripted["docs"].push(_items(*filler, "P1", "P2"))
    scripted["queue"].push(_items(*stay))
    runner.run()
    assert db.key_event_counts(queue)["exited"] == 2
    assert db.outstanding_removed_keys(queue) == set()
    text = findings(db)
    assert "EXIT-CONDITION RESULTS" in text
    line = next(line for line in text.splitlines() if line.strip().startswith("queue:"))
    assert "2 distinct removed keys, 2 found in docs -> reappearance rate 100.0%; 0 unaccounted for" in line
    row = next(line for line in text.splitlines() if " queue " in line)
    cols = row.split()
    assert cols[7] == "0", "exited keys must not count as permanent destruction"


def test_exit_target_missing_alerts_instead_of_crashing(harness):
    runner, scripted, db, store, clock = harness
    q = add_json_source(db, "queue", exit_target="nope")
    runner.adapter_factory(q, runner.client, clock.now())
    scripted["queue"].push(_items("P1", "P2", "P3", "P4"))
    runner.run()
    clock.advance()
    scripted["queue"].push(_items("P1", "P2", "P3"))
    runner.run()
    assert any(a[3] == "exit_target_missing" for a in db.alerts_since(0))


def test_expected_silent_reported_separately(harness):
    runner, scripted, db, store, clock = harness
    quiet = add_json_source(db, "quiet", expected_silent=True, cadence="quarterly (inferred)")
    loud = add_json_source(db, "steady")
    for s in (quiet, loud):
        runner.adapter_factory(s, runner.client, clock.now())
    for _ in range(3):
        scripted["quiet"].push(_items("Q1", "Q2"))
        scripted["steady"].push(_items("S1", "S2"))
        runner.run()
        clock.advance()
    text = findings(db)
    measured = next(line for line in text.splitlines() if "measured-stable:" in line)
    silent = next(line for line in text.splitlines() if "expected-silent" in line and "quiet" in line)
    assert "steady" in measured and "quiet" not in measured
    assert "quiet" in silent
