"""#14 FAA NAS Status — XML.

Records are the entries of each <Delay_type> list; child elements vary by
program type (Ground_Delay: ARPT/Reason/Avg/Max; Airport closures:
ARPT/Reason/Start/Reopen; Ground_Stop: ARPT/Reason/End_Time; ...).
Key: <Delay_type Name>|<ARPT>[#n] — n disambiguates repeated airports
within one program type (e.g. two closure NOTAMs). <Update_Time> is a
document-level timestamp and is not a record.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from harness.adapter import ExtractionError
from harness.adapters.xml_base import XmlAdapter


class FaaNasStatusXml(XmlAdapter):
    version = "1.0"
    selectors = ("Delay_type/Name", "Delay_type/*_List/*", "key:Name,ARPT")

    def records(self, root: ET.Element) -> list[tuple[str, list[tuple[str, str]]]]:
        if root.tag != "AIRPORT_STATUS_INFORMATION":
            raise ExtractionError(f"unexpected root {root.tag!r}")
        out: list[tuple[str, list[tuple[str, str]]]] = []
        counts: dict[str, int] = {}
        for dt in root.findall("Delay_type"):
            name = (dt.findtext("Name") or "").strip()
            if not name:
                raise ExtractionError("Delay_type without Name")
            for lst in dt:
                if lst.tag == "Name":
                    continue
                for entry in lst:
                    arpt = (entry.findtext("ARPT") or "").strip()
                    if not arpt:
                        raise ExtractionError(f"{lst.tag}/{entry.tag} without ARPT")
                    base = f"{name}|{arpt}"
                    counts[base] = counts.get(base, 0) + 1
                    key = base if counts[base] == 1 else f"{base}#{counts[base]}"
                    vals = [("element", entry.tag)] + [
                        (c.tag, " ".join((c.text or "").split())) for c in entry if c.tag != "ARPT"
                    ]
                    out.append((key, vals))
        return out
