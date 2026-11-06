"""Write the audit log for a run into the private audit_dir.

<audit_dir>/run_<timestamp>.json       settings, versions, counts (no DICOM values)
<audit_dir>/run_<timestamp>_files.csv  input path -> output path, status, burned-in flag

The files CSV lists ORIGINAL input paths, which may contain names: audit_dir is private,
like the mapping file, and must never be shared together with the output.
"""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from . import __version__
from .config import Config
from .io import RunResult
from .rules import meta


def config_summary(cfg: Config) -> dict:
    d = asdict(cfg)
    d.pop("salt", None)                       # never written anywhere
    for k in ("input_dir", "output_dir", "mapping_file", "audit_dir"):
        d[k] = str(d[k]) if d[k] else None
    d["keep_tags"] = [f"({t >> 16:04X},{t & 0xFFFF:04X})" for t in cfg.keep_tags]
    d["extra_remove_tags"] = [f"({t >> 16:04X},{t & 0xFFFF:04X})" for t in cfg.extra_remove_tags]
    return d


def write(cfg: Config, rr: RunResult) -> tuple[Path, Path]:
    cfg.audit_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = cfg.audit_dir / f"run_{stamp}.json"
    files = cfg.audit_dir / f"run_{stamp}_files.csv"
    conf = config_summary(cfg)
    m = meta()
    doc = {
        "tool": f"dicompseudo {__version__}",
        "rules": {"standard": "DICOM PS3.15", "edition": m["edition"], "table": m["table"]},
        "timestamp": stamp,
        "config_sha256": hashlib.sha256(json.dumps(conf, sort_keys=True).encode()).hexdigest(),
        "config": conf,
        "status_counts": dict(rr.status_counts),
        "files_per_participant": dict(sorted(rr.per_participant.items())),
        "burned_in": dict(rr.burned_in),
        "actions": {f"{c} {a}": n for (c, a), n in sorted(rr.actions.items())},
        "error_types": dict(sorted(
            {r.error_type: sum(1 for x in rr.results if x.error_type == r.error_type)
             for r in rr.results if r.error_type}.items())),
    }
    summary.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf8")
    with files.open("w", newline="", encoding="utf8") as f:
        w = csv.writer(f)
        w.writerow(["input_relpath", "experimental_number", "output_relpath", "status",
                    "burned_in", "error_type"])
        for r in rr.results:
            w.writerow([r.input_rel, r.experimental_number, r.target_rel, r.status,
                        r.burned_in, r.error_type])
    return summary, files
