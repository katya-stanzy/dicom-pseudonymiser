"""Build src/dicompseudo/data/ps315_annexE.csv from DICOM PS3.15 Table E.1-1.

Usage:
    python tools/build_rules.py chapter_E.html

`chapter_E.html` is downloaded from
https://dicom.nema.org/medical/dicom/current/output/chtml/part15/chapter_E.html
It is not committed: only the derived CSV and data/ps315_meta.json are.

Each attribute keeps its Basic Profile action and the standard's option columns, and
receives one *category* that environment.json can switch on/off. Categories are
assigned in priority order:
  1. explicit keyword sets below (identity, age, sex, body_measurements, contact,
     medical_social, staff, identifiers, institution, device)
  2. the standard's option columns (Retain UIDs -> uids, Retain Institution Identity ->
     institution, Retain Device Identity -> device, Retain Patient Characteristics ->
     medical_social, Retain Longitudinal Full Dates -> dates, Clean Graphics -> graphics,
     Clean Structured Content -> structured_content, Clean Descriptors -> free_text)
  3. group 50xx/60xx -> curves_overlays (checked right after the explicit sets)
  4. VR = PN -> staff (remaining person names are staff or other persons)
  5. otherwise -> other (always processed with the Basic Profile action)
"""
from __future__ import annotations

import csv
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

from pydicom.datadict import dictionary_VR, keyword_for_tag, tag_for_keyword

ROOT = Path(__file__).resolve().parents[1]
OUT_CSV = ROOT / "src" / "dicompseudo" / "data" / "ps315_annexE.csv"
OUT_META = ROOT / "src" / "dicompseudo" / "data" / "ps315_meta.json"
SOURCE_URL = "https://dicom.nema.org/medical/dicom/current/output/chtml/part15/chapter_E.html"

EXPLICIT: dict[str, list[str]] = {
    "identity": [
        "PatientName", "PatientID", "OtherPatientIDs", "OtherPatientIDsSequence",
        "OtherPatientNames", "PatientBirthName", "PatientMotherBirthName",
        "IssuerOfPatientID", "IssuerOfPatientIDQualifiersSequence", "MedicalRecordLocator",
        "PatientInsurancePlanCodeSequence", "ResponsiblePerson", "ResponsibleOrganization",
        "PersonName", "ReferencedPatientPhotoSequence",
    ],
    "age": [
        "PatientAge", "PatientBirthDate", "PatientBirthTime",
        "PatientBirthDateInAlternativeCalendar", "PatientDeathDateInAlternativeCalendar",
        "PatientAlternativeCalendar",
    ],
    "sex": ["PatientSex", "PatientSexNeutered", "SexParametersForClinicalUseCategorySequence",
            "GenderIdentitySequence"],
    "body_measurements": [
        "PatientSize", "PatientWeight", "PatientBodyMassIndex", "PatientSizeCodeSequence",
        "MeasuredAPDimension", "MeasuredLateralDimension",
    ],
    "contact": [
        "PatientAddress", "PatientTelephoneNumbers", "PatientTelecomInformation",
        "CountryOfResidence", "RegionOfResidence", "MilitaryRank", "BranchOfService",
    ],
    "medical_social": [
        "EthnicGroup", "EthnicGroupCodeSequence", "EthnicGroups", "Occupation",
        "AdditionalPatientHistory", "PatientComments", "MedicalAlerts", "Allergies",
        "PregnancyStatus", "SmokingStatus", "PatientReligiousPreference", "LastMenstrualDate",
        "PatientState", "SpecialNeeds",
    ],
    "staff": [
        "ReferringPhysicianName", "ReferringPhysicianAddress",
        "ReferringPhysicianTelephoneNumbers", "ReferringPhysicianIdentificationSequence",
        "PerformingPhysicianName", "PerformingPhysicianIdentificationSequence",
        "OperatorsName", "OperatorIdentificationSequence", "NameOfPhysiciansReadingStudy",
        "PhysiciansReadingStudyIdentificationSequence", "PhysiciansOfRecord",
        "PhysiciansOfRecordIdentificationSequence", "RequestingPhysician",
        "ScheduledPerformingPhysicianName", "ConsultingPhysicianName",
    ],
    "identifiers": [
        "AccessionNumber", "StudyID", "RequestAttributesSequence", "RequestedProcedureID",
        "ScheduledProcedureStepID", "PerformedProcedureStepID", "AdmissionID",
        "IssuerOfAdmissionIDSequence", "ServiceEpisodeID",
        "PlacerOrderNumberImagingServiceRequest", "FillerOrderNumberImagingServiceRequest",
    ],
    "institution": [
        "InstitutionName", "InstitutionAddress", "InstitutionalDepartmentName",
        "InstitutionCodeSequence", "InstitutionalDepartmentTypeCodeSequence",
    ],
    "device": ["StationName", "DeviceSerialNumber"],
}

