"""Run orchestration: fetch -> snapshot -> extract -> validate -> diff -> health.

Per-source isolation: any exception in one source is recorded and the run
continues with the next source. Silent failure is the failure mode this module
exists to prevent: every non-ok outcome raises an alert row.
"""

from __future__ import annotations

import json
import logging
import re
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from harness.adapter import Adapter, ExtractionError, Fingerprint, load_adapter
from harness.db import Database, Snapshot, Source, utcnow
from harness.diff import DiffResult, Window, diff
from harness.fetch import FetchError, HttpClient, RobotsDisallowed, RobotsUnavailable
from harness.storage import PayloadStore, sha256

log = logging.getLogger("harness")

COUNT_DEVIATION_LIMIT = 0.40
CONSECUTIVE_FAILURE_ALERT = 2

AdapterFactory = Callable[[Source, HttpClient, datetime], Adapter]

_TEMPLATE = re.compile(r"\{(today|yesterday|prev_business_day)(?:([+-])(\d+)d)?(?::([^}]+))?\}")


def prev_business_day(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def resolve_endpoint(template: str, now: datetime) -> str:
    """Expand date tokens: {today}, {yesterday}, {prev_business_day}, each with an
    optional day offset and strftime format, e.g. {today-30d:%Y-%m-%d}.
    Default format is ISO date."""
    base = {
        "today": now.date(),
        "yesterday": now.date() - timedelta(days=1),
        "prev_business_day": prev_business_day(now.date()),
    }

    def sub(m: re.Match[str]) -> str:
        d = base[m.group(1)]
        if m.group(2):
            off = int(m.group(3))
            d = d + timedelta(days=off) if m.group(2) == "+" else d - timedelta(days=off)
        return d.strftime(m.group(4) or "%Y-%m-%d")

    return _TEMPLATE.sub(sub, template)


def resolve_config(value: Any, now: datetime) -> Any:
    """Apply resolve_endpoint() to every string inside an adapter_config value."""
    if isinstance(value, str):
        return resolve_endpoint(value, now)
    if isinstance(value, dict):
        return {k: resolve_config(v, now) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_config(v, now) for v in value]
    return value


def default_adapter_factory(source: Source, client: HttpClient, now: datetime) -> Adapter:
    cfg = resolve_config(json.loads(source.adapter_config or "{}"), now)
    if source.timeout_s is not None:
        cfg.setdefault("timeout_s", source.timeout_s)
    return load_adapter(source.adapter_module, url=resolve_endpoint(source.endpoint, now), client=client, **cfg)


@dataclass
class SourceResult:
    source: Source
    snapshot_id: int | None
    outcome: str
    detail: str = ""
    record_count: int | None = None
    drifted: bool = False
    diff: DiffResult | None = None
    exited: int = 0
    duration_s: float = 0.0
    alerts: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    started_at: datetime
    finished_at: datetime
    results: list[SourceResult]


class ValidationFailed(Exception):
    pass


def validate_count(prev_count: int | None, curr_count: int) -> None:
    if prev_count is None:
        return
    if prev_count > 0 and curr_count == 0:
        raise ValidationFailed(f"zero records extracted; previous successful snapshot had {prev_count}")
    if prev_count > 0:
        dev = abs(curr_count - prev_count) / prev_count
        if dev > COUNT_DEVIATION_LIMIT:
            raise ValidationFailed(
                f"record count {curr_count} deviates {dev:.0%} from previous {prev_count}"
                f" (limit {COUNT_DEVIATION_LIMIT:.0%})"
            )


def drift_detail(prev: Fingerprint | None, curr: Fingerprint) -> str | None:
    if prev is None or prev.structure() == curr.structure():
        return None
    parts = []
    pf, cf = set(prev.field_names), set(curr.field_names)
    if pf != cf:
        parts.append(f"fields -{sorted(pf - cf)} +{sorted(cf - pf)}")
    if prev.selectors != curr.selectors:
        parts.append(f"selectors {prev.selectors} -> {curr.selectors}")
    return "; ".join(parts) or "field order changed"


def parse_fingerprint(text: str) -> Fingerprint:
    return Fingerprint.from_text(text)


class Runner:
    def __init__(
        self,
        db: Database,
        store: PayloadStore,
        client: HttpClient | None = None,
        adapter_factory: AdapterFactory = default_adapter_factory,
        now: Callable[[], datetime] = utcnow,
    ):
        self.db = db
        self.store = store
        self.client = client or HttpClient()
        self.adapter_factory = adapter_factory
        self.now = now

    def run(self, sources: list[Source] | None = None) -> RunResult:
        started = self.now()
        results: list[SourceResult] = []
        for source in sources if sources is not None else self.db.sources():
            t0 = time.monotonic()
            try:
                res = self.run_source(source)
            except Exception as e:  # isolation: never let one source halt the run
                log.exception("unhandled error in source %s", source.name)
                msg = f"unhandled {type(e).__name__}: {e}"
                snap_id = None
                try:
                    snap_id = self.db.add_snapshot(
                        source.id,
                        self.now(),
                        None,
                        None,
                        None,
                        None,
                        None,
                        "unknown",
                        "fetch_failed",
                        detail=msg + "\n" + traceback.format_exc(),
                    )
                except Exception:
                    log.exception("could not record failure snapshot for %s", source.name)
                res = SourceResult(source, snap_id, "fetch_failed", msg)
                self._alert(res, "unhandled_error", msg)
            res.duration_s = time.monotonic() - t0
            self._check_consecutive(res)
            results.append(res)
        return RunResult(started, self.now(), results)

    # ------------------------------------------------------------------
    def _alert(self, res: SourceResult, kind: str, message: str) -> None:
        text = f"[{res.source.name}] {kind}: {message}"
        res.alerts.append(text)
        self.db.add_alert(kind, message, res.source.id, res.snapshot_id)
        log.warning(text)

    def _check_consecutive(self, res: SourceResult) -> None:
        recent = self.db.recent_outcomes(res.source, CONSECUTIVE_FAILURE_ALERT)
        if len(recent) == CONSECUTIVE_FAILURE_ALERT and all(o != "ok" for o in recent):
            self._alert(res, "consecutive_failures", f"{CONSECUTIVE_FAILURE_ALERT} consecutive non-ok runs: {recent}")

    def run_source(self, source: Source) -> SourceResult:
        fetched_at = self.now()
        adapter = self.adapter_factory(source, self.client, fetched_at)
        version = getattr(adapter, "version", "unknown")
        prev_any: Snapshot | None = self.db.last_snapshot(source)
        prev_hash = prev_any.content_hash if prev_any else None

        # ---- fetch -----------------------------------------------------
        try:
            fr = adapter.fetch()
        except RobotsDisallowed as e:
            sid = self.db.add_snapshot(
                source.id, fetched_at, None, None, None, prev_hash, None, version, "robots_disallowed", str(e)
            )
            res = SourceResult(source, sid, "robots_disallowed", str(e))
            self._alert(res, "robots_disallowed", str(e))
            return res
        except RobotsUnavailable as e:
            sid = self.db.add_snapshot(
                source.id, fetched_at, None, None, None, prev_hash, None, version, "fetch_failed", str(e)
            )
            res = SourceResult(source, sid, "fetch_failed", str(e))
            self._alert(res, "robots_unavailable", str(e))
            return res
        except FetchError as e:
            raw_path = None
            chash = None
            if e.body:
                raw_path = self.store.write(source.name, fetched_at, e.body)
                chash = sha256(e.body)
            sid = self.db.add_snapshot(
                source.id,
                fetched_at,
                e.http_status,
                len(e.body),
                chash,
                prev_hash,
                raw_path,
                version,
                "fetch_failed",
                str(e),
            )
            res = SourceResult(source, sid, "fetch_failed", str(e))
            kind = "endpoint_refused" if e.http_status in (401, 403) else "fetch_failed"
            self._alert(res, kind, str(e))
            return res

        raw = fr.body
        chash = sha256(raw)
        raw_path = self.store.write(source.name, fetched_at, raw)

        # ---- extract ---------------------------------------------------
        try:
            fp = adapter.fingerprint(raw)
            pairs = adapter.extract(raw)
        except ExtractionError as e:
            sid = self.db.add_snapshot(
                source.id,
                fetched_at,
                fr.http_status,
                len(raw),
                chash,
                prev_hash,
                raw_path,
                version,
                "extract_failed",
                str(e),
                fr.duration_s,
            )
            res = SourceResult(source, sid, "extract_failed", str(e))
            self._alert(res, "extract_failed", str(e))
            return res

        # ---- drift + validation -----------------------------------------
        prev_ok = self.db.last_ok_snapshot(source)
        prev_fp = None
        prev_count = None
        if prev_ok is not None:
            prev_count = self.db.record_count(prev_ok.id)
            hist = [h for h in self.db.health_for(source) if h[0] == prev_ok.id]
            if hist:
                prev_fp = parse_fingerprint(hist[-1][1])
        ddetail = drift_detail(prev_fp, fp)
        drifted = ddetail is not None
        accepted = drifted and any(
            parse_fingerprint(a).structure() == fp.structure() for a in self.db.accepted_fingerprints(source)
        )

        try:
            if prev_count and not pairs:
                validate_count(prev_count, 0)
            if drifted and not accepted:
                raise ValidationFailed(f"structural drift: {ddetail}")
            validate_count(prev_count, len(pairs))
        except ValidationFailed as e:
            sid = self.db.add_snapshot(
                source.id,
                fetched_at,
                fr.http_status,
                len(raw),
                chash,
                prev_hash,
                raw_path,
                version,
                "validation_failed",
                str(e),
                fr.duration_s,
            )
            self.db.add_health(source.id, sid, fp.as_text(), fp.record_count, drifted, ddetail)
            res = SourceResult(source, sid, "validation_failed", str(e), len(pairs), drifted)
            self._alert(res, "drift" if drifted else "validation_failed", str(e))
            return res

        # ---- ok: index, diff, health -----------------------------------
        sid = self.db.add_snapshot(
            source.id,
            fetched_at,
            fr.http_status,
            len(raw),
            chash,
            prev_hash,
            raw_path,
            version,
            "ok",
            None,
            fr.duration_s,
        )
        self.db.add_records(sid, pairs)
        self.db.add_health(source.id, sid, fp.as_text(), fp.record_count, drifted, ddetail)
        res = SourceResult(source, sid, "ok", "", len(pairs), drifted)
        if drifted:
            self._alert(res, "drift_accepted", f"structural drift matched an accepted fingerprint: {ddetail}")

        window = None
        if source.window_days is not None and source.window_key_part is not None:
            window = Window(source.window_days, source.window_key_part)
        prev_records = self.db.records(prev_ok.id) if prev_ok else None
        d = diff(
            prev_records,
            dict(pairs),
            as_of=fetched_at,
            window=window,
            outstanding_removed=self.db.outstanding_removed_keys(source),
        )
        self.db.add_run_diff(
            source.id,
            sid,
            prev_ok.id if prev_ok else None,
            len(d.added),
            len(d.removed),
            len(d.mutated),
            len(d.aged_out),
            len(d.reappeared),
            d.unchanged,
            d.classification,
        )
        self.db.add_key_events(source.id, sid, "removed", sorted(d.removed))
        self.db.add_key_events(source.id, sid, "reappeared", sorted(d.reappeared))
        self.db.add_key_events(source.id, sid, "aged_out", sorted(d.aged_out))
        res.diff = d
        if source.exit_target:
            res.exited = self._check_exit_condition(source, sid, d)
        return res

    def _check_exit_condition(self, source: Source, sid: int, d: DiffResult) -> int:
        """Keys removed from this source are checked against the exit_target's
        latest successful key space; matches are recorded as 'exited' rather
        than left outstanding as candidate destruction."""
        target = self.db.source_by_name(source.exit_target or "")
        if target is None:
            self._alert(
                SourceResult(source, sid, "ok"), "exit_target_missing", f"exit_target {source.exit_target!r} not found"
            )
            return 0
        target_ok = self.db.last_ok_snapshot(target)
        if target_ok is None:
            return 0
        target_keys = self.db.keys_in_snapshot(target_ok.id)
        candidates = d.removed | self.db.outstanding_removed_keys(source)
        exited = sorted(k for k in candidates if k in target_keys)
        self.db.add_key_events(source.id, sid, "exited", exited)
        return len(exited)
