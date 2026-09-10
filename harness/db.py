"""SQLite persistence. Append-only: UPDATE and DELETE are rejected by triggers on
every table. Corrections are new rows.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

TABLES = (
    "sources",
    "snapshots",
    "record_index",
    "run_diffs",
    "source_health",
    "key_events",
    "alerts",
    "drift_acceptances",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    tier            TEXT NOT NULL,
    endpoint        TEXT NOT NULL,
    format          TEXT NOT NULL,
    adapter_module  TEXT NOT NULL,
    adapter_config  TEXT NOT NULL DEFAULT '{}',
    identity_key    TEXT NOT NULL,
    license_url     TEXT NOT NULL,
    window_days     INTEGER,
    window_key_part INTEGER,
    control_group   TEXT,
    timeout_s       REAL,
    cadence         TEXT,
    expected_silent INTEGER NOT NULL DEFAULT 0,
    unverified_contrary_claim INTEGER NOT NULL DEFAULT 0,
    exit_target     TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    added_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id              INTEGER PRIMARY KEY,
    source_id       INTEGER NOT NULL REFERENCES sources(id),
    fetched_at      TEXT NOT NULL,
    http_status     INTEGER,
    byte_length     INTEGER,
    content_hash    TEXT,
    prev_hash       TEXT,
    raw_path        TEXT,
    adapter_version TEXT NOT NULL,
    outcome         TEXT NOT NULL CHECK (outcome IN
                        ('ok','fetch_failed','extract_failed','validation_failed','robots_disallowed')),
    detail          TEXT,
    duration_s      REAL
);
CREATE INDEX IF NOT EXISTS snapshots_source_idx ON snapshots(source_id, id);

CREATE TABLE IF NOT EXISTS record_index (
    snapshot_id     INTEGER NOT NULL REFERENCES snapshots(id),
    record_key      TEXT NOT NULL,
    value_hash      TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, record_key)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS run_diffs (
    id              INTEGER PRIMARY KEY,
    source_id       INTEGER NOT NULL REFERENCES sources(id),
    snapshot_id     INTEGER NOT NULL REFERENCES snapshots(id),
    prev_snapshot_id INTEGER REFERENCES snapshots(id),
    added           INTEGER NOT NULL,
    removed         INTEGER NOT NULL,
    mutated         INTEGER NOT NULL,
    aged_out        INTEGER NOT NULL DEFAULT 0,
    reappeared      INTEGER NOT NULL DEFAULT 0,
    unchanged       INTEGER NOT NULL,
    classification  TEXT NOT NULL CHECK (classification IN
                        ('append_only','mutating','destructive','baseline'))
);

CREATE TABLE IF NOT EXISTS source_health (
    id              INTEGER PRIMARY KEY,
    source_id       INTEGER NOT NULL REFERENCES sources(id),
    snapshot_id     INTEGER NOT NULL REFERENCES snapshots(id),
    fingerprint     TEXT NOT NULL,
    record_count    INTEGER NOT NULL,
    drifted         INTEGER NOT NULL,
    drift_detail    TEXT
);

-- Lifecycle of keys that leave the record set. 'removed' when a key vanishes,
-- 'reappeared' when a previously removed key returns, 'aged_out' when a key
-- leaves a rolling window by creation date (not destruction), 'exited' when a
-- removed key is found in the key space of the source's exit_target.
CREATE TABLE IF NOT EXISTS key_events (
    id              INTEGER PRIMARY KEY,
    source_id       INTEGER NOT NULL REFERENCES sources(id),
    snapshot_id     INTEGER NOT NULL REFERENCES snapshots(id),
    record_key      TEXT NOT NULL,
    event           TEXT NOT NULL CHECK (event IN ('removed','reappeared','aged_out','exited'))
);
CREATE INDEX IF NOT EXISTS key_events_idx ON key_events(source_id, record_key, id);

CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY,
    raised_at       TEXT NOT NULL,
    source_id       INTEGER REFERENCES sources(id),
    snapshot_id     INTEGER REFERENCES snapshots(id),
    kind            TEXT NOT NULL,
    message         TEXT NOT NULL
);

-- Operator acknowledgement that a structural fingerprint is the new expected
-- shape. Runs matching an accepted fingerprint are not rejected for drift;
-- the drift itself is still recorded in source_health.
CREATE TABLE IF NOT EXISTS drift_acceptances (
    id              INTEGER PRIMARY KEY,
    source_id       INTEGER NOT NULL REFERENCES sources(id),
    fingerprint     TEXT NOT NULL,
    accepted_at     TEXT NOT NULL,
    note            TEXT
);
"""


