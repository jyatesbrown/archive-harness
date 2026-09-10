"""XML adapters built on ElementTree.

Subclasses implement `records(root) -> list[(key, value_fields)]`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from harness.adapter import ExtractionError, Fingerprint, value_hash
from harness.adapters.base import HttpAdapter


def parse_xml(raw: bytes) -> ET.Element:
    try:
        return ET.fromstring(raw)
    except ET.ParseError as e:
        raise ExtractionError(f"invalid XML: {e}") from e


class XmlAdapter(HttpAdapter):
    version = "0.1"
    accept = "application/xml, text/xml, */*"
    selectors: tuple[str, ...] = ()

    def records(self, root: ET.Element) -> list[tuple[str, list[tuple[str, str]]]]:
        raise NotImplementedError

    def extract(self, raw: bytes) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for key, vals in self.records(parse_xml(raw)):
            if key in seen:
                raise ExtractionError(f"duplicate record_key {key!r}")
            seen.add(key)
            out.append((key, value_hash(vals)))
        return out

    def fingerprint(self, raw: bytes) -> Fingerprint:
        root = parse_xml(raw)
        recs = self.records(root)
        fields: set[str] = set()
        for _, vals in recs:
            fields.update(k for k, _ in vals)
        return Fingerprint(
            record_count=len(recs),
            field_names=tuple(sorted(fields)),
            selectors=(f"root:{root.tag}", *self.selectors),
        )
