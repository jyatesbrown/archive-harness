"""#8 CDC NNDSS Weekly Data — Socrata JSON. Key: states|year|week|label."""

from __future__ import annotations

from harness.adapters.generic_json import GenericJsonAdapter
from harness.fetch import HttpClient


class CdcNndssJson(GenericJsonAdapter):
    version = "1.0"

    def __init__(self, url: str, client: HttpClient | None = None, timeout_s: float | None = None):
        super().__init__(url, key_fields=("states", "year", "week", "label"), client=client, timeout_s=timeout_s)