def _trigger_sql() -> str:
    parts = []
    for t in TABLES:
        for op in ("UPDATE", "DELETE"):
            parts.append(
                f"CREATE TRIGGER IF NOT EXISTS {t}_no_{op.lower()} BEFORE {op} ON {t} "
                f"BEGIN SELECT RAISE(ABORT, 'append-only: {op} on {t} is forbidden'); END;"
            )
    return "\n".join(parts)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class Source:
    id: int
    name: str
    tier: str
    endpoint: str
    format: str
    adapter_module: str
    adapter_config: str
    identity_key: str
    license_url: str
    window_days: int | None
    window_key_part: int | None
    control_group: str | None
    timeout_s: float | None
    cadence: str | None
    expected_silent: bool
    unverified_contrary_claim: bool
    exit_target: str | None
    active: bool


@dataclass(frozen=True)
class Snapshot:
    id: int
    source_id: int
    fetched_at: str
    http_status: int | None
    byte_length: int | None
    content_hash: str | None
    prev_hash: str | None
    raw_path: str | None
    adapter_version: str
    outcome: str
    detail: str | None
    duration_s: float | None


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self.conn.executescript(_trigger_sql())

    def close(self) -> None:
        self.conn.close()

    # ---- sources -------------------------------------------------------
    def add_source(
        self,
        name: str,
        tier: str,
        endpoint: str,
        format: str,
        adapter_module: str,
        identity_key: str,
        license_url: str,
        adapter_config: str = "{}",
        window_days: int | None = None,
        window_key_part: int | None = None,
        control_group: str | None = None,
        timeout_s: float | None = None,
        cadence: str | None = None,
        expected_silent: bool = False,
        unverified_contrary_claim: bool = False,
        exit_target: str | None = None,
        active: bool = True,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO sources (name,tier,endpoint,format,adapter_module,adapter_config,"
            "identity_key,license_url,window_days,window_key_part,control_group,timeout_s,cadence,"
            "expected_silent,unverified_contrary_claim,exit_target,active,added_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                name,
                tier,
                endpoint,
                format,
                adapter_module,
                adapter_config,
                identity_key,
                license_url,
                window_days,
                window_key_part,
                control_group,
                timeout_s,
                cadence,
                int(expected_silent),
                int(unverified_contrary_claim),
                exit_target,
                int(active),
                iso(utcnow()),
            ),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)

    def sources(self, active_only: bool = True) -> list[Source]:
        """Latest row per source name wins (deactivation is a new row with active=0)."""
        rows = self.conn.execute(
            "SELECT s.id,s.name,s.tier,s.endpoint,s.format,s.adapter_module,s.adapter_config,"
            " s.identity_key,s.license_url,s.window_days,s.window_key_part,s.control_group,"
            " s.timeout_s,s.cadence,s.expected_silent,s.unverified_contrary_claim,s.exit_target,s.active"
            " FROM sources s JOIN (SELECT name, MAX(id) AS mid FROM sources GROUP BY name) m"
            " ON s.id = m.mid ORDER BY s.id"
        ).fetchall()
        out = [
            Source(
                *r[:14],  # type: ignore[misc]
                expected_silent=bool(r[14]),
                unverified_contrary_claim=bool(r[15]),
                exit_target=r[16],
                active=bool(r[17]),
            )
            for r in rows
        ]
        return [s for s in out if s.active] if active_only else out

    def source_by_name(self, name: str) -> Source | None:
        for s in self.sources(active_only=False):
            if s.name == name:
                return s
        return None

    def source_lineage_ids(self, source: Source) -> list[int]:
        """All row ids that share this source's name (history follows the name)."""
        rows = self.conn.execute("SELECT id FROM sources WHERE name=?", (source.name,)).fetchall()
        return [r[0] for r in rows]

    # ---- snapshots -----------------------------------------------------
    def add_snapshot(
        self,
        source_id: int,
        fetched_at: datetime,
        http_status: int | None,
        byte_length: int | None,
        content_hash: str | None,
        prev_hash: str | None,
        raw_path: str | None,
        adapter_version: str,
        outcome: str,
        detail: str | None = None,
        duration_s: float | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO snapshots (source_id,fetched_at,http_status,byte_length,content_hash,"
            "prev_hash,raw_path,adapter_version,outcome,detail,duration_s) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                source_id,
                iso(fetched_at),
                http_status,
                byte_length,
                content_hash,
                prev_hash,
                raw_path,
                adapter_version,
                outcome,
                detail,
                duration_s,
            ),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)

    def _snap_rows(self, sql: str, params: Sequence[object]) -> list[Snapshot]:
        rows = self.conn.execute(
            "SELECT id,source_id,fetched_at,http_status,byte_length,content_hash,prev_hash,"
            "raw_path,adapter_version,outcome,detail,duration_s FROM snapshots " + sql,
            params,
        ).fetchall()
        return [Snapshot(*r) for r in rows]

    def last_snapshot(self, source: Source) -> Snapshot | None:
        ids = self.source_lineage_ids(source)
        q = ",".join("?" * len(ids))
        rows = self._snap_rows(f"WHERE source_id IN ({q}) ORDER BY id DESC LIMIT 1", ids)
        return rows[0] if rows else None

    def last_ok_snapshot(self, source: Source, before_id: int | None = None) -> Snapshot | None:
        ids = self.source_lineage_ids(source)
        q = ",".join("?" * len(ids))
        extra = " AND id < ?" if before_id is not None else ""
        params: list[int] = list(ids) + ([before_id] if before_id is not None else [])
        rows = self._snap_rows(f"WHERE source_id IN ({q}) AND outcome='ok'{extra} ORDER BY id DESC LIMIT 1", params)
        return rows[0] if rows else None

    def snapshots_for(self, source: Source) -> list[Snapshot]:
        ids = self.source_lineage_ids(source)
        q = ",".join("?" * len(ids))
        return self._snap_rows(f"WHERE source_id IN ({q}) ORDER BY id", ids)

    def all_snapshots(self) -> list[Snapshot]:
        return self._snap_rows("ORDER BY id", ())

    def recent_outcomes(self, source: Source, n: int) -> list[str]:
        ids = self.source_lineage_ids(source)
        q = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT outcome FROM snapshots WHERE source_id IN ({q}) ORDER BY id DESC LIMIT ?",
            list(ids) + [n],
        ).fetchall()
        return [r[0] for r in rows]

    # ---- record index --------------------------------------------------
    def add_records(self, snapshot_id: int, pairs: Iterable[tuple[str, str]]) -> None:
        self.conn.executemany(
            "INSERT INTO record_index (snapshot_id, record_key, value_hash) VALUES (?,?,?)",
            ((snapshot_id, k, v) for k, v in pairs),
        )

    def records(self, snapshot_id: int) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT record_key, value_hash FROM record_index WHERE snapshot_id=?", (snapshot_id,)
        ).fetchall()
        return dict(rows)

    def record_count(self, snapshot_id: int) -> int:
        return int(
            self.conn.execute("SELECT COUNT(*) FROM record_index WHERE snapshot_id=?", (snapshot_id,)).fetchone()[0]
        )

    # ---- diffs / health / events / alerts -------------------------------
    def add_run_diff(
        self,
        source_id: int,
        snapshot_id: int,
        prev_snapshot_id: int | None,
        added: int,
        removed: int,
        mutated: int,
        aged_out: int,
        reappeared: int,
        unchanged: int,
        classification: str,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO run_diffs (source_id,snapshot_id,prev_snapshot_id,added,removed,mutated,"
            "aged_out,reappeared,unchanged,classification) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                source_id,
                snapshot_id,
                prev_snapshot_id,
                added,
                removed,
                mutated,
                aged_out,
                reappeared,
                unchanged,
                classification,
            ),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)

    def run_diffs_for(self, source: Source) -> list[sqlite3.Row]:
        ids = self.source_lineage_ids(source)
        q = ",".join("?" * len(ids))
        self.conn.row_factory = sqlite3.Row
        try:
            return self.conn.execute(f"SELECT * FROM run_diffs WHERE source_id IN ({q}) ORDER BY id", ids).fetchall()
        finally:
            self.conn.row_factory = None

    def add_health(
        self,
        source_id: int,
        snapshot_id: int,
        fingerprint: str,
        record_count: int,
        drifted: bool,
        drift_detail: str | None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO source_health (source_id,snapshot_id,fingerprint,record_count,drifted,"
            "drift_detail) VALUES (?,?,?,?,?,?)",
            (source_id, snapshot_id, fingerprint, record_count, int(drifted), drift_detail),
        )

    def health_for(self, source: Source) -> list[tuple[int, str, int, int]]:
        ids = self.source_lineage_ids(source)
        q = ",".join("?" * len(ids))
        return self.conn.execute(
            f"SELECT snapshot_id, fingerprint, record_count, drifted FROM source_health"
            f" WHERE source_id IN ({q}) ORDER BY id",
            ids,
        ).fetchall()

    def add_key_events(self, source_id: int, snapshot_id: int, event: str, keys: Iterable[str]) -> None:
        self.conn.executemany(
            "INSERT INTO key_events (source_id,snapshot_id,record_key,event) VALUES (?,?,?,?)",
            ((source_id, snapshot_id, k, event) for k in keys),
        )

    def outstanding_removed_keys(self, source: Source) -> set[str]:
        """Keys whose most recent event is 'removed' (not yet reappeared)."""
        ids = self.source_lineage_ids(source)
        q = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT e.record_key, e.event FROM key_events e JOIN ("
            f"  SELECT record_key, MAX(id) AS mid FROM key_events WHERE source_id IN ({q})"
            f"  GROUP BY record_key) m ON e.id = m.mid",
            ids,
        ).fetchall()
        return {k for k, ev in rows if ev == "removed"}

    def keys_in_snapshot(self, snapshot_id: int) -> set[str]:
        rows = self.conn.execute("SELECT record_key FROM record_index WHERE snapshot_id=?", (snapshot_id,)).fetchall()
        return {r[0] for r in rows}

    def removed_keys_ever(self, source: Source) -> set[str]:
        ids = self.source_lineage_ids(source)
        q = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT DISTINCT record_key FROM key_events WHERE source_id IN ({q}) AND event='removed'", ids
        ).fetchall()
        return {r[0] for r in rows}

    # ---- drift acceptances ---------------------------------------------
    def accept_drift(self, source: Source, fingerprint: str, note: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO drift_acceptances (source_id,fingerprint,accepted_at,note) VALUES (?,?,?,?)",
            (source.id, fingerprint, iso(utcnow()), note),
        )

    def accepted_fingerprints(self, source: Source) -> list[str]:
        ids = self.source_lineage_ids(source)
        q = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT fingerprint FROM drift_acceptances WHERE source_id IN ({q}) ORDER BY id", ids
        ).fetchall()
        return [r[0] for r in rows]

    def key_event_counts(self, source: Source) -> dict[str, int]:
        ids = self.source_lineage_ids(source)
        q = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT event, COUNT(*) FROM key_events WHERE source_id IN ({q}) GROUP BY event", ids
        ).fetchall()
        return dict(rows)

    def add_alert(self, kind: str, message: str, source_id: int | None = None, snapshot_id: int | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO alerts (raised_at,source_id,snapshot_id,kind,message) VALUES (?,?,?,?,?)",
            (iso(utcnow()), source_id, snapshot_id, kind, message),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)

    def alerts_since(self, alert_id: int) -> list[tuple[int, str, int | None, str, str]]:
        return self.conn.execute(
            "SELECT id, raised_at, source_id, kind, message FROM alerts WHERE id > ? ORDER BY id",
            (alert_id,),
        ).fetchall()

    def max_alert_id(self) -> int:
        return int(self.conn.execute("SELECT COALESCE(MAX(id),0) FROM alerts").fetchone()[0])
