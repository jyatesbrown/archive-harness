from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from harness.adapter import Adapter, FetchResult
from harness.db import Database, Source
from harness.fetch import FetchError, HttpClient
from harness.runner import Runner
from harness.storage import FilesystemPayloadStore

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class Clock:
    """Deterministic clock; each call to advance() moves one day."""

    def __init__(self, start: datetime = datetime(2026, 9, 1, 6, 0, tzinfo=timezone.utc)):
        self.t = start

    def now(self) -> datetime:
        return self.t

    def advance(self, days: float = 1.0) -> None:
        self.t += timedelta(days=days)


class ScriptedFetch:
    """Wraps a real adapter but replaces fetch() with a queue of canned bodies/errors."""

    def __init__(self, inner: Adapter):
        self.inner = inner
        self.queue: list[bytes | Exception] = []
        self.version = inner.version

    def push(self, item: bytes | Exception) -> None:
        self.queue.append(item)

    def fetch(self) -> FetchResult:
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return FetchResult(body=item, http_status=200, url="fixture://", content_type="")

    def extract(self, raw: bytes) -> list[tuple[str, str]]:
        return self.inner.extract(raw)

    def fingerprint(self, raw: bytes):  # type: ignore[no-untyped-def]
        return self.inner.fingerprint(raw)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = Database(tmp_path / "h.sqlite")
    yield d
    d.close()


@pytest.fixture
def store(tmp_path: Path) -> FilesystemPayloadStore:
    return FilesystemPayloadStore(tmp_path / "payloads")


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def harness(db: Database, store: FilesystemPayloadStore, clock: Clock):  # type: ignore[no-untyped-def]
    """Returns (runner, scripted-adapters-by-source-name, db, store, clock)."""
    from harness.adapters.generic_csv import GenericCsvAdapter
    from harness.adapters.generic_json import GenericJsonAdapter

    scripted: dict[str, ScriptedFetch] = {}

    def factory(source: Source, client: HttpClient, now: datetime) -> Adapter:
        if source.name not in scripted:
            cfg = json.loads(source.adapter_config)
            cls: Callable[..., Adapter] = GenericJsonAdapter if source.format == "json" else GenericCsvAdapter
            scripted[source.name] = ScriptedFetch(cls(url=source.endpoint, client=client, **cfg))
        return scripted[source.name]

    runner = Runner(db, store, client=HttpClient(sleep=lambda s: None), adapter_factory=factory, now=clock.now)
    return runner, scripted, db, store, clock


def add_json_source(db: Database, name: str = "fixture_json", **kw: object) -> Source:
    db.add_source(
        name=name,
        tier="fixture",
        endpoint="fixture://records.json",
        format="json",
        adapter_module="harness.adapters.generic_json:GenericJsonAdapter",
        adapter_config=json.dumps({"key_fields": ["id"], "records_path": "data.items"}),
        identity_key="id",
        license_url="fixture://none",
        **kw,  # type: ignore[arg-type]
    )
    src = db.source_by_name(name)
    assert src is not None
    return src


def add_csv_source(db: Database, name: str = "fixture_csv", **kw: object) -> Source:
    db.add_source(
        name=name,
        tier="fixture",
        endpoint="fixture://table.csv",
        format="csv",
        adapter_module="harness.adapters.generic_csv:GenericCsvAdapter",
        adapter_config=json.dumps({"key_fields": ["date", "series"]}),
        identity_key="date|series",
        license_url="fixture://none",
        **kw,  # type: ignore[arg-type]
    )
    src = db.source_by_name(name)
    assert src is not None
    return src


__all__ = ["FetchError", "fixture_bytes", "add_json_source", "add_csv_source", "ScriptedFetch", "Clock"]
