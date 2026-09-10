"""Raw payload storage interface. Payloads live outside git and are never deleted."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class PayloadStore(ABC):
    @abstractmethod
    def write(self, source_name: str, fetched_at: datetime, data: bytes) -> str:
        """Persist `data`; return an opaque locator recorded in `snapshots.raw_path`."""

    @abstractmethod
    def read(self, locator: str) -> bytes: ...

    @abstractmethod
    def exists(self, locator: str) -> bool: ...


class FilesystemPayloadStore(PayloadStore):
    """<root>/<source>/<YYYY>/<MM>/<timestamp>-<sha256[:12]>.raw"""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def write(self, source_name: str, fetched_at: datetime, data: bytes) -> str:
        digest = sha256(data)
        rel = Path(source_name) / fetched_at.strftime("%Y") / fetched_at.strftime("%m")
        name = f"{fetched_at.strftime('%Y%m%dT%H%M%S%fZ')}-{digest[:12]}.raw"
        target = self.root / rel / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(f"refusing to overwrite payload {target}")
        tmp = target.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.rename(target)
        return str(rel / name)

    def read(self, locator: str) -> bytes:
        return (self.root / locator).read_bytes()

    def exists(self, locator: str) -> bool:
        return (self.root / locator).is_file()
