"""Find DICOM files, process them in parallel and write the pseudonymised tree.

Output layout (original file and folder names never reach the output):
    <output_dir>/<experimental_number>/study_<hash8>/<SeriesNumber>_<Modality>/<InstanceNumber>.dcm

Workers write into a staging folder; the parent process renames into place, so names
never collide between workers. The input folder is only ever read.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from pathlib import Path

from pydicom import dcmread
from pydicom.errors import InvalidDicomError

from .config import Config, ConfigError
from .engine import Pseudonymiser
from .mapping import Mapping, Participant

STAGING = ".dicompseudo_staging"


# --------------------------------------------------------------------- discovery
def is_dicom(path: Path) -> bool:
    """True for Part 10 files (128-byte preamble + 'DICM'); the extension is ignored."""
    try:
        with path.open("rb") as f:
            head = f.read(132)
        return len(head) == 132 and head[128:132] == b"DICM"
    except OSError:
        return False


def iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != STAGING)
        for name in sorted(filenames):
            yield Path(dirpath) / name


def participant_for(rel: Path, ds_patient_id: str | None, cfg: Config,
                    mapping: Mapping) -> Participant | None:
    if cfg.match_participant_by == "folder":
        return mapping.get(rel.parts[0]) if len(rel.parts) > 1 else None
    return mapping.get(ds_patient_id or "")


def _h(text: str, n: int) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:n]


def output_relpath(ds, p: Participant) -> Path:
    study = f"study_{_h(str(ds.get('StudyInstanceUID', '')), 8)}"
    mod = "".join(c for c in str(ds.get("Modality", "") or "XX") if c.isalnum()) or "XX"
    try:
        series = f"{int(ds.SeriesNumber):04d}_{mod}"
    except (AttributeError, TypeError, ValueError):
        series = f"s{_h(str(ds.get('SeriesInstanceUID', '')), 6)}_{mod}"
    try:
        inst = f"{int(ds.InstanceNumber):05d}"
    except (AttributeError, TypeError, ValueError):
        inst = f"i{_h(str(ds.get('SOPInstanceUID', '')), 10)}"
    safe_num = "".join(c if c.isalnum() or c in "-_" else "_" for c in p.experimental_number)
    return Path(safe_num) / study / series / f"{inst}.dcm"


# --------------------------------------------------------------------- worker
@dataclass
class FileResult:
    input_rel: str
    status: str                       # written | not_dicom | dicomdir | unmatched |
                                      # skipped_burned_in | error
    experimental_number: str = ""
    target_rel: str = ""
    staged: str = ""
    burned_in: str = ""
    error_type: str = ""
    actions: Counter = field(default_factory=Counter)


def process_one(path_str: str, input_dir: str, staging: str, cfg: Config,
                participants: dict[str, Participant]) -> FileResult:
    path = Path(path_str)
    rel = path.relative_to(input_dir)
    res = FileResult(input_rel=rel.as_posix(), status="error")
    if not is_dicom(path):
        res.status = "not_dicom"
        return res
    try:
        ds = dcmread(path)
        if ds.file_meta.get("MediaStorageSOPClassUID") == "1.2.840.10008.1.3.10":
            res.status = "dicomdir"           # references original paths: never copied
            return res
        mapping = Mapping(list(participants.values()))
        p = participant_for(rel, str(ds.get("PatientID", "") or ""), cfg, mapping)
        if p is None:
            res.status = "unmatched"
            return res
        res.experimental_number = p.experimental_number
        eng = Pseudonymiser(cfg)
        res.burned_in = eng.burned_in_status(ds)
        if res.burned_in != "no" and cfg.on_burned_in_annotation == "skip":
            res.status = "skipped_burned_in"
            return res
        rep = eng.process(ds, p)
        res.actions = rep.actions
        res.target_rel = output_relpath(ds, p).as_posix()
        staged = Path(staging) / f"{uuid.uuid4().hex}.dcm"
        ds.save_as(staged, enforce_file_format=True)
        res.staged = str(staged)
        res.status = "written"
    except (InvalidDicomError, Exception) as e:   # never report the message: may hold values
        res.status = "error"
        res.error_type = type(e).__name__
    return res


# --------------------------------------------------------------------- run
@dataclass
class RunResult:
    results: list[FileResult]
    status_counts: Counter
    actions: Counter
    burned_in: Counter
    per_participant: Counter


def _place(res: FileResult, out: Path, claimed: set[str], overwrite: bool) -> None:
    target = Path(res.target_rel)
    stem, n = target.with_suffix(""), 1
    key = target.as_posix()
    while key in claimed or (not overwrite and (out / key).exists()):
        key = f"{stem.as_posix()}_{n}.dcm"
        n += 1
    claimed.add(key)
    dest = out / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(res.staged, dest)
    res.target_rel = key


def run(cfg: Config, mapping: Mapping, progress=None) -> RunResult:
    cfg.check_paths()
    out = cfg.output_dir
    out.mkdir(parents=True, exist_ok=True)
    if not cfg.overwrite and any(p.name != STAGING for p in out.iterdir()):
        raise ConfigError("output_dir is not empty; empty it or set \"overwrite\": true")
    staging = out / STAGING
    staging.mkdir(exist_ok=True)

    participants = {p.source_key: p for p in mapping.participants()}
    files = [str(f) for f in iter_files(cfg.input_dir)]
    results: list[FileResult] = []
    claimed: set[str] = set()
    args = (str(cfg.input_dir), str(staging), cfg, participants)
    try:
        if cfg.workers == 1:
            for i, f in enumerate(files, 1):
                r = process_one(f, *args)
                if r.status == "written":
                    _place(r, out, claimed, cfg.overwrite)
                results.append(r)
                if progress:
                    progress(i, len(files))
        else:
            with ProcessPoolExecutor(max_workers=cfg.workers) as ex:
                futs = [ex.submit(process_one, f, *args) for f in files]
                for i, fut in enumerate(as_completed(futs), 1):
                    try:
                        r = fut.result()
                    except BrokenProcessPool:
                        raise ConfigError(
                            "parallel workers failed to start. When calling run() from your "
                            "own script, put the call under  if __name__ == \"__main__\":  "
                            "(Windows requirement), or set \"workers\": 1. In a notebook use "
                            "\"workers\": 1.") from None
                    if r.status == "written":
                        _place(r, out, claimed, cfg.overwrite)
                    results.append(r)
                    if progress:
                        progress(i, len(files))
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    results.sort(key=lambda r: r.input_rel)
    actions, burned, per_p = Counter(), Counter(), Counter()
    for r in results:
        actions.update(r.actions)
        if r.status == "written":
            per_p[r.experimental_number] += 1
            burned[r.burned_in] += 1
    return RunResult(results, Counter(r.status for r in results), actions, burned, per_p)
