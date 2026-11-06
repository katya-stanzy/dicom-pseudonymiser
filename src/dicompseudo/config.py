"""Load and validate environment.json."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from pydicom.datadict import tag_for_keyword

SALT_ENV = "DICOMPSEUDO_SALT"

# Switchable categories and their defaults (True = remove/clean).
CATEGORIES: dict[str, bool] = {
    "identity": True,
    "age": True,
    "sex": True,
    "body_measurements": True,
    "contact": True,
    "medical_social": True,
    "institution": True,
    "device": True,
    "staff": True,
    "all_person_names": True,
    "identifiers": True,
    "structured_content": True,
    "graphics": True,
    "private_tags": True,
    "curves_overlays": True,
}
DATES_MODES = ("keep", "remove", "year_only", "shift")
UID_MODES = ("keep", "regenerate")
FREE_TEXT_MODES = ("keep", "remove")
BURNED_IN_MODES = ("flag", "skip", "allow")
MATCH_MODES = ("folder", "PatientID")
REPLACE_SOURCES = ("experimental_number", "nickname")
REPLACE_TARGETS = ("PatientName", "PatientID")


class ConfigError(ValueError):
    """Raised with a readable message when environment.json is invalid."""


@dataclass
class Config:
    input_dir: Path | None = None
    output_dir: Path | None = None
    mapping_file: Path | None = None
    audit_dir: Path | None = None          # default: <mapping_file folder>/dicompseudo_audit
    match_participant_by: str = "folder"
    replace: dict[str, str] = field(
        default_factory=lambda: {"PatientName": "nickname", "PatientID": "experimental_number"})
    categories: dict[str, bool] = field(default_factory=lambda: dict(CATEGORIES))
    dates: str = "shift"
    uids: str = "regenerate"
    free_text: str = "remove"
    keep_tags: list[int] = field(default_factory=list)
    extra_remove_tags: list[int] = field(default_factory=list)
    on_burned_in_annotation: str = "flag"
    pixel_masks: list = field(default_factory=list)   # list[pixels.MaskTemplate]
    workers: int = 4
    overwrite: bool = False
    salt: str | None = None

    @property
    def needs_salt(self) -> bool:
        return self.uids == "regenerate" or self.dates == "shift"

    def check_paths(self) -> None:
        """Refuse folder layouts that could overwrite input or leak the audit log."""
        def inside(a: Path, b: Path) -> bool:
            a, b = a.resolve(), b.resolve()
            return a == b or b in a.parents
        if not self.input_dir or not self.input_dir.is_dir():
            raise ConfigError(f"input_dir does not exist: {self.input_dir}")
        if inside(self.output_dir, self.input_dir) or inside(self.input_dir, self.output_dir):
            raise ConfigError("output_dir and input_dir must not contain each other")
        if inside(self.audit_dir, self.output_dir):
            raise ConfigError("audit_dir must not be inside output_dir (the audit log lists "
                              "original paths and must stay private)")
        if self.mapping_file and inside(self.mapping_file, self.output_dir):
            raise ConfigError("mapping_file must not be inside output_dir")


def parse_tag(text: str) -> int:
    """Accept a keyword ('PatientSex') or a tag ('(0010,0040)', '00100040', '0010,0040')."""
    s = str(text).strip()
    t = tag_for_keyword(s)
    if t is not None:
        return t
    h = s.strip("()").replace(",", "").replace(" ", "")
    if len(h) == 8:
        try:
            return int(h, 16)
        except ValueError:
            pass
    raise ConfigError(f"'{text}' is neither a DICOM keyword nor a tag like (0010,0040)")


def _choice(d: dict, key: str, options: tuple, default):
    v = d.get(key, default)
    if v not in options:
        raise ConfigError(f"'{key}' must be one of {list(options)}, got {v!r}")
    return v


def from_dict(d: dict, *, require_paths: bool = True, salt: str | None = None) -> Config:
    if not isinstance(d, dict):
        raise ConfigError("environment.json must contain a JSON object")
    known = {"input_dir", "output_dir", "mapping_file", "audit_dir", "match_participant_by", "replace",
             "categories", "dates", "uids", "free_text", "keep_tags", "extra_remove_tags",
             "on_burned_in_annotation", "pixel_masks", "workers", "overwrite"}
    unknown = set(d) - known
    if unknown:
        raise ConfigError(f"unknown setting(s): {sorted(unknown)}")

    cfg = Config()
    for key in ("input_dir", "output_dir", "mapping_file"):
        if d.get(key):
            setattr(cfg, key, Path(d[key]))
        elif require_paths:
            raise ConfigError(f"'{key}' is required")

    if d.get("audit_dir"):
        cfg.audit_dir = Path(d["audit_dir"])
    elif cfg.mapping_file:
        cfg.audit_dir = cfg.mapping_file.parent / "dicompseudo_audit"

    cfg.match_participant_by = _choice(d, "match_participant_by", MATCH_MODES, "folder")

    rep = d.get("replace", cfg.replace)
    if not isinstance(rep, dict):
        raise ConfigError("'replace' must be an object like {\"PatientID\": \"experimental_number\"}")
    for k, v in rep.items():
        if k not in REPLACE_TARGETS:
            raise ConfigError(f"'replace' key must be one of {list(REPLACE_TARGETS)}, got {k!r}")
        if v not in REPLACE_SOURCES:
            raise ConfigError(f"'replace.{k}' must be one of {list(REPLACE_SOURCES)}, got {v!r}")
    cfg.replace = dict(rep)

    cats = d.get("categories", {})
    if not isinstance(cats, dict):
        raise ConfigError("'categories' must be an object of true/false switches")
    for k, v in cats.items():
        if k not in CATEGORIES:
            raise ConfigError(f"unknown category {k!r}; known: {sorted(CATEGORIES)}")
        if not isinstance(v, bool):
            raise ConfigError(f"category '{k}' must be true or false")
    cfg.categories = {**CATEGORIES, **cats}

    cfg.dates = _choice(d, "dates", DATES_MODES, "shift")
    cfg.uids = _choice(d, "uids", UID_MODES, "regenerate")
    cfg.free_text = _choice(d, "free_text", FREE_TEXT_MODES, "remove")
    cfg.on_burned_in_annotation = _choice(d, "on_burned_in_annotation", BURNED_IN_MODES, "flag")

    for key in ("keep_tags", "extra_remove_tags"):
        v = d.get(key, [])
        if not isinstance(v, list):
            raise ConfigError(f"'{key}' must be a list")
        setattr(cfg, key, [parse_tag(x) for x in v])
    both = set(cfg.keep_tags) & set(cfg.extra_remove_tags)
    if both:
        raise ConfigError("tag(s) listed in both keep_tags and extra_remove_tags: "
                          + ", ".join(f"({t >> 16:04X},{t & 0xFFFF:04X})" for t in sorted(both)))

    from .pixels import MaskError, parse_templates
    try:
        cfg.pixel_masks = parse_templates(d.get("pixel_masks", []))
    except MaskError as e:
        raise ConfigError(str(e)) from None

    w = d.get("workers", 4)
    if not isinstance(w, int) or w < 1:
        raise ConfigError("'workers' must be a positive integer")
    cfg.workers = w
    ow = d.get("overwrite", False)
    if not isinstance(ow, bool):
        raise ConfigError("'overwrite' must be true or false")
    cfg.overwrite = ow

    cfg.salt = salt if salt is not None else os.environ.get(SALT_ENV)
    if cfg.needs_salt and not cfg.salt:
        raise ConfigError(
            f"uids='regenerate' or dates='shift' needs a secret salt: set the environment "
            f"variable {SALT_ENV} (keep it with the mapping file, never in environment.json)")
    return cfg


def load(path: str | Path, **kw) -> Config:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"settings file not found: {p}")
    try:
        d = json.loads(p.read_text(encoding="utf8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{p.name} is not valid JSON (line {e.lineno}, column {e.colno}): {e.msg}")
    return from_dict(d, **kw)