COLS = {  # CSV column -> header text in Table E.1-1
    "basic": "Basic Prof.",
    "rtn_uids": "Rtn. UIDs Opt.",
    "rtn_dev": "Rtn. Dev. Id. Opt.",
    "rtn_inst": "Rtn. Inst. Id. Opt.",
    "rtn_pat": "Rtn. Pat. Chars. Opt.",
    "rtn_dates_full": "Rtn. Long. Full Dates Opt.",
    "rtn_dates_mod": "Rtn. Long. Modif. Dates Opt.",
    "clean_desc": "Clean Desc. Opt.",
    "clean_struct": "Clean Struct. Cont. Opt.",
    "clean_graph": "Clean Graph. Opt.",
}


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_table(html_text: str) -> tuple[list[str], list[list[str]]]:
    start = html_text.find('id="table_E.1-1"')
    if start < 0:
        raise SystemExit("Table E.1-1 not found in the HTML file")
    end = html_text.find("</table>", start)
    p = _TableParser()
    p.feed(html_text[start:end])
    header = next(r for r in p.rows if r and r[0].startswith("Attribute Name"))
    body = [r for r in p.rows if len(r) == len(header)
            and re.fullmatch(r"\([0-9A-Fx]{4},[0-9A-Fx]{4}\)", r[1])]
    return header, body


def keyword_and_vr(tag_text: str) -> tuple[str, str]:
    if "x" in tag_text:
        return "", ""
    tag = int(tag_text[1:5] + tag_text[6:10], 16)
    try:
        return keyword_for_tag(tag) or "", dictionary_VR(tag)
    except KeyError:
        return "", ""


def categorise(row: dict[str, str], kw_to_cat: dict[str, str]) -> str:
    if row["keyword"] in kw_to_cat:
        return kw_to_cat[row["keyword"]]
    if row["tag"].startswith(("(50xx", "(60xx")):
        return "curves_overlays"
    if row["rtn_uids"] == "K":
        return "uids"
    if row["rtn_inst"] in ("K", "C"):
        return "institution"
    if row["rtn_dev"] in ("K", "C"):
        return "device"
    if row["rtn_pat"] in ("K", "C"):
        return "medical_social"
    if row["vr"] == "PN":
        return "staff"
    if row["rtn_dates_full"] == "K":
        return "dates"
    if row["clean_graph"] == "C":
        return "graphics"
    if row["clean_struct"] == "C":
        return "structured_content"
    if row["clean_desc"] == "C":
        return "free_text"
    return "other"


def main(html_path: str) -> None:
    text = Path(html_path).read_text(encoding="utf8")
    edition = re.search(r"DICOM PS3\.15 (\d{4}[a-e])", text)
    header, body = parse_table(text)
    idx = {k: header.index(v) for k, v in COLS.items()}
    kw_to_cat = {kw: cat for cat, kws in EXPLICIT.items() for kw in kws}

    rows: list[dict[str, str]] = []
    seen_kw: set[str] = set()
    for r in body:
        kw, vr = keyword_and_vr(r[1])
        row = {"tag": r[1], "keyword": kw, "name": r[0], "vr": vr,
               **{k: r[i] for k, i in idx.items()}, "source": "PS3.15"}
        row["category"] = categorise(row, kw_to_cat)
        rows.append(row)
        seen_kw.add(kw)

    # explicit keywords the table does not list: add them, Basic action X (remove)
    added = []
    for kw, cat in kw_to_cat.items():
        if kw in seen_kw:
            continue
        tag = tag_for_keyword(kw)
        if tag is None:
            print(f"  note: '{kw}' is not in the pydicom dictionary, skipped")
            continue
        t = f"({tag >> 16:04X},{tag & 0xFFFF:04X})"
        rows.append({"tag": t, "keyword": kw, "name": kw, "vr": dictionary_VR(tag),
                     **{k: "" for k in COLS}, "basic": "X", "source": "added",
                     "category": cat})
        added.append(kw)

    rows.sort(key=lambda r: r["tag"])
    fields = ["tag", "keyword", "name", "vr", *COLS, "category", "source"]
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    meta = {"source_url": SOURCE_URL, "edition": edition.group(1) if edition else "unknown",
            "table": "E.1-1", "n_rows": len(rows), "added_keywords": added,
            "category_counts": dict(sorted(counts.items()))}
    OUT_META.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf8")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
