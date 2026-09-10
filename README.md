# archive-harness

Source-agnostic overwrite detector (Work Order 002, Task 2a). Fetches a set of
public data endpoints on a schedule, keeps every raw payload forever, indexes
only `(record_key, value_hash)` pairs, and reports per source whether records
are added, mutated, or removed between runs — distinguishing permanent removal,
transient disappearance, and rolling-window age-out.

The harness contains no knowledge of what any record means.

## Layout

```
harness/
  adapter.py            Adapter protocol, FetchResult, Fingerprint, value_hash
  adapters/
    generic_json.py     config-driven JSON adapter (list of objects at a dotted path)
    generic_csv.py      config-driven CSV adapter (header row + key columns)
  fetch.py              HttpClient: robots.txt (Amendment A §1.1), crawl-delay,
                        one request per host, 3 attempts w/ exponential backoff,
                        401/403 = endpoint refusal (not retried)
  storage.py            PayloadStore interface + FilesystemPayloadStore
  db.py                 SQLite schema; UPDATE/DELETE forbidden by trigger on every table
  diff.py               added/removed/mutated/aged_out/reappeared + classification
  runner.py             per-source pipeline with isolation, validation, drift, alerts
  report.py             run summary, integrity check, findings ranking
  cli.py                python -m harness {init,run,verify,findings,sources}
deploy/                 systemd timer/service and cron example (daily 06:00 UTC)
tests/fixtures/         two throwaway fixtures (JSON, CSV) + variants used by tests
```

## Storage model

All tables are append-only (enforced by `BEFORE UPDATE` / `BEFORE DELETE` triggers).

| table | one row per | notes |
|---|---|---|
| `sources` | source config version | a changed config is a *new* row; the latest row per name wins |
| `snapshots` | fetch attempt | http status, byte length, `content_hash`, `prev_hash`, raw path, adapter version, outcome |
| `record_index` | (snapshot, record) | `record_key`, `value_hash` only — never values |
| `run_diffs` | successful run | added / removed / mutated / aged_out / reappeared / unchanged, classification |
| `source_health` | run | fingerprint text and drift flag |
| `key_events` | key state change | `removed`, `reappeared`, `aged_out` — powers permanent-vs-transient reporting |
| `alerts` | alert | every non-ok outcome, drift, validation failure, consecutive failures |

Outcomes: `ok`, `fetch_failed`, `robots_disallowed`, `extract_failed`, `validation_failed`.
Raw payloads are written for every attempt that produced a body, including
failures, under `<payloads>/<source>/<YYYY>/<MM>/<utc-ts>-<sha12>.raw`, via the
`PayloadStore` interface (swap in another backend without touching the runner).

## Diff semantics

Compared against the prior **successful** snapshot of the same source:

* `added` — key now, not before. If the key's most recent event was `removed`,
  it is also counted as `reappeared` (transient disappearance).
* `removed` — key before, not now, and *not* aged out.
* `mutated` — key in both, `value_hash` differs.
* `aged_out` — for sources with `window_days`, keys whose creation date
  (taken from key component `window_key_part`, ISO `YYYY-MM-DD` prefix) is
  older than `now - window_days`. Excluded from `removed`.

Classification: `destructive` if any `removed`; else `mutating` if any
`mutated`; else `append_only`. First successful run is `baseline`.

Validation (raw retained, snapshot written, nothing indexed, alert emitted):
zero records after a source previously returned records; structural drift
(field-name set or extractor selectors changed); record count deviating more
than 40% from the prior successful snapshot.

## Running

```bash
pip install -e '.[dev]'
python -m harness --db data/harness.sqlite --payloads data/payloads init --sources sources.json
python -m harness --db data/harness.sqlite --payloads data/payloads run
python -m harness --db data/harness.sqlite --payloads data/payloads verify     # prev_hash chain + payload hashes
python -m harness --db data/harness.sqlite findings
```

Scheduling: `deploy/archive-harness.timer` (systemd, daily 06:00 UTC,
`Persistent=true`) or `deploy/crontab.example`. Sources run sequentially; one
request per host at a time; 30 s default timeout.

## Adding a twenty-first source without modifying core code

1. Append an entry to `sources.json`:

   ```json
   {
     "name": "agency_dataset",
     "tier": "A",
     "endpoint": "https://example.gov/data.json",
     "format": "json",
     "adapter_module": "harness.adapters.generic_json:GenericJsonAdapter",
     "adapter_config": {"key_fields": ["id"], "records_path": "data.items", "ignore_fields": ["retrieved_at"]},
     "identity_key": "id",
     "license_url": "https://example.gov/license",
     "window_days": null,
     "window_key_part": null,
     "control_group": null
   }
   ```

   `adapter_config` is passed as keyword arguments to the adapter constructor,
   along with `url` and the shared `HttpClient`. For a rolling-window source set
   `window_days` and `window_key_part` (0-based index into the `|`-joined key of
   the component holding the creation date).

2. If neither generic adapter fits (PDF, HTML table, non-list JSON), add a
   module anywhere on the import path exposing a class with `version`,
   `fetch()`, `extract(raw) -> list[(key, value_hash)]`, and
   `fingerprint(raw) -> Fingerprint`, and point `adapter_module` at it as
   `package.module:ClassName`. Reuse `harness.adapter.value_hash` and
   `compose_key`. Ship a captured fixture under `tests/fixtures/` and a test
   asserting the expected extraction.

3. `python -m harness init --sources sources.json` — a new `sources` row is
   appended; nothing is edited.

## Tests

```bash
pytest
ruff check harness tests
mypy
```
