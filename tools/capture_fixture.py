"""Capture a live fixture through a registered source's adapter.

    python -m tools.capture_fixture sources.json NAME OUT_PATH [ENDPOINT_OVERRIDE]

ENDPOINT_OVERRIDE replaces the configured endpoint template (used to capture a
bounded fixture, e.g. a smaller $limit, of a source whose full payload is too
large to commit). Extraction and fingerprint always run through the real adapter.

Uses the harness HttpClient (declared UA, robots, per-host serialization) and
the adapter's own fetch() so paged sources are captured exactly as at run time.
Prints status, byte length, wall time, record count and fingerprint.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from harness.cli import load_source_entries
from harness.db import Source
from harness.fetch import HttpClient
from harness.runner import default_adapter_factory


def main(argv: list[str]) -> int:
    cfg, name, out = argv[1], argv[2], Path(argv[3])
    entries = [e for e in load_source_entries(Path(cfg)) if e["name"] == name]
    if not entries:
        print(f"no source named {name!r}", file=sys.stderr)
        return 2
    e = entries[0]
    if len(argv) > 4:
        e["endpoint"] = argv[4]
    src = Source(id=0, **e)
    adapter = default_adapter_factory(src, HttpClient(), datetime.now(timezone.utc))
    res = adapter.fetch()
    if res.http_status != 200:
        print(f"HTTP {res.http_status} from {res.url}", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(res.body)
    pairs = adapter.extract(res.body)
    fp = adapter.fingerprint(res.body)
    print(
        json.dumps(
            {
                "name": name,
                "url": res.url,
                "status": res.http_status,
                "bytes": len(res.body),
                "duration_s": round(res.duration_s, 2),
                "pages": res.headers.get("x-harness-pages", "1"),
                "records": len(pairs),
                "fields": list(fp.field_names),
                "selectors": list(fp.selectors),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
