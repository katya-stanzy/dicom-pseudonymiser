"""Leak scan of a pseudonymised folder.

Reads every DICOM in the folder and reports, per finding, the number of files.
Findings name tags, categories, mapping row numbers or terms-file line numbers,
never values. Exit status of `dicompseudo check` is 1 when any LEAK is found.

Optional inputs (all private, never printed):
  config   the settings used for the run, so deliberately kept tags are not leaks
  mapping  participants.csv: original source keys must not appear anywhere
  terms    a text file with one word per line (real names, the lab's name, a town, ...)
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from pydicom import dcmread

from .config import Config
from .engine import DUMMY, TAG_PATIENT_ID, TAG_PATIENT_NAME, Pseudonymiser, tag_str
from .io import is_dicom, iter_files
from .mapping import Mapping
from .rules import resolve_action, rule_for

TEXT_VRS = {"AE", "AS", "CS", "DA", "DS", "DT", "IS", "LO", "LT", "PN", "SH", "ST", "TM",
            "UC", "UI", "UR", "UT"}
DUMMIES = {str(v) for v in DUMMY.values()} | {"ANONYMOUS", "ANONYMIZED"}
MIN_TERM = 3
NO_SEARCH_VRS = {"UI", "DA", "DT", "TM", "DS", "IS", "AS"}   # numbers: too many false hits


def _token(word: str) -> re.Pattern:
    """Match a word only as a whole token, case-insensitive ('003' won't hit 'jrf003')."""
    return re.compile(r"(?<![a-z0-9])" + re.escape(word.lower()) + r"(?![a-z0-9])")


@dataclass
class CheckResult:
    files: int = 0
    dicom: int = 0
    leaks: Counter = field(default_factory=Counter)      # description -> files
    warnings: Counter = field(default_factory=Counter)

    @property
    def ok(self) -> bool:
        return not self.leaks


def load_terms(path: str | Path | None) -> list[str]:
    if not path:
        return []
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    return [ln.strip() for ln in lines]


def _nonempty(elem) -> bool:
    if elem.VR == "SQ":
        return bool(elem.value)
    return elem.value not in (None, "", b"")


def _text(elem) -> str:
    if elem.VR in TEXT_VRS and elem.VR not in NO_SEARCH_VRS:
        return str(elem.value)
    return ""


def _is_dummy(elem) -> bool:
    v = elem.value
    vals = list(v) if elem.VM > 1 else [v]
    return all(str(x) in DUMMIES for x in vals)


class Checker:
    def __init__(self, cfg: Config | None = None, mapping: Mapping | None = None,
                 terms: list[str] | None = None):
        self.cfg = cfg or Config()
        self.mapping = mapping
        self.allowed: set[str] = set()
        self.keys: list[tuple[int, str]] = []
        if mapping is not None:
            for i, p in enumerate(mapping.participants(), start=2):
                self.allowed |= {p.experimental_number, p.nickname}
                if len(p.source_key) >= MIN_TERM and p.source_key not in self.allowed:
                    self.keys.append((i, _token(p.source_key)))
        self.terms = [(i, _token(t)) for i, t in enumerate(terms or [], start=1)
                      if len(t) >= MIN_TERM]

    # ------------------------------------------------------------------ element
    def _element_finding(self, elem) -> str | None:
        cfg = self.cfg
        t = int(elem.tag)
        if t in cfg.keep_tags or not _nonempty(elem):
            return None
        if elem.tag.is_private:
            return "private tag present" if cfg.categories["private_tags"] else None
        g = elem.tag.group
        if 0x5000 <= g <= 0x50FF or 0x6000 <= g <= 0x60FF:
            return "curve/overlay group present" if cfg.categories["curves_overlays"] else None
        if t in (TAG_PATIENT_NAME, TAG_PATIENT_ID):
            target = "PatientName" if t == TAG_PATIENT_NAME else "PatientID"
            if target in cfg.replace:
                if self.mapping is None or str(elem.value) in self.allowed:
                    return None
                return f"{tag_str(t)} {elem.keyword}: value is not a mapped number/nickname"

        r = rule_for(t)
        if r is None:
            if elem.VR == "PN" and cfg.categories["all_person_names"]:
                if str(elem.value) in self.allowed or _is_dummy(elem):
                    return None
                return f"{tag_str(t)} {elem.keyword} [all_person_names]"
            return None

        cat, action = r.category, resolve_action(r.basic)
        label = f"{tag_str(t)} {elem.keyword or r.name} [{cat}]"
        if cat == "dates":
            if cfg.dates == "keep":
                return None
            if cfg.dates in ("shift", "year_only") and elem.VR in ("DA", "DT"):
                return None
            if cfg.dates == "shift" and elem.VR == "TM":
                return None
            return None if _is_dummy(elem) else label
        if cat == "uids" or action == "U":
            if cfg.uids == "keep":
                return None
            if elem.VR == "UI":
                vals = list(elem.value) if elem.VM > 1 else [elem.value]
                return None if all(str(v).startswith("2.25.") for v in vals) else \
                    f"{label}: UID not regenerated"
            return label
        if cat == "free_text":
            enabled = cfg.free_text == "remove"
        elif cat == "other":
            enabled = True
        else:
            enabled = cfg.categories.get(cat, True)
        if not enabled:
            return None
        if elem.VR == "SQ":
            return label            # the engine deletes or empties these sequences
        return None if _is_dummy(elem) else label

    # ------------------------------------------------------------------ file
    def check_dataset(self, ds, rel_path: str) -> tuple[set[str], set[str]]:
        leaks, warns = set(), set()
        for elem in ds.iterall():
            f = self._element_finding(elem)
            if f:
                leaks.add(f)
            txt = _text(elem).lower()
            if txt:
                for i, k in self.keys:
                    if k.search(txt):
                        leaks.add(f"mapping row {i}: original source_key found in {tag_str(elem.tag)}")
                for i, term in self.terms:
                    if term.search(txt):
                        leaks.add(f"terms line {i}: found in {tag_str(elem.tag)}")
        fm = getattr(ds, "file_meta", None)
        if fm is not None:
            cats = self.cfg.categories
            for t in Pseudonymiser.FILE_META_SITE:
                if t in fm and (cats["institution"] or cats["device"]) and fm[t].value:
                    leaks.add(f"{tag_str(t)} file meta AE title present")
            for elem in fm:
                txt = _text(elem).lower()
                for i, term in self.terms:
                    if term.search(txt):
                        leaks.add(f"terms line {i}: found in file meta {tag_str(elem.tag)}")
        low = rel_path.lower()
        for i, k in self.keys:
            if k.search(low):
                leaks.add(f"mapping row {i}: original source_key found in output path")
        for i, term in self.terms:
            if term.search(low):
                leaks.add(f"terms line {i}: found in output path")
        if str(ds.get("PatientIdentityRemoved", "")) != "YES":
            leaks.add("(0012,0062) PatientIdentityRemoved is not YES")
        b = Pseudonymiser.burned_in_status(ds)
        if b != "no":
            warns.add(f"burned-in annotation {b} (pixel text not checked; see phase 2)")
        return leaks, warns

    def run(self, folder: Path) -> CheckResult:
        res = CheckResult()
        for path in iter_files(folder):
            res.files += 1
            if not is_dicom(path):
                res.warnings["non-DICOM file in output"] += 1
                continue
            res.dicom += 1
            try:
                ds = dcmread(path, stop_before_pixels=True)
            except Exception as e:
                res.warnings[f"unreadable DICOM ({type(e).__name__})"] += 1
                continue
            leaks, warns = self.check_dataset(ds, path.relative_to(folder).as_posix())
            res.leaks.update(leaks)
            res.warnings.update(warns)
        return res
