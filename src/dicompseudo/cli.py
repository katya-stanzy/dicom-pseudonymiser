"""Command-line interface. Prints settings, counts and tag names, never DICOM values."""
from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import ConfigError, load
from .mapping import Mapping, MappingError
from .rules import meta


def cmd_validate(args) -> int:
    try:
        cfg = load(args.config)
        mapping = Mapping.load(cfg.mapping_file)
    except (ConfigError, MappingError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    m = meta()
    print(f"dicompseudo {__version__}  |  rules: PS3.15 {m['edition']} ({m['n_rows']} attributes)")
    print(f"input_dir : {cfg.input_dir}  (exists: {cfg.input_dir.is_dir()})")
    print(f"output_dir: {cfg.output_dir}")
    print(f"mapping   : {len(mapping)} participants, matched by {cfg.match_participant_by}")
    print(f"replace   : " + ", ".join(f"{k} <- {v}" for k, v in cfg.replace.items()))
    on = [k for k, v in cfg.categories.items() if v]
    off = [k for k, v in cfg.categories.items() if not v]
    print(f"remove    : {', '.join(on) or '-'}")
    print(f"keep      : {', '.join(off) or '-'}")
    print(f"dates={cfg.dates}  uids={cfg.uids}  free_text={cfg.free_text}  "
          f"burned_in={cfg.on_burned_in_annotation}")
    print(f"keep_tags: {len(cfg.keep_tags)}  extra_remove_tags: {len(cfg.extra_remove_tags)}")
    if cfg.output_dir and cfg.input_dir and cfg.output_dir.resolve() == cfg.input_dir.resolve():
        print("ERROR: output_dir must differ from input_dir", file=sys.stderr)
        return 2
    print("OK")
    return 0


def _not_yet(step: str):
    def f(args) -> int:
        print(f"not implemented yet (planned in {step}, see PLAN.md)", file=sys.stderr)
        return 3
    return f


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dicompseudo", description=__doc__)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    for name, fn, help_ in [
        ("validate", cmd_validate, "check environment.json and the mapping file"),
        ("inspect", _not_yet("step 5"), "list sensitive tags present (counts only)"),
        ("run", _not_yet("step 5"), "pseudonymise input_dir into output_dir"),
    ]:
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--config", default="environment.json")
        sp.set_defaults(func=fn)
    sp = sub.add_parser("check", help="leak scan of an output folder (step 6)")
    sp.add_argument("output_dir")
    sp.add_argument("--mapping")
    sp.set_defaults(func=_not_yet("step 6"))
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
