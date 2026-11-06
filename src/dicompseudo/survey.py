"""Survey an input folder before a run: which sensitive tags are present, how many files
match a participant, which devices produced them. Counts and tag names only.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from pydicom import dcmread

from .config import Config
from .engine import Pseudonymiser, tag_str
from .io import is_dicom, iter_files, participant_for
from .mapping import Mapping
from .pixels import find_template
from .rules import rule_for


@dataclass
class Survey:
    files: int = 0
    dicom: int = 0
    matched: int = 0
    unmatched: int = 0
    errors: int = 0
    category_files: Counter = field(default_factory=Counter)   # category -> files with it
    tag_files: Counter = field(default_factory=Counter)        # "(tag) keyword [category]" -> files
    burned_in: Counter = field(default_factory=Counter)
    devices: Counter = field(default_factory=Counter)          # technical signature -> files


def _nonempty(elem) -> bool:
    if elem.VR == "SQ":
        return bool(elem.value)
    return elem.value not in (None, "", b"")


def survey(cfg: Config, mapping: Mapping | None) -> Survey:
    s = Survey()
    for path in iter_files(cfg.input_dir):
        s.files += 1
        if not is_dicom(path):
            continue
        s.dicom += 1
        try:
            ds = dcmread(path, stop_before_pixels=True)
        except Exception:
            s.errors += 1
            continue
        if mapping is not None:
            rel = path.relative_to(cfg.input_dir)
            p = participant_for(rel, str(ds.get("PatientID", "") or ""), cfg, mapping)
            s.matched += p is not None
            s.unmatched += p is None
        cats, tags = set(), set()
        for elem in ds.iterall():
            if not _nonempty(elem):
                continue
            t = int(elem.tag)
            if elem.tag.is_private:
                cats.add("private_tags")
                continue
            g = elem.tag.group
            if 0x5000 <= g <= 0x50FF or 0x6000 <= g <= 0x60FF:
                cats.add("curves_overlays")
                continue
            r = rule_for(t)
            if r is not None:
                cats.add(r.category)
                tags.add(f"{tag_str(t)} {elem.keyword or r.name} [{r.category}]")
            elif elem.VR == "PN":
                cats.add("all_person_names")
                tags.add(f"{tag_str(t)} {elem.keyword} [all_person_names]")
        s.category_files.update(cats)
        s.tag_files.update(tags)
        s.burned_in[Pseudonymiser.burned_in_status(ds)] += 1
        sig = (str(ds.get("Modality", "")), str(ds.get("Manufacturer", "")),
               str(ds.get("ManufacturerModelName", "")),
               f"{ds.get('Rows', '?')}x{ds.get('Columns', '?')}",
               str(ds.get("BurnedInAnnotation", "") or "-"))
        tpl = find_template(ds, cfg.pixel_masks)
        s.devices[" | ".join(sig) + f"  -> {('mask: ' + tpl.name) if tpl else 'no mask'}"] += 1
    return s
