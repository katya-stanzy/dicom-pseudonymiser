"""dicompseudo: configurable DICOM pseudonymisation (PS3.15 Annex E).

Every command prints settings, counts, tag names and pseudonymous output paths only,
never DICOM values or original paths.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .config import SALT_ENV, ConfigError, load
from .mapping import Mapping, MappingError
from .rules import meta


def _err(msg: str) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return 2


def _load(args, *, need_salt: bool):
    salt = None if need_salt else (os.environ.get(SALT_ENV) or "not-needed")
    cfg = load(args.config, salt=salt)
    mapping = Mapping.load(cfg.mapping_file)
    if need_salt:
        cfg.check_salt(mapping)
    return cfg, mapping


def _table(title: str, counter, limit: int | None = None) -> None:
    print(f"\n{title}")
    items = counter.most_common(limit)
    if not items:
        print("  (none)")
    for k, n in items:
        print(f"  {n:>7}  {k}")


# ------------------------------------------------------------------ commands
def cmd_validate(args) -> int:
    try:
        cfg, mapping = _load(args, need_salt=True)
        cfg.check_paths()
    except (ConfigError, MappingError) as e:
        return _err(e)
    m = meta()
    print(f"dicompseudo {__version__}  |  rules: PS3.15 {m['edition']} ({m['n_rows']} attributes)")
    print(f"input_dir : {cfg.input_dir}")
    print(f"output_dir: {cfg.output_dir}")
    print(f"audit_dir : {cfg.audit_dir}  (private: lists original paths)")
    print(f"mapping   : {len(mapping)} participants, matched by {cfg.match_participant_by}")
    print("replace   : " + ", ".join(f"{k} <- {v}" for k, v in cfg.replace.items()))
    on = [k for k, v in cfg.categories.items() if v]
    off = [k for k, v in cfg.categories.items() if not v]
    print(f"remove    : {', '.join(on) or '-'}")
    print(f"keep      : {', '.join(off) or '-'}")
    print(f"dates={cfg.dates}  uids={cfg.uids}  free_text={cfg.free_text}  "
          f"burned_in={cfg.on_burned_in_annotation}  workers={cfg.workers}")
    print(f"keep_tags: {len(cfg.keep_tags)}  extra_remove_tags: {len(cfg.extra_remove_tags)}")
    if cfg.dates == "shift":
        n_own = sum(1 for p in mapping.participants() if p.date_shift_days is not None)
        print(f"date shift: {n_own} participant(s) from date_shift_days, "
              f"{len(mapping) - n_own} derived from the salt")
    print(f"salt      : {'set' if cfg.salt else 'not set (not needed with these settings)'}")
    print("OK")
    return 0


def cmd_inspect(args) -> int:
    from .survey import survey
    try:
        cfg, mapping = _load(args, need_salt=False)
        cfg.check_paths()
    except (ConfigError, MappingError) as e:
        return _err(e)
    s = survey(cfg, mapping)
    print(f"files: {s.files}   DICOM: {s.dicom}   unreadable: {s.errors}")
    print(f"matched to a participant: {s.matched}   unmatched: {s.unmatched}")
    _table("Files containing each category (non-empty values):", s.category_files)
    _table("Burned-in annotation:", s.burned_in)
    _table("Device signatures (Modality | Manufacturer | Model | Rows x Columns | BurnedIn):",
           s.devices)
    if args.tags:
        _table("Sensitive tags present (files):", s.tag_files)
    else:
        print("\n(add --tags for the per-tag list)")
    return 0


def cmd_run(args) -> int:
    from . import audit
    from .io import run
    try:
        cfg, mapping = _load(args, need_salt=True)
    except (ConfigError, MappingError) as e:
        return _err(e)

    def progress(i, n, _last=[-1]):
        pct = int(100 * i / max(n, 1))
        if pct // 5 != _last[0]:
            _last[0] = pct // 5
            print(f"\r  {i}/{n} files ({pct}%)", end="", flush=True)

    try:
        rr = run(cfg, mapping, progress=progress)
    except ConfigError as e:
        print()
        return _err(e)
    print()
    summary, files = audit.write(cfg, rr)
    _table("Files by status:", rr.status_counts)
    _table("Files written per participant:", rr.per_participant)
    _table("Burned-in annotation (written files):", rr.burned_in)
    if rr.masks:
        _table("Pixel mask templates applied (files):", rr.masks)
    print(f"\naudit summary: {summary}\naudit files  : {files}  (private)")
    if rr.burned_in.get("yes") or rr.burned_in.get("possible"):
        print("NOTE: some written files may have text burned into the pixels; "
              "check them visually (phase 2 adds masking).")
    print(f"\nnext: dicompseudo check \"{cfg.output_dir}\" --config {args.config} "
          f"--mapping \"{cfg.mapping_file}\" [--terms <private words file>]")
    return 1 if rr.status_counts.get("error") else 0


def cmd_preview(args) -> int:
    from .preview import build
    try:
        cfg, _ = _load(args, need_salt=False)
        cfg.check_paths()
    except (ConfigError, MappingError) as e:
        return _err(e)
    try:
        out, summary = build(cfg, args.n, args.all)
    except ImportError:
        return _err("preview needs numpy and pillow: conda env update -f environment.yml")
    print("Device signatures previewed:")
    if not summary:
        print("  (none: no file may carry burned-in text; use --all to preview every device)")
    for s in summary:
        print(f"  sig{s['index']:02d}  {s['files']:>6} files  burned-in: {','.join(s['burned_in']):<14}"
              f" template: {s['template'] or 'NONE'}  {json.dumps(s['signature'])}")
    print(f"\npreview (PRIVATE, shows original pixels): {out / 'index.html'}")
    print(f"template skeleton for devices without one : {out / 'pixel_masks_skeleton.json'}")
    return 0


def cmd_check(args) -> int:
    from .check import Checker, load_terms
    try:
        cfg = load(args.config, require_paths=False,
                   salt=os.environ.get(SALT_ENV) or "not-needed") if args.config else None
        mapping = Mapping.load(args.mapping) if args.mapping else None
        terms = load_terms(args.terms)
    except (ConfigError, MappingError, OSError) as e:
        return _err(e)
    from pathlib import Path
    folder = Path(args.output_dir)
    if not folder.is_dir():
        return _err(f"folder not found: {folder}")
    res = Checker(cfg, mapping, terms).run(folder)
    print(f"files: {res.files}   DICOM: {res.dicom}")
    print(f"settings: {'from ' + args.config if cfg else 'defaults'}   "
          f"mapping: {'yes' if mapping else 'no'}   terms: {len(terms)}")
    _table("LEAKS (files):", res.leaks)
    _table("Warnings (files):", res.warnings)
    print("\nRESULT: " + ("OK, no leaks found" if res.ok else "LEAKS FOUND"))
    return 0 if res.ok else 1


# ------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dicompseudo", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("validate", help="check environment.json and the mapping file")
    sp.add_argument("--config", default="environment.json")
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("inspect", help="survey sensitive tags in input_dir (counts only)")
    sp.add_argument("--config", default="environment.json")
    sp.add_argument("--tags", action="store_true", help="also list every sensitive tag found")
    sp.set_defaults(func=cmd_inspect)

    sp = sub.add_parser("run", help="pseudonymise input_dir into output_dir")
    sp.add_argument("--config", default="environment.json")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("preview", help="private before/after images for drawing pixel masks")
    sp.add_argument("--config", default="environment.json")
    sp.add_argument("--n", type=int, default=3, help="sample images per device (default 3)")
    sp.add_argument("--all", action="store_true", help="preview every device, not only risky ones")
    sp.set_defaults(func=cmd_preview)

    sp = sub.add_parser("check", help="leak scan of a pseudonymised folder")
    sp.add_argument("output_dir")
    sp.add_argument("--config", help="settings used for the run (kept tags are not leaks)")
    sp.add_argument("--mapping", help="participants.csv: original keys must not appear")
    sp.add_argument("--terms", help="private text file, one word per line, to search for")
    sp.set_defaults(func=cmd_check)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
