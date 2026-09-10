"""Plain-text reporting: per-run summary, integrity check, and findings ranking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from harness.db import Database, Snapshot, Source
from harness.runner import RunResult
from harness.storage import PayloadStore, sha256


def run_summary(run: RunResult) -> str:
    lines = [
        f"archive-harness run {run.started_at:%Y-%m-%d %H:%M:%S}Z -> {run.finished_at:%H:%M:%S}Z",
        f"{'source':<32} {'outcome':<18} {'records':>8} {'+add':>6} {'-rem':>6} {'~mut':>6}"
        f" {'aged':>5} {'reapp':>5} {'drift':<6} {'secs':>6}  class",
    ]
    alerts: list[str] = []
    for r in run.results:
        d = r.diff
        c = d.counts() if d else {}
        lines.append(
            f"{r.source.name:<32} {r.outcome:<18} {r.record_count if r.record_count is not None else '-':>8}"
            f" {c.get('added', '-'):>6} {c.get('removed', '-'):>6} {c.get('mutated', '-'):>6}"
            f" {c.get('aged_out', '-'):>5} {c.get('reappeared', '-'):>5} {'yes' if r.drifted else 'no':<6}"
            f" {r.duration_s:>6.1f}  {d.classification if d else ''}"
        )
        if r.detail:
            lines.append(f"    {r.detail.splitlines()[0]}")
        alerts.extend(r.alerts)
    ok = sum(1 for r in run.results if r.outcome == "ok")
    lines.append(f"{ok}/{len(run.results)} sources ok; {len(alerts)} alert(s)")
    if alerts:
        lines.append("ALERTS:")
        lines.extend(f"  ! {a}" for a in alerts)
    return "\n".join(lines)


@dataclass
class IntegrityReport:
    snapshots: int
    chain_breaks: list[str]
    missing_payloads: list[str]
    hash_mismatches: list[str]

    @property
    def ok(self) -> bool:
        return not (self.chain_breaks or self.missing_payloads or self.hash_mismatches)

    def as_text(self) -> str:
        lines = [f"integrity: {self.snapshots} snapshots, {'OK' if self.ok else 'PROBLEMS'}"]
        for name, items in (
            ("prev_hash chain breaks", self.chain_breaks),
            ("missing raw payloads", self.missing_payloads),
            ("content_hash mismatches", self.hash_mismatches),
        ):
            lines.append(f"  {name}: {len(items)}")
            lines.extend(f"    {i}" for i in items[:20])
        return "\n".join(lines)


def verify_integrity(db: Database, store: PayloadStore, verify_bytes: bool = True) -> IntegrityReport:
    """Every snapshot's prev_hash must equal the prior snapshot's content_hash for
    that source, and every snapshot with a content_hash must have a retained
    payload whose SHA-256 matches."""
    breaks: list[str] = []
    missing: list[str] = []
    mism: list[str] = []
    last_hash: dict[str, str | None] = {}
    lineage = dict(db.conn.execute("SELECT id, name FROM sources").fetchall())
    snaps = db.all_snapshots()
    for s in snaps:
        name = lineage.get(s.source_id, str(s.source_id))
        expected = last_hash.get(name)
        if name in last_hash and s.prev_hash != expected:
            breaks.append(f"snapshot {s.id} ({name}): prev_hash={s.prev_hash} expected={expected}")
        last_hash[name] = s.content_hash
        if s.content_hash is not None:
            if s.raw_path is None or not store.exists(s.raw_path):
                missing.append(f"snapshot {s.id} ({name}): {s.raw_path}")
            elif verify_bytes and sha256(store.read(s.raw_path)) != s.content_hash:
                mism.append(f"snapshot {s.id} ({name}): {s.raw_path}")
    return IntegrityReport(len(snaps), breaks, missing, mism)


def findings(db: Database) -> str:
    """Rank sources by how much history they destroy, on the evidence so far."""
    rows = []
    for src in db.sources(active_only=False):
        diffs = db.run_diffs_for(src)
        ok_runs = [d for d in diffs if d["classification"] != "baseline"]
        snaps = db.snapshots_for(src)
        days = _span_days(snaps)
        removed = sum(d["removed"] for d in ok_runs)
        mutated = sum(d["mutated"] for d in ok_runs)
        aged = sum(d["aged_out"] for d in ok_runs)
        compared = sum(d["unchanged"] + d["mutated"] + d["removed"] for d in ok_runs)
        ev = db.key_event_counts(src)
        permanent = len(db.outstanding_removed_keys(src))
        transient = ev.get("reappeared", 0)
        exited = ev.get("exited", 0)
        removed_keys = len(db.removed_keys_ever(src))
        drifts = sum(1 for h in db.health_for(src) if h[3])
        outcomes: dict[str, int] = {}
        for s in snaps:
            outcomes[s.outcome] = outcomes.get(s.outcome, 0) + 1
        mean_removed = removed / days if days else 0.0
        rows.append(
            dict(
                name=src.name,
                tier=src.tier,
                control=src.control_group or "",
                days=days,
                runs=len(snaps),
                ok=outcomes.get("ok", 0),
                removed=removed,
                permanent=permanent,
                transient=transient,
                mean_removed=mean_removed,
                mutation_rate=(mutated / compared) if compared else 0.0,
                aged=aged,
                annual_loss=mean_removed * 365,
                drifts=drifts,
                worst=max((d["classification"] for d in ok_runs), key=_severity, default="n/a"),
                expected_silent=src.expected_silent,
                unverified=src.unverified_contrary_claim,
                exit_target=src.exit_target or "",
                exited=exited,
                removed_keys=removed_keys,
                cadence=src.cadence or "",
            )
        )
    rows.sort(key=lambda r: (-r["permanent"], -r["mean_removed"], -r["mutation_rate"]))
    out = [
        "FINDINGS — sources ranked by permanent removals, then mean removals/day, then mutation rate",
        f"{'#':>2} {'source':<32} {'tier':<4} {'days':>4} {'runs':>4} {'ok':>3} {'rem':>6} {'perm':>6} {'trans':>6}"
        f" {'rem/day':>8} {'mut%':>6} {'aged':>6} {'est/yr':>8} {'drift':>5}  worst",
    ]
    for i, r in enumerate(rows, 1):
        out.append(
            f"{i:>2} {r['name']:<32} {r['tier'][:4]:<4} {r['days']:>4.1f} {r['runs']:>4} {r['ok']:>3} {r['removed']:>6}"
            f" {r['permanent']:>6} {r['transient']:>6} {r['mean_removed']:>8.2f} {r['mutation_rate'] * 100:>6.2f}"
            f" {r['aged']:>6} {r['annual_loss']:>8.0f} {r['drifts']:>5}  {r['worst']}"
            + (f"  [{r['control']}]" if r["control"] else "")
            + ("  [EXPECTED-SILENT]" if r["expected_silent"] else "")
            + ("  [UNVERIFIED-CONTRARY-CLAIM]" if r["unverified"] else "")
        )
    out.append("")
    out.append("perm = removed keys not seen again in this source or its exit target (candidate destruction)")
    out.append("trans = removed keys that later reappeared (instability / partial publication)")
    out.append("aged = keys that left a rolling window by creation date; excluded from rem")
    out.append("est/yr = mean removals per day x 365, assuming no archive existed")
    out.append("drift = runs whose structural fingerprint differed from the prior successful run (adapter fragility)")

    out.append("")
    out.append("EXIT-CONDITION RESULTS (named; not folded into the ranking)")
    exits = [r for r in rows if r["exit_target"]]
    if not exits:
        out.append("  none configured")
    for r in exits:
        rate = (r["exited"] / r["removed_keys"]) if r["removed_keys"] else 0.0
        out.append(
            f"  {r['name']}: {r['removed_keys']} distinct removed keys, {r['exited']} found in"
            f" {r['exit_target']} -> reappearance rate {rate:.1%}; {r['permanent']} unaccounted for"
        )

    out.append("")
    out.append("STABILITY (zero removals and zero mutations over the window)")
    stable = [r for r in rows if r["ok"] >= 2 and r["removed"] == 0 and r["mutation_rate"] == 0.0]
    measured = [r for r in stable if not r["expected_silent"]]
    silent = [r for r in stable if r["expected_silent"]]
    out.append("  measured-stable: " + (", ".join(r["name"] for r in measured) or "none"))
    out.append(
        "  expected-silent (cadence longer than the window; no change is NOT evidence of stability): "
        + (", ".join(f"{r['name']} ({r['cadence'] or 'cadence unstated'})" for r in silent) or "none")
    )
    out.append("")
    out.append("ADAPTER FRAGILITY (structural drifts per source, alongside destruction)")
    for r in sorted(rows, key=lambda r: (-r["drifts"], r["name"])):
        out.append(f"  {r['name']:<32} drifts={r['drifts']:<3} worst={r['worst']:<12} perm={r['permanent']}")
    return "\n".join(out)


def _severity(c: str) -> int:
    return {"baseline": 0, "append_only": 1, "mutating": 2, "destructive": 3}.get(c, 0)


def _span_days(snaps: list[Snapshot]) -> float:
    ok = [s for s in snaps if s.outcome == "ok"]
    if len(ok) < 2:
        return 0.0
    fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
    a = datetime.strptime(ok[0].fetched_at, fmt)
    b = datetime.strptime(ok[-1].fetched_at, fmt)
    return max((b - a).total_seconds() / 86400.0, 1.0)


def source_table(sources: list[Source]) -> str:
    lines = [f"{'id':>3} {'name':<32} {'tier':<4} {'format':<6} {'active':<6} {'control':<16} endpoint"]
    for s in sources:
        lines.append(
            f"{s.id:>3} {s.name:<32} {s.tier:<4} {s.format:<6} {'yes' if s.active else 'no':<6}"
            f" {s.control_group or '':<16} {s.endpoint}"
        )
    return "\n".join(lines)
