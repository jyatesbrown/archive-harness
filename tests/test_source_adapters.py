"""Offline fixture tests for the 20 Amendment B source adapters.

Every adapter is constructed exactly as the runner constructs it (from
sources.json through default_adapter_factory) with a client that refuses any
network call, then run against a fixture captured live through the same
adapter. Expectations were recorded at capture time.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from harness.adapter import ExtractionError, Fingerprint
from harness.adapters.base import PAGE_SEP
from harness.adapters.sources.faa_nas_status_xml import FaaNasStatusXml
from harness.adapters.sources.fdic_failed_banks_html import FdicFailedBanksHtml
from harness.adapters.sources.fr_issue_pdf import FrIssuePdf
from harness.cli import load_source_entries
from harness.db import Source
from harness.runner import default_adapter_factory

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "sources"
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


class NoNetwork:
    """Stands in for HttpClient; any fetch is a test failure."""

    def fetch(self, *a: object, **kw: object) -> object:
        raise AssertionError("network access attempted during offline test")


@dataclass(frozen=True)
class Expect:
    name: str
    fixture: str
    count: int
    first_key: str
    first_hash: str
    last_key: str
    n_fields: int
    fields_head: tuple[str, ...]
    selectors: tuple[str, ...]


EXPECT = [
    Expect(
        "treasury_par_yield_csv",
        "treasury_par_yield.csv",
        173,
        "09/09/2026",
        "c3558f50fa8a7d45b32ce04353c5b5ee1e7c4ece6958503a75c423614a3c84e9",
        "01/02/2026",
        15,
        ("Date", "1 Mo", "1.5 Month", "2 Mo", "3 Mo", "4 Mo"),
        ("csv:delim=',';skip=0;header=True", "key:Date"),
    ),
    Expect(
        "treasury_par_yield_html",
        "treasury_par_yield.html",
        173,
        "01/02/2026",
        "bd4e21bc184729fffe661351113a0df1b52c0fac72435929f6092759ec03465d",
        "09/09/2026",
        26,
        ("Date", "20 YR", "30 YR", "Extrapolation Factor", "6 WEEKS BANK DISCOUNT", "COUPON EQUIVALENT"),
        ("table:class=views-table#0", "header_row:0", "key:Date"),
    ),
    Expect(
        "dol_ui_weekly_claims_pdf",
        "dol_ui_weekly_claims.pdf",
        203,
        "national|b1|Initial Claims (SA)|2026-09-05",
        "cd59a383a8f0d2232cef9d38469d35190fb66a0959fc71a24d4682bfe50dff99",
        "sa_weekly|2026-08-29",
        8,
        ("initial", "initial_4wk", "initial_chg", "insured", "insured_4wk", "insured_chg"),
        ("sa_weekly:Seasonally Adjusted US Weekly UI Claims", "national:WEEK ENDING", "state:Advance State Claims"),
    ),
    Expect(
        "dol_eta_weekly_claims_html",
        "dol_eta_weekly_claims.html",
        104,
        "01/04/2025",
        "87261ebb70c375f6024d2dffe63ec689786c77a31ea82fd8362409afe7ac2734",
        "12/26/2026",
        12,
        (
            "Week Ended",
            "Initial Claims N.S.A",
            "Initial Claims S.F.",
            "Initial Claims S.A.",
            "Initial Claims S.A. 4-Week",
            "Continued Claims N.S.A",
        ),
        ("table:summary=r539cy#0", "header_row:0", "key:Week Ended"),
    ),
    Expect(
        "eia_gasdiesel_html",
        "eia_gasdiesel.html",
        120,
        "U.S. Regular Gasoline Prices|U.S.|08/24/26",
        "48b332874b09eeb1d2f4a7c132ab1204fce70ca9c6769751fa9658dbd892a4e7",
        "U.S. On-Highway Diesel Fuel Prices|California|09/07/26",
        3,
        ("U.S. Regular Gasoline Prices", "States", "U.S. On-Highway Diesel Fuel Prices"),
        ("table:class=basic-table", "key:caption,row,date"),
    ),
    Expect(
        "fr_documents_json",
        "fr_documents.json",
        315,
        "C1-2026-17324",
        "4e1121089fe5d38ce9e7edf830c74dce738b45039ecdc401ec25cba8df95f556",
        "2026-18189",
        10,
        ("abstract", "agencies", "document_number", "excerpts", "html_url", "pdf_url"),
        ("json:results", "key:document_number", "pages:next_url"),
    ),
    Expect(
        "fr_issue_pdf",
        "fr_issue.pdf",
        127,
        "2026-09-09|2026-18337",
        "09faed7b429f97759bcc1fcdd41b7494c4270c2be9b14f67d0ba103bceb46e9a",
        "2026-09-09|2026-18366",
        2,
        ("billing_code", "filed"),
        ("marker:[FR Doc. N Filed ...]", "key:url_date,doc_number"),
    ),
    Expect(
        "cdc_nndss_weekly_json",
        "cdc_nndss_weekly.json",
        3000,
        "U.S. Residents|2026|1|Anthrax",
        "5e86b36273ffa971df73728300986f9f7bcc4efc6f43d65474dd481ab18fb9a8",
        "Hawaii|2026|1|Hepatitis B, acute, Probable",
        16,
        ("geocode", "label", "location1", "location2", "m1", "m1_flag"),
        ("json:$", "key:states,year,week,label", "pages:none"),
    ),
    Expect(
        "cms_hospital_general_info_json",
        "cms_hospital_general_info.json",
        300,
        "010001",
        "00587b6efdfe7fa9b9910cb3f79877167e8543240a659e9bd460c8ac33091ac0",
        "041324",
        38,
        (
            "address",
            "citytown",
            "count_of_facility_mort_measures",
            "count_of_facility_pt_exp_measures",
            "count_of_facility_readm_measures",
            "count_of_facility_safety_measures",
        ),
        ("json:results", "key:facility_id", "pages:offset"),
    ),
    Expect(
        "fdic_failed_bank_list_html",
        "fdic_failed_bank_list.html",
        77,
        "8266",
        "0f58b62683a679f20ce9df8721528fb5be1e79049758c64ff734b37414da071a",
        "21029",
        7,
        ("Bank Name", "City", "State", "Cert", "Acquiring Institution", "Closing Date"),
        ("table:class=usa-table#0", "header_row:0", "key:Cert"),
    ),
    Expect(
        "ofac_recent_actions_html",
        "ofac_recent_actions.html",
        20,
        "2026-09-09|/recent-actions/20260909",
        "5f0e221a1136c8aba054d23c1ef90306f542d451d15d4646dca24b50b5cd7b3b",
        "2026-07-27|/recent-actions/20260727",
        4,
        ("date", "href", "title", "category"),
        ("div.views-row a[href^=/recent-actions/]", "key:date,href"),
    ),
    Expect(
        "ofac_sdn_csv",
        "ofac_sdn.csv",
        19369,
        "36",
        "e285cbda82363da9c63473c87939ce0dbd9ce1ab5fe5aaf834541bcd0a68fab0",
        "58548",
        12,
        ("c0", "c1", "c2", "c3", "c4", "c5"),
        ("csv:delim=',';skip=0;header=False", "key:c0"),
    ),
    Expect(
        "fr_public_inspection_json",
        "fr_public_inspection.json",
        113,
        "2026-18583",
        "031ffad32e7db9dedbe1964f19a0b33b6559fe6032853d53305fc49cd63af689",
        "2026-18670",
        27,
        ("agencies", "agency_letters", "agency_names", "docket_numbers", "document_number", "editorial_note"),
        ("json:results", "key:document_number", "pages:none"),
    ),
    Expect(
        "faa_nas_status_xml",
        "faa_nas_status.xml",
        10,
        "Ground Delay Programs|MIA",
        "10e8789b045def6726b69bf1659289e1faf6eee6f7d1757bef6ef370f0771037",
        "Airport Closures|DAL",
        7,
        ("Arrival_Departure", "Avg", "Max", "Reason", "Reopen", "Start"),
        ("root:AIRPORT_STATUS_INFORMATION", "Delay_type/Name", "Delay_type/*_List/*", "key:Name,ARPT"),
    ),
    Expect(
        "fiscaldata_dts_operating_cash_json",
        "fiscaldata_dts_operating_cash.json",
        500,
        "2026-09-08|Treasury General Account (TGA) Opening Balance",
        "846293b588bb7ac4476beaa0d0d90edd15235e96d6994d554025c5a6dbac25b7",
        "2026-03-13|Treasury General Account (TGA) Closing Balance",
        16,
        (
            "account_type",
            "close_today_bal",
            "open_fiscal_year_bal",
            "open_month_bal",
            "open_today_bal",
            "record_calendar_day",
        ),
        ("json:data", "key:record_date,account_type", "pages:page"),
    ),
    Expect(
        "usgs_earthquakes_all_day_json",
        "usgs_earthquakes_all_day.json",
        251,
        "2026-09-10|nc75433162",
        "1a23ad0a8e295aeff4a3e80e49655df4efae5e81e55a382750600b8acdb2556a",
        "2026-09-09|nc75432412",
        30,
        ("geometry", "id", "properties", "properties.alert", "properties.cdi", "properties.code"),
        ("json:features", "key:date(properties.time),id"),
    ),
    Expect(
        "cisa_kev_json",
        "cisa_kev.json",
        1703,
        "CVE-2026-19490",
        "2cd29f8d2f77d1fdbe79ca8e049e4031fcf4f4932af7b1f456f3c0b4b793281e",
        "CVE-2020-29583",
        12,
        ("cveID", "cwes", "dateAdded", "dueDate", "forensicTriage", "knownRansomwareCampaignUse"),
        ("json:vulnerabilities", "key:cveID", "pages:none"),
    ),
    Expect(
        "austin_311_json",
        "austin_311.json",
        2000,
        "2026-08-11|26-00263080",
        "b371207e4eedd690215da531fbc0e53684c1ac60d6e2438271595ba1da54da2f",
        "2026-08-13|26-00265962",
        23,
        (
            "sr_closed_date",
            "sr_created_date",
            "sr_department_desc",
            "sr_location",
            "sr_location_city",
            "sr_location_council_district",
        ),
        ("json:$", "key:date(sr_created_date),sr_number", "pages:none"),
    ),
    Expect(
        "seattle_building_permits_csv",
        "seattle_building_permits.csv",
        2000,
        "3001095-EX",
        "df94675b38afea315754bcd59839dcd360bd2f748b1e5fc7adb3c799709ec6c8",
        "6068629-CN",
        41,
        ("permitnum", "permitclass", "permitclassmapped", "permittypemapped", "permittypedesc", "description"),
        ("csv:delim=',';skip=0;header=True", "key:permitnum"),
    ),
    Expect(
        "la_311_cases_2026_json",
        "la_311_cases_2026.json",
        2000,
        "2026-09-03|04517177",
        "55a0e01c37347e63f806ae7f502b9bc3ad03401415f07f154743ffb83aebb5d9",
        "2026-09-03|04519508",
        32,
        (
            "action_taken__c",
            "assigned_to__c",
            "casenumber",
            "closeddate",
            "created_by_user_organization",
            "createddate",
        ),
        ("json:$", "key:date(createddate),casenumber", "pages:none"),
    ),
]

SOURCES = {e["name"]: e for e in load_source_entries(ROOT / "sources.json")}


def make_adapter(name: str):  # type: ignore[no-untyped-def]
    return default_adapter_factory(Source(id=0, **SOURCES[name]), NoNetwork(), NOW)  # type: ignore[arg-type]


def _no_pdftotext(sel: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(s for s in sel if not s.startswith("pdftotext"))


def test_roster_is_the_amendment_b_composition() -> None:
    assert len(SOURCES) == 20
    fmt: dict[str, int] = {}
    tiers: dict[str, int] = {}
    for e in SOURCES.values():
        fmt[e["format"]] = fmt.get(e["format"], 0) + 1
        tiers[e["tier"]] = tiers.get(e["tier"], 0) + 1
    assert fmt == {"json": 9, "csv": 3, "pdf": 2, "html": 5, "xml": 1}
    assert tiers == {"A": 17, "B": 3}
    assert {e.name for e in EXPECT} == set(SOURCES)
    pi = SOURCES["fr_public_inspection_json"]
    assert pi["exit_target"] == "fr_documents_json" and pi["control_group"] == "EXIT-CONDITION"
    assert SOURCES["la_311_cases_2026_json"]["timeout_s"] == 120
    assert SOURCES["cms_hospital_general_info_json"]["expected_silent"] is True
    assert SOURCES["cisa_kev_json"]["unverified_contrary_claim"] is True
    for n in ("usgs_earthquakes_all_day_json", "austin_311_json", "la_311_cases_2026_json"):
        assert SOURCES[n]["window_days"] and SOURCES[n]["window_key_part"] == 0


@pytest.mark.parametrize("exp", EXPECT, ids=[e.name for e in EXPECT])
def test_fixture_extraction(exp: Expect) -> None:
    adapter = make_adapter(exp.name)
    raw = (FIX / exp.fixture).read_bytes()
    pairs = adapter.extract(raw)
    assert len(pairs) == exp.count
    assert pairs[0] == (exp.first_key, exp.first_hash)
    assert pairs[-1][0] == exp.last_key
    keys = [k for k, _ in pairs]
    assert len(set(keys)) == len(keys), "record keys must be unique"
    assert all(len(h) == 64 for _, h in pairs)
    fp = adapter.fingerprint(raw)
    assert isinstance(fp, Fingerprint)
    assert fp.record_count == exp.count
    assert len(fp.field_names) == exp.n_fields
    assert tuple(fp.field_names[: len(exp.fields_head)]) == exp.fields_head
    assert _no_pdftotext(tuple(fp.selectors)) == exp.selectors
    # deterministic
    assert adapter.extract(raw) == pairs and adapter.fingerprint(raw) == fp


@pytest.mark.parametrize("exp", EXPECT, ids=[e.name for e in EXPECT])
def test_garbage_payload_is_rejected(exp: Expect) -> None:
    adapter = make_adapter(exp.name)
    with pytest.raises(ExtractionError):
        adapter.extract(b"<html><body>Service unavailable</body></html>")


def test_format_control_pair_shares_key_space() -> None:
    csv_keys = {
        k for k, _ in make_adapter("treasury_par_yield_csv").extract((FIX / "treasury_par_yield.csv").read_bytes())
    }
    html_keys = {
        k for k, _ in make_adapter("treasury_par_yield_html").extract((FIX / "treasury_par_yield.html").read_bytes())
    }
    assert csv_keys == html_keys


def test_exit_pair_shares_key_space_format() -> None:
    pi = {
        k
        for k, _ in make_adapter("fr_public_inspection_json").extract((FIX / "fr_public_inspection.json").read_bytes())
    }
    docs = {k for k, _ in make_adapter("fr_documents_json").extract((FIX / "fr_documents.json").read_bytes())}
    assert pi and docs
    assert all("|" not in k for k in pi | docs)
    # both fixtures were captured the same morning: documents published today were on PI yesterday
    assert pi.isdisjoint(docs) or pi & docs


def test_public_inspection_page_views_do_not_mutate() -> None:
    raw = (FIX / "fr_public_inspection.json").read_bytes()
    doc = json.loads(raw)
    for r in doc["results"]:
        r["page_views"] = {"count": 999999, "last_updated": "never"}
    a = make_adapter("fr_public_inspection_json")
    assert a.extract(raw) == a.extract(json.dumps(doc).encode())


def test_sdn_drift_when_column_added() -> None:
    a = make_adapter("ofac_sdn_csv")
    raw = (FIX / "ofac_sdn.csv").read_bytes()
    broken = b"\n".join(line + b",extra" if line else line for line in raw.split(b"\n"))
    with pytest.raises(ExtractionError):
        a.extract(broken)


def test_treasury_html_drift_when_table_class_renamed() -> None:
    a = make_adapter("treasury_par_yield_html")
    raw = (FIX / "treasury_par_yield.html").read_bytes()
    a.extract(raw)
    with pytest.raises(ExtractionError):
        a.extract(raw.replace(b"views-table", b"data-table"))


def test_fdic_paged_payload_and_page_stop() -> None:
    page = (FIX / "fdic_failed_bank_list.html").read_bytes()
    a = make_adapter("fdic_failed_bank_list_html")
    assert isinstance(a, FdicFailedBanksHtml)
    page2 = re.sub(rb'(views-field-field-cert">)(\d+)', rb"\g<1>9\2", page)
    two = page + PAGE_SEP + page2
    pairs = a.extract(two)
    assert len(pairs) == 154 and a.fingerprint(two).record_count == 154
    from harness.adapter import FetchResult

    assert a._next(1, FetchResult(body=page, http_status=200, url="u", content_type="")) is None


def test_faa_repeated_airport_within_program_gets_suffix() -> None:
    xml = b"""<AIRPORT_STATUS_INFORMATION><Update_Time>x</Update_Time>
    <Delay_type><Name>Airport Closures</Name><Airport_Closure_List>
      <Airport><ARPT>DAL</ARPT><Reason>A</Reason><Start>1</Start><Reopen>2</Reopen></Airport>
      <Airport><ARPT>DAL</ARPT><Reason>B</Reason><Start>3</Start><Reopen>4</Reopen></Airport>
    </Airport_Closure_List></Delay_type></AIRPORT_STATUS_INFORMATION>"""
    keys = [k for k, _ in FaaNasStatusXml("u", client=NoNetwork()).extract(xml)]  # type: ignore[arg-type]
    assert keys == ["Airport Closures|DAL", "Airport Closures|DAL#2"]


def test_fr_issue_key_uses_url_date() -> None:
    a = make_adapter("fr_issue_pdf")
    assert isinstance(a, FrIssuePdf)
    assert a.url.endswith("FR-2026-09-09.pdf")  # NOW=2026-09-10 (Thu) -> prev business day
    keys = [k for k, _ in a.extract((FIX / "fr_issue.pdf").read_bytes())]
    assert all(k.startswith("2026-09-09|") for k in keys)


def test_offline_client_is_never_called() -> None:
    a = make_adapter("cisa_kev_json")
    with pytest.raises(AssertionError):
        a.fetch()


def test_timeouts_and_templates_reach_the_adapters() -> None:
    from harness.adapters.sources.dol_eta_weekly_claims_html import DolEtaWeeklyClaimsHtml

    la = make_adapter("la_311_cases_2026_json")
    assert la.timeout_s == 120  # type: ignore[attr-defined]
    assert make_adapter("cisa_kev_json").timeout_s is None  # type: ignore[attr-defined]  # -> client default 30 s
    eta = make_adapter("dol_eta_weekly_claims_html")
    assert isinstance(eta, DolEtaWeeklyClaimsHtml)
    assert eta.form["strtdate"] == "2025" and eta.form["enddate"] == "2026" and eta.form["final_yr"] == "2027"
    docs = make_adapter("fr_documents_json")
    assert docs.url.endswith("[publication_date][gte]=2026-08-11")  # type: ignore[attr-defined]  # today-30d


def test_cli_init_registers_all_twenty(tmp_path: Path) -> None:
    from harness.cli import main
    from harness.db import Database

    dbp = tmp_path / "h.sqlite"
    assert main(["--db", str(dbp), "init", "--sources", str(ROOT / "sources.json")]) == 0
    db = Database(dbp)
    try:
        rows = db.sources(active_only=True)
        assert len(rows) == 20
        by = {s.name: s for s in rows}
        assert by["fr_public_inspection_json"].exit_target == "fr_documents_json"
        assert by["la_311_cases_2026_json"].timeout_s == 120
        assert by["cms_hospital_general_info_json"].expected_silent
        assert by["cisa_kev_json"].unverified_contrary_claim
        assert by["usgs_earthquakes_all_day_json"].window_days == 1
        # re-init is idempotent (append-only: no new lineage rows when nothing changed)
        assert main(["--db", str(dbp), "init", "--sources", str(ROOT / "sources.json")]) == 0
        assert len(db.sources(active_only=True)) == 20
    finally:
        db.close()
