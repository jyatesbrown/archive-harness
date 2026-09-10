"""Command-line entry point.

python -m harness init      --db DB --sources sources.json   register/refresh sources
python -m harness run       --db DB --payloads DIR [--only NAME]  one scheduled run
python -m harness verify    --db DB --payloads DIR             prev_hash chain + payload check
python -m harness findings  --db DB                            ranking report
python -m harness sources   --db DB
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from harness.db import Database
from harness.report import findings, run_summary, source_table, verify_integrity
from harness.runner import Runner
from harness.storage import FilesystemPayloadStore

SOURCE_FIELDS = {
    "name",
    "tier",
    "endpoint",
    "format",
    "adapter_module",
    "identity_key",
    "license_url",
    "adapter_config",
    "window_days",
    "window_key_part",
    "control_group",
    "active",
}


def load_sources(db: Database, path: Path) -> list[str]:
    """Insert a row for every configured source whose latest row differs.

    Append-only: a changed or deactivated source is a new row, never an edit.
    """
    changed = []
    for entry in json.loads(path.read_text()):
        unknown = set(entry) - SOURCE_FIELDS
        if unknown:
            raise SystemExit(f"source {entry.get('name')!r}: unknown fields {sorted(unknown)}")
        entry = dict(entry)
        entry["adapter_config"] = json.dumps(entry.get("adapter_config", {}), sort_keys=True)
        entry.setdefault("active", True)
        cur = db.source_by_name(entry["name"])
        if cur is not None and all(getattr(cur, k) == v for k, v in entry.items()):
            continue
        db.add_source(**entry)
        changed.append(entry["name"])
    return changed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="harness")
    p.add_argument("--db", default="data/harness.sqlite")
    p.add_argument("--payloads", default="data/payloads")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    s_init = sub.add_parser("init")
    s_init.add_argument("--sources", default="sources.json")
    s_run = sub.add_parser("run")
    s_run.add_argument("--only", action="append", default=[])
    s_run.add_argument("--summary-file", default=None)
    sub.add_parser("verify")
    sub.add_parser("findings")
    sub.add_parser("sources")
    a = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    Path(a.db).parent.mkdir(parents=True, exist_ok=True)
    db = Database(a.db)
    store = FilesystemPayloadStore(a.payloads)

    if a.cmd == "init":
        changed = load_sources(db, Path(a.sources))
        print(f"registered/updated {len(changed)} source(s): {', '.join(changed) or '-'}")
        return 0
    if a.cmd == "sources":
        print(source_table(db.sources(active_only=False)))
        return 0
    if a.cmd == "verify":
        rep = verify_integrity(db, store)
        print(rep.as_text())
        return 0 if rep.ok else 1
    if a.cmd == "findings":
        print(findings(db))
        return 0
    if a.cmd == "run":
        sources = db.sources()
        if a.only:
            sources = [s for s in sources if s.name in set(a.only)]
        try:
            run = Runner(db, store).run(sources)
        except Exception as e:
            db.add_alert("run_incomplete", f"run aborted: {type(e).__name__}: {e}")
            print(f"ALERT run_incomplete: {e}", file=sys.stderr)
            raise
        text = run_summary(run)
        print(text)
        if a.summary_file:
            Path(a.summary_file).parent.mkdir(parents=True, exist_ok=True)
            Path(a.summary_file).write_text(text + "\n")
        return 0 if all(r.outcome == "ok" for r in run.results) else 2
    return 1
