import pytest

from harness.adapter import Adapter, ExtractionError, load_adapter
from harness.adapters.generic_csv import GenericCsvAdapter
from harness.adapters.generic_json import GenericJsonAdapter
from tests.conftest import fixture_bytes


def test_json_extract_and_fingerprint():
    a = GenericJsonAdapter(url="fixture://", key_fields=["id"], records_path="data.items")
    raw = fixture_bytes("records_v1.json")
    pairs = a.extract(raw)
    assert [k for k, _ in pairs] == ["A-001", "A-002", "A-003", "A-004", "A-005"]
    assert all(len(v) == 64 for _, v in pairs)
    # A-003 and A-004 share color=green but differ in qty -> different hashes
    d = dict(pairs)
    assert d["A-003"] != d["A-004"]
    fp = a.fingerprint(raw)
    assert fp.record_count == 5
    assert fp.field_names == ("color", "created", "id", "qty")
    assert fp.selectors == ("json:data.items", "key:id")


def test_json_value_hash_ignores_key_and_ignored_fields():
    a = GenericJsonAdapter(url="fixture://", key_fields=["id"], records_path="data.items", ignore_fields=["created"])
    v1 = dict(a.extract(fixture_bytes("records_v1.json")))
    v2 = dict(a.extract(fixture_bytes("records_v2.json")))
    assert v1["A-001"] == v2["A-001"]  # unchanged record hashes identically across payloads
    assert v1["A-004"] != v2["A-004"]  # color changed


def test_json_errors():
    a = GenericJsonAdapter(url="fixture://", key_fields=["id"], records_path="data.items")
    with pytest.raises(ExtractionError):
        a.extract(b"not json")
    with pytest.raises(ExtractionError):
        a.extract(b'{"data": {"items": {"not": "a list"}}}')
    with pytest.raises(ExtractionError, match="missing key fields"):
        a.extract(b'{"data": {"items": [{"nope": 1}]}}')
    with pytest.raises(ExtractionError, match="duplicate"):
        a.extract(b'{"data": {"items": [{"id": 1}, {"id": 1}]}}')
    with pytest.raises(ExtractionError, match="not found"):
        GenericJsonAdapter(url="fixture://", key_fields=["id"], records_path="data.rows").extract(
            fixture_bytes("records_v1.json")
        )


def test_csv_extract_and_fingerprint():
    a = GenericCsvAdapter(url="fixture://", key_fields=["date", "series"])
    raw = fixture_bytes("table_v1.csv")
    pairs = a.extract(raw)
    assert len(pairs) == 10
    assert pairs[0][0] == "2026-08-25|X"
    fp = a.fingerprint(raw)
    assert fp.record_count == 10
    assert fp.field_names == ("date", "series", "value", "revised")


def test_csv_errors():
    a = GenericCsvAdapter(url="fixture://", key_fields=["date", "series"])
    with pytest.raises(ExtractionError, match="missing key fields"):
        a.extract(b"a,b\n1,2\n")
    with pytest.raises(ExtractionError, match="duplicate"):
        a.extract(b"date,series,value\n1,X,1\n1,X,2\n")
    with pytest.raises(ExtractionError, match="column count"):
        a.extract(b"date,series,value\n1,X\n")


def test_load_adapter_by_module_path():
    a = load_adapter("harness.adapters.generic_csv:GenericCsvAdapter", url="fixture://", key_fields=["date"])
    assert isinstance(a, Adapter)
    with pytest.raises(ValueError):
        load_adapter("harness.adapters.generic_csv", url="x")
