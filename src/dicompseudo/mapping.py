"""Participant mapping: source key -> experimental number, nickname, date shift.

CSV columns: source_key, experimental_number, nickname[, date_shift_days]
Error messages refer to row numbers only, never to cell values.
"""
from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path

REQUIRED = ("source_key", "experimental_number", "nickname")


class MappingError(ValueError):
    pass


@dataclass(frozen=True)
class Participant:
    source_key: str
    experimental_number: str
    nickname: str
    date_shift_days: int | None = None

    def value(self, source: str) -> str:
        return getattr(self, source)


def derived_shift(salt: str, experimental_number: str) -> int:
    """Deterministic per-participant shift, 365..3650 days into the past."""
    h = hashlib.sha256(f"{salt}|shift|{experimental_number}".encode()).digest()
    return -(365 + int.from_bytes(h[:4], "big") % 3286)


class Mapping:
    def __init__(self, participants: list[Participant]):
        self._by_key: dict[str, Participant] = {}
        for p in participants:
            self._by_key[p.source_key] = p

    def __len__(self) -> int:
        return len(self._by_key)

    def get(self, source_key: str) -> Participant | None:
        return self._by_key.get(str(source_key).strip())

    def participants(self) -> list[Participant]:
        return list(self._by_key.values())

    @classmethod
    def load(cls, path: str | Path) -> "Mapping":
        p = Path(path)
        if not p.is_file():
            raise MappingError(f"mapping file not found: {p}")
        with p.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            cols = [c.strip() for c in (reader.fieldnames or [])]
            missing = [c for c in REQUIRED if c not in cols]
            if missing:
                raise MappingError(f"mapping file is missing column(s): {missing}")
            rows = [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in reader]
        return cls.from_rows(rows)

    @classmethod
    def from_rows(cls, rows: list[dict[str, str]]) -> "Mapping":
        out: list[Participant] = []
        keys: set[str] = set()
        numbers: set[str] = set()
        for i, r in enumerate(rows, start=2):  # row 1 is the header
            for c in REQUIRED:
                if not r.get(c):
                    raise MappingError(f"mapping row {i}: '{c}' is empty")
            if r["source_key"] in keys:
                raise MappingError(f"mapping row {i}: duplicate source_key")
            if r["experimental_number"] in numbers:
                raise MappingError(f"mapping row {i}: duplicate experimental_number")
            shift = r.get("date_shift_days") or None
            if shift is not None:
                try:
                    shift = int(shift)
                except ValueError:
                    raise MappingError(f"mapping row {i}: date_shift_days must be a whole number")
            keys.add(r["source_key"])
            numbers.add(r["experimental_number"])
            out.append(Participant(r["source_key"], r["experimental_number"], r["nickname"], shift))
        if not out:
            raise MappingError("mapping file has no participants")
        return cls(out)
