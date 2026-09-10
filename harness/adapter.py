"""Adapter interface.

Every source is a module exposing a class that implements `Adapter`. The core
never inspects record contents; it only sees what `extract` and `fingerprint`
return.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

KEY_SEP = "|"


@dataclass(frozen=True)
class FetchResult:
    body: bytes
    http_status: int
    url: str
    content_type: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    duration_s: float = 0.0


@dataclass(frozen=True)
class Fingerprint:
    """Structural signature of a payload. Compared field-for-field across runs."""

    record_count: int
    field_names: tuple[str, ...]
    selectors: tuple[str, ...]

    def structure(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """The part of the fingerprint that constitutes drift when it changes.

        Record count is reported but is validated separately (40% rule), so it
        is excluded from the drift comparison.
        """
        return (self.field_names, self.selectors)

    def as_text(self) -> str:
        return json.dumps(
            {"count": self.record_count, "fields": list(self.field_names), "selectors": list(self.selectors)},
            separators=(",", ":"),
        )

    @classmethod
    def from_text(cls, text: str) -> Fingerprint:
        d = json.loads(text)
        return cls(int(d["count"]), tuple(d["fields"]), tuple(d["selectors"]))


class ExtractionError(Exception):
    """Raised by adapters when the payload cannot be interpreted structurally."""


@runtime_checkable
class Adapter(Protocol):
    version: str

    def fetch(self) -> FetchResult: ...

    def extract(self, raw: bytes) -> list[tuple[str, str]]:
        """Return (record_key, value_hash) pairs and nothing else."""
        ...

    def fingerprint(self, raw: bytes) -> Fingerprint: ...


def value_hash(values: Iterable[object]) -> str:
    """Hash the non-key field values of a record, in a stable order."""
    h = hashlib.sha256()
    for v in values:
        h.update(repr(v).encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def compose_key(parts: Sequence[object]) -> str:
    return KEY_SEP.join(str(p) for p in parts)


def load_adapter(module_path: str, **kwargs: object) -> Adapter:
    """Import `package.module:ClassName` and instantiate it with kwargs."""
    mod_name, _, cls_name = module_path.partition(":")
    if not cls_name:
        raise ValueError(f"adapter module must be 'module:Class', got {module_path!r}")
    mod = importlib.import_module(mod_name)
    cls = getattr(mod, cls_name)
    inst = cls(**kwargs)
    if not isinstance(inst, Adapter):
        raise TypeError(f"{module_path} does not implement Adapter")
    return inst
