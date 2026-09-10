import sqlite3

import pytest

from harness.db import TABLES, Database, utcnow
from tests.conftest import add_json_source


def test_every_table_rejects_update_and_delete(db: Database):
    src = add_json_source(db)
    sid = db.add_snapshot(src.id, utcnow(), 200, 1, "h", None, "p", "0.1", "ok")
    db.add_records(sid, [("k", "v")])
    db.add_run_diff(src.id, sid, None, 0, 0, 0, 0, 0, 0, "baseline")
    db.add_health(src.id, sid, '{"count":1,"fields":[],"selectors":[]}', 1, False, None)
    db.add_key_events(src.id, sid, "removed", ["k"])
    db.add_alert("x", "y", src.id, sid)
    for t in TABLES:
        assert db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] >= 1, t
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.conn.execute(f"DELETE FROM {t}")
        col = db.conn.execute(f"PRAGMA table_info({t})").fetchall()[1][1]
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.conn.execute(f"UPDATE {t} SET {col}={col}")


def test_source_change_is_new_row_and_latest_wins(db: Database):
    s1 = add_json_source(db)
    assert [s.id for s in db.sources()] == [s1.id]
    s2 = add_json_source(db, active=False)
    assert s2.id != s1.id
    assert db.sources() == []
    assert [s.id for s in db.sources(active_only=False)] == [s2.id]
    assert set(db.source_lineage_ids(s2)) == {s1.id, s2.id}


def test_outstanding_removed_keys_follows_latest_event(db: Database):
    src = add_json_source(db)
    a = db.add_snapshot(src.id, utcnow(), 200, 1, "a", None, "p", "0.1", "ok")
    b = db.add_snapshot(src.id, utcnow(), 200, 1, "b", "a", "p", "0.1", "ok")
    db.add_key_events(src.id, a, "removed", ["k1", "k2"])
    db.add_key_events(src.id, b, "reappeared", ["k1"])
    assert db.outstanding_removed_keys(src) == {"k2"}
