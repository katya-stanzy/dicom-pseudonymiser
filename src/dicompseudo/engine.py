"""Apply the configured rules to one pydicom Dataset (in place).

Order of precedence for every element (recursively inside sequences):
  1. keep_tags            -> kept unchanged
  2. extra_remove_tags    -> deleted
  3. replace              -> PatientName / PatientID set from the participant
  4. PS3.15 rule table    -> category switch decides; Basic Profile action if enabled
  5. any remaining PN     -> blanked if 'all_person_names' is on
Private tags and curves/overlays (groups 50xx/60xx) are removed first if switched on.

The engine never logs or returns element values: the Report has tags and counts only.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from pydicom.dataelem import DataElement, empty_value_for_VR
from pydicom.dataset import Dataset
from pydicom.sequence import Sequence

from . import __version__
from .config import Config
from .dates import shift_value, year_only
from .mapping import Participant, derived_shift
from .rules import meta, resolve_action, rule_for
from .uids import new_uid

TAG_PATIENT_NAME = 0x00100010
TAG_PATIENT_ID = 0x00100020
TAG_SOP_INSTANCE_UID = 0x00080018
DATE_VRS = ("DA", "DT")
# modalities that often carry text burned into the pixels
BURNED_IN_MODALITIES = {"US", "SC", "OT", "XC", "ES", "GM", "SM", "DOC"}

DUMMY = {
    "PN": "ANONYMOUS", "DA": "19000101", "TM": "000000", "DT": "19000101000000",
    "AS": "000Y", "CS": "ANONYMIZED", "AE": "ANONYMIZED", "SH": "ANONYMIZED",
    "LO": "ANONYMIZED", "ST": "ANONYMIZED", "LT": "ANONYMIZED", "UT": "ANONYMIZED",
    "UC": "ANONYMIZED", "IS": "0", "DS": "0", "US": 0, "SS": 0, "UL": 0, "SL": 0,
    "UV": 0, "SV": 0, "FL": 0.0, "FD": 0.0,
}
# DICOM CID 7050 de-identification method codes
CODES = {
    "basic": ("113100", "Basic Application Confidentiality Profile"),
    "clean_graphics": ("113103", "Clean Graphics Option"),
    "clean_struct": ("113104", "Clean Structured Content Option"),
    "clean_desc": ("113105", "Clean Descriptors Option"),
    "dates_full": ("113106", "Retain Longitudinal Temporal Information Full Dates Option"),
    "dates_mod": ("113107", "Retain Longitudinal Temporal Information Modified Dates Option"),
    "patient_chars": ("113108", "Retain Patient Characteristics Option"),
    "device": ("113109", "Retain Device Identity Option"),
    "uids": ("113110", "Retain UIDs Option"),
    "safe_private": ("113111", "Retain Safe Private Option"),
    "institution": ("113112", "Retain Institution Identity Option"),
}


def tag_str(tag: int) -> str:
    return f"({tag >> 16:04X},{tag & 0xFFFF:04X})"


@dataclass
class Report:
    actions: Counter = field(default_factory=Counter)      # (category, action) -> n
    tags: Counter = field(default_factory=Counter)         # "(gggg,eeee) action" -> n
    burned_in: str = "no"                                  # no | possible | yes
    date_shift_days: int | None = None

    def add(self, tag: int, category: str, action: str) -> None:
        self.actions[(category, action)] += 1
        self.tags[f"{tag_str(tag)} {action}"] += 1


class Pseudonymiser:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.keep = set(cfg.keep_tags)
        self.extra = set(cfg.extra_remove_tags)

    # ------------------------------------------------------------------ public
    def process(self, ds: Dataset, participant: Participant) -> Report:
        rep = Report()
        rep.burned_in = self.burned_in_status(ds)
        shift = None
        if self.cfg.dates == "shift":
            shift = (participant.date_shift_days if participant.date_shift_days is not None
                     else derived_shift(self.cfg.salt or "", participant.experimental_number))
            rep.date_shift_days = shift
        old_sop = str(ds.get("SOPInstanceUID", "") or "")

        if self.cfg.categories["private_tags"]:
            n = sum(1 for e in ds.iterall() if e.tag.is_private)
            ds.remove_private_tags()
            if n:
                rep.actions[("private_tags", "X")] += n
        if self.cfg.categories["curves_overlays"]:
            self._remove_groups(ds, rep)

        self._walk(ds, participant, shift, rep)
        self._mark(ds)
        self._fix_file_meta(ds, old_sop)
        return rep

    @staticmethod
    def burned_in_status(ds: Dataset) -> str:
        flag = str(ds.get("BurnedInAnnotation", "") or "").upper()
        if flag == "YES":
            return "yes"
        if flag == "NO":
            return "no"
        return "possible" if str(ds.get("Modality", "")).upper() in BURNED_IN_MODALITIES else "no"

    # ---------------------------------------------------------------- internals
    def _remove_groups(self, ds: Dataset, rep: Report) -> None:
        for elem in list(ds):
            g = elem.tag.group
            if 0x5000 <= g <= 0x50FF or 0x6000 <= g <= 0x60FF:
                del ds[elem.tag]
                rep.add(elem.tag, "curves_overlays", "X")
            elif elem.VR == "SQ":
                for item in elem.value:
                    self._remove_groups(item, rep)

    def _walk(self, ds: Dataset, p: Participant, shift: int | None, rep: Report) -> None:
        for tag in list(ds.keys()):
            elem = ds[tag]
            t = int(tag)
            if t in self.keep:
                rep.add(t, "keep_tags", "K")
                self._recurse(elem, p, shift, rep)
                continue
            if t in self.extra:
                del ds[tag]
                rep.add(t, "extra_remove_tags", "X")
                continue
            if t in (TAG_PATIENT_NAME, TAG_PATIENT_ID):
                target = "PatientName" if t == TAG_PATIENT_NAME else "PatientID"
                if target in self.cfg.replace:
                    elem.value = p.value(self.cfg.replace[target])
                    rep.add(t, "identity", "R")
                    continue

            rule = rule_for(t)
            if rule is not None:
                self._apply_rule(ds, elem, rule.category, rule.basic, p, shift, rep)
                continue
            if elem.VR == "PN" and self.cfg.categories["all_person_names"]:
                elem.value = empty_value_for_VR("PN")
                rep.add(t, "all_person_names", "Z")
                continue
            self._recurse(elem, p, shift, rep)

    def _recurse(self, elem: DataElement, p, shift, rep) -> None:
        if elem.VR == "SQ" and elem.value:
            for item in elem.value:
                self._walk(item, p, shift, rep)

    def _apply_rule(self, ds, elem, category, basic, p, shift, rep) -> None:
        t = int(elem.tag)
        action = resolve_action(basic)
        cfg = self.cfg

        if category == "dates":
            mode = cfg.dates
            if mode == "keep":
                return self._keep(elem, category, p, shift, rep, dates_too=False)
            if mode in ("shift", "year_only") and elem.VR in DATE_VRS:
                return self._transform_date(elem, category, shift, rep)
            if mode == "shift" and elem.VR == "TM":
                return self._keep(elem, category, p, shift, rep, dates_too=False)
            return self._do(ds, elem, category, action, rep)

        if category == "uids" or action == "U":
            if cfg.uids == "keep":
                return self._keep(elem, category, p, shift, rep)
            if elem.VR == "UI":
                return self._regen_uid(elem, category, rep)
            return self._do(ds, elem, category, "X" if action == "U" else action, rep)

        if category == "free_text":
            enabled = cfg.free_text == "remove"
        elif category == "other":
            enabled = True
        else:
            enabled = cfg.categories.get(category, True)

        if not enabled:
            return self._keep(elem, category, p, shift, rep)
        return self._do(ds, elem, category, action, rep)

    def _keep(self, elem, category, p, shift, rep, dates_too: bool = True) -> None:
        """Keep a value; dates inside kept categories still follow the dates setting."""
        if dates_too and elem.VR in DATE_VRS and self.cfg.dates in ("shift", "year_only"):
            return self._transform_date(elem, category, shift, rep)
        rep.add(elem.tag, category, "K")
        self._recurse(elem, p, shift, rep)

    def _transform_date(self, elem, category, shift, rep) -> None:
        if elem.value in (None, ""):
            return
        if self.cfg.dates == "shift":
            elem.value = shift_value(str(elem.value), elem.VR, shift or 0)
            rep.add(elem.tag, category, "shift")
        else:
            elem.value = year_only(str(elem.value), elem.VR)
            rep.add(elem.tag, category, "year")

    def _regen_uid(self, elem, category, rep) -> None:
        vals = elem.value if elem.VM > 1 else [elem.value]
        new = [new_uid(str(v), self.cfg.salt or "") for v in vals if v]
        elem.value = new if elem.VM > 1 else (new[0] if new else "")
        rep.add(elem.tag, category, "U")

    def _do(self, ds, elem, category, action, rep) -> None:
        t = int(elem.tag)
        if action == "X":
            del ds[elem.tag]
        elif action == "Z":
            elem.value = Sequence() if elem.VR == "SQ" else empty_value_for_VR(elem.VR)
        elif action == "D":
            if elem.VR == "SQ":
                elem.value = Sequence()
            elif elem.VR == "UI":
                return self._regen_uid(elem, category, rep)
            else:
                elem.value = DUMMY.get(elem.VR, empty_value_for_VR(elem.VR))
        rep.add(t, category, action)

    def _mark(self, ds: Dataset) -> None:
        cfg, cats = self.cfg, self.cfg.categories
        ds.PatientIdentityRemoved = "YES"
        m = meta()
        ds.DeidentificationMethod = f"dicompseudo {__version__}; PS3.15 {m['edition']} Table E.1-1"
        used = ["basic"]
        if cats["graphics"]:
            used.append("clean_graphics")
        if cats["structured_content"]:
            used.append("clean_struct")
        if cfg.free_text == "remove":
            used.append("clean_desc")
        if cfg.dates == "keep":
            used.append("dates_full")
        elif cfg.dates in ("shift", "year_only"):
            used.append("dates_mod")
        if not (cats["age"] and cats["sex"] and cats["body_measurements"]):
            used.append("patient_chars")
        if not cats["device"]:
            used.append("device")
        if cfg.uids == "keep":
            used.append("uids")
        if not cats["private_tags"]:
            used.append("safe_private")      # nearest code; private tags were kept
        if not cats["institution"]:
            used.append("institution")
        seq = Sequence()
        for key in used:
            code, meaning = CODES[key]
            item = Dataset()
            item.CodeValue = code
            item.CodingSchemeDesignator = "DCM"
            item.CodeMeaning = meaning
            seq.append(item)
        ds.DeidentificationMethodCodeSequence = seq
        ds.LongitudinalTemporalInformationModified = {
            "keep": "UNMODIFIED", "shift": "MODIFIED",
            "year_only": "MODIFIED", "remove": "REMOVED"}[cfg.dates]

    # file meta (group 0002): AE titles can name the site, private info the vendor
    FILE_META_SITE = (0x00020016, 0x00020017, 0x00020018)  # Source/Sending/Receiving AE
    FILE_META_PRIVATE = (0x00020100, 0x00020102)            # Private Information (Creator)

    def _fix_file_meta(self, ds: Dataset, old_sop: str) -> None:
        fm = getattr(ds, "file_meta", None)
        if fm is None:
            return
        cats = self.cfg.categories
        if cats["institution"] or cats["device"]:
            for t in self.FILE_META_SITE:
                if t in fm:
                    del fm[t]
        if cats["private_tags"]:
            for t in self.FILE_META_PRIVATE:
                if t in fm:
                    del fm[t]
        new_sop = str(ds.get("SOPInstanceUID", "") or "")
        if "MediaStorageSOPInstanceUID" in fm and new_sop and new_sop != old_sop:
            fm.MediaStorageSOPInstanceUID = new_sop
