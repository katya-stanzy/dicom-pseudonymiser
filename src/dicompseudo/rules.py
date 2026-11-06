"""Rule table: DICOM PS3.15 Table E.1-1, with a switchable category per attribute."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

DATA = files("dicompseudo") / "data"


@dataclass(frozen=True)
class Rule:
    tag: str           # "(0010,0010)" or a mask such as "(60xx,3000)"
    keyword: str
    name: str
    vr: str
    basic: str         # Basic Profile action: X, Z, D, U, X/Z, X/D, Z/D, X/Z/D, X/Z/U*
    category: str
    clean_desc: str
    rtn_dates_full: str


def _mask_to_int(tag: str) -> tuple[int, int]:
    """'(60xx,3000)' -> (value, mask) where mask has 0 for wildcard nibbles."""
    h = tag.strip("()").replace(",", "")
    value = int(h.replace("x", "0"), 16)
    mask = int("".join("0" if c == "x" else "F" for c in h), 16)
    return value, mask


@lru_cache(maxsize=1)
def load_rules() -> tuple[dict[int, Rule], list[tuple[int, int, Rule]]]:
    """Return (exact rules by int tag, masked rules as (value, mask, rule))."""
    exact: dict[int, Rule] = {}
    masked: list[tuple[int, int, Rule]] = []
    with (DATA / "ps315_annexE.csv").open(encoding="utf8", newline="") as f:
        for r in csv.DictReader(f):
            rule = Rule(r["tag"], r["keyword"], r["name"], r["vr"], r["basic"],
                        r["category"], r["clean_desc"], r["rtn_dates_full"])
            value, mask = _mask_to_int(r["tag"])
            if mask == 0xFFFFFFFF:
                exact[value] = rule
            else:
                masked.append((value, mask, rule))
    return exact, masked


def rule_for(tag: int) -> Rule | None:
    exact, masked = load_rules()
    r = exact.get(tag)
    if r is not None:
        return r
    for value, mask, rule in masked:
        if tag & mask == value:
            return rule
    return None


def meta() -> dict:
    return json.loads((DATA / "ps315_meta.json").read_text(encoding="utf8"))


def resolve_action(basic: str) -> str:
    """Collapse a Basic Profile code to one action.

    Combined codes depend on the attribute's Type in each IOD, which a generic tool
    cannot know. A value is never kept either way, so the safest choice that keeps files
    valid is used: D (dummy) if offered, otherwise Z (empty), otherwise X (remove).
    U* (UID) is always U.
    """
    codes = basic.replace("*", "").split("/")
    if "U" in codes:
        return "U"
    for c in ("D", "Z", "X"):
        if c in codes:
            return c
    return "X"
