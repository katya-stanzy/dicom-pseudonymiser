"""Folder processing (step 5) and leak check (step 6), synthetic data only."""
from __future__ import annotations

import hashlib
import json

import pytest
from pydicom import dcmread

from dicompseudo import audit
from dicompseudo.check import Checker
from dicompseudo.cli import main
from dicompseudo.config import ConfigError, from_dict
from dicompseudo.io import run
from dicompseudo.mapping import Mapping
from dicompseudo.survey import survey

import make_synthetic as syn

SALT = "test-salt"
TERMS = ["Testperson", "Erika", "Fake Imaging Lab", "Faketown"]


def write_file(path, *, sop_suffix, instance, series=1, modality="MR", **extra):
    ds = syn.synthetic_dataset(modality)
    uid = f"{syn.FAKE_SOP_UID}.{sop_suffix}"
    ds.SOPInstanceUID = uid
    ds.file_meta.MediaStorageSOPInstanceUID = uid
    ds.InstanceNumber = instance
    ds.SeriesNumber = series
    ds.file_meta.SourceApplicationEntityTitle = "FAKELABAE"
    for k, v in extra.items():
        setattr(ds, k, v)
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.save_as(path, enforce_file_format=True)


@pytest.fixture
def study(tmp_path):
    """input/ with two participants (folder names = fake real names), noise and a US file."""
    inp = tmp_path / "input"
    write_file(inp / "Erika_Testperson" / "MRI knee" / "IM0001", sop_suffix=1, instance=1)
    write_file(inp / "Erika_Testperson" / "MRI knee" / "IM0002", sop_suffix=2, instance=2)
    write_file(inp / "Erika_Testperson" / "MRI knee" / "IM0002b", sop_suffix=3, instance=2)  # dup
    write_file(inp / "Max_Fakeman" / "scan.dcm", sop_suffix=4, instance=1, PatientID="FAKE-777")
    write_file(inp / "Max_Fakeman" / "us.dcm", sop_suffix=5, instance=1, series=2, modality="US")
    write_file(inp / "Unknown_Person" / "x.dcm", sop_suffix=6, instance=1)
    (inp / "Max_Fakeman" / "notes.txt").write_text("not a dicom", encoding="utf8")
    keys = tmp_path / "keys"
    keys.mkdir()
    mapping_file = keys / "participants.csv"
    mapping_file.write_text("source_key,experimental_number,nickname\n"
                            "Erika_Testperson,JRF001,Owl\nMax_Fakeman,JRF002,Fox\n",
                            encoding="utf8")
    terms = keys / "terms.txt"
    terms.write_text("\n".join(TERMS) + "\n", encoding="utf8")
    return tmp_path, inp, mapping_file, terms


def make_cfg(tmp_path, inp, mapping_file, **settings):
    d = {"input_dir": str(inp), "output_dir": str(tmp_path / "output"),
         "mapping_file": str(mapping_file), "workers": 1, **settings}
    return from_dict(d, salt=SALT)


def tree_hash(root):
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


# ------------------------------------------------------------------ step 5
@pytest.mark.parametrize("workers", [1, 2])
def test_run_layout_and_counts(study, workers):
    tmp_path, inp, mapping_file, _ = study
    before = tree_hash(inp)
    cfg = make_cfg(tmp_path, inp, mapping_file, workers=workers)
    rr = run(cfg, Mapping.load(mapping_file))
    assert rr.status_counts == {"written": 5, "unmatched": 1, "not_dicom": 1}
    assert rr.per_participant == {"JRF001": 3, "JRF002": 2}
    assert tree_hash(inp) == before                         # input untouched
    out = cfg.output_dir
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*.dcm"))
    assert len(files) == 5
    assert all(f.split("/")[0] in ("JRF001", "JRF002") for f in files)
    assert not any("Erika" in f or "Fakeman" in f or "IM000" in f for f in files)
    assert any(f.endswith("00002_1.dcm") for f in files)    # duplicate instance number
    assert not (out / ".dicompseudo_staging").exists()


def test_file_meta_ae_title_removed(study):
    tmp_path, inp, mapping_file, _ = study
    cfg = make_cfg(tmp_path, inp, mapping_file)
    run(cfg, Mapping.load(mapping_file))
    f = next(cfg.output_dir.rglob("*.dcm"))
    assert "SourceApplicationEntityTitle" not in dcmread(f).file_meta


def test_skip_burned_in(study):
    tmp_path, inp, mapping_file, _ = study
    cfg = make_cfg(tmp_path, inp, mapping_file, on_burned_in_annotation="skip")
    rr = run(cfg, Mapping.load(mapping_file))
    assert rr.status_counts["skipped_burned_in"] == 1 and rr.status_counts["written"] == 4


def test_match_by_patient_id(study):
    tmp_path, inp, _, _ = study
    m = tmp_path / "keys" / "by_id.csv"
    m.write_text("source_key,experimental_number,nickname\nFAKE-777,JRF009,Lynx\n", encoding="utf8")
    cfg = make_cfg(tmp_path, inp, m, match_participant_by="PatientID")
    rr = run(cfg, Mapping.load(m))
    assert rr.per_participant == {"JRF009": 1}      # only scan.dcm carries FAKE-777
    assert rr.status_counts["unmatched"] == 5


def test_refuses_non_empty_output_and_nested_paths(study):
    tmp_path, inp, mapping_file, _ = study
    cfg = make_cfg(tmp_path, inp, mapping_file)
    cfg.output_dir.mkdir()
    (cfg.output_dir / "old.txt").write_text("x", encoding="utf8")
    with pytest.raises(ConfigError, match="not empty"):
        run(cfg, Mapping.load(mapping_file))
    bad = from_dict({"input_dir": str(inp), "output_dir": str(inp / "out"),
                     "mapping_file": str(mapping_file)}, salt=SALT)
    with pytest.raises(ConfigError, match="contain each other"):
        bad.check_paths()
    bad = from_dict({"input_dir": str(inp), "output_dir": str(tmp_path / "o"),
                     "mapping_file": str(mapping_file), "audit_dir": str(tmp_path / "o" / "a")},
                    salt=SALT)
    with pytest.raises(ConfigError, match="audit_dir"):
        bad.check_paths()


def test_audit_written_privately_without_salt(study):
    tmp_path, inp, mapping_file, _ = study
    cfg = make_cfg(tmp_path, inp, mapping_file)
    rr = run(cfg, Mapping.load(mapping_file))
    summary, files = audit.write(cfg, rr)
    assert summary.parent == tmp_path / "keys" / "dicompseudo_audit"
    doc = json.loads(summary.read_text(encoding="utf8"))
    assert SALT not in summary.read_text(encoding="utf8")
    assert doc["status_counts"]["written"] == 5 and doc["rules"]["edition"]
    assert len(files.read_text(encoding="utf8").splitlines()) == 1 + 7


def test_survey_counts(study):
    tmp_path, inp, mapping_file, _ = study
    cfg = make_cfg(tmp_path, inp, mapping_file)
    s = survey(cfg, Mapping.load(mapping_file))
    assert s.dicom == 6 and s.matched == 5 and s.unmatched == 1
    assert s.category_files["institution"] == 6 and s.category_files["private_tags"] == 6
    assert s.burned_in["possible"] == 1


# ------------------------------------------------------------------ step 6
def test_check_clean_output(study):
    tmp_path, inp, mapping_file, terms = study
    cfg = make_cfg(tmp_path, inp, mapping_file)
    mapping = Mapping.load(mapping_file)
    run(cfg, mapping)
    res = Checker(cfg, mapping, TERMS).run(cfg.output_dir)
    assert res.ok, dict(res.leaks)
    assert res.warnings  # the US file is flagged as possible burned-in text


def test_check_finds_planted_leaks(study):
    tmp_path, inp, mapping_file, _ = study
    cfg = make_cfg(tmp_path, inp, mapping_file)
    mapping = Mapping.load(mapping_file)
    run(cfg, mapping)
    f = next(cfg.output_dir.rglob("*.dcm"))
    ds = dcmread(f)
    ds.InstitutionName = "Fake Imaging Lab"
    ds.PatientWeight = "61.5"
    ds.ImageComments = "seen by Erika_Testperson"
    blk = ds.private_block(0x0031, "FAKE", create=True)
    blk.add_new(0x01, "LO", "x")
    ds.save_as(f, enforce_file_format=True)
    res = Checker(cfg, mapping, TERMS).run(cfg.output_dir)
    found = " | ".join(res.leaks)
    assert "(0008,0080)" in found and "[institution]" in found
    assert "(0010,1030)" in found
    assert "private tag present" in found
    assert "terms line 3" in found                      # 'Fake Imaging Lab'
    assert "mapping row 2" in found                      # original folder name
    assert "Erika" not in found and "Fake Imaging" not in found   # never values


def test_check_kept_free_text_still_caught_by_terms(study):
    tmp_path, inp, mapping_file, _ = study
    cfg = make_cfg(tmp_path, inp, mapping_file, free_text="keep")
    mapping = Mapping.load(mapping_file)
    run(cfg, mapping)
    res = Checker(cfg, mapping, TERMS).run(cfg.output_dir)
    assert any("terms line 1" in k for k in res.leaks)   # StudyDescription names her


def test_check_token_matching_avoids_false_hits():
    from dicompseudo.check import _token
    assert _token("003").search("jrf 003 x")
    assert not _token("003").search("jrf003")
    assert not _token("ann").search("annotation")


def test_cli_run_and_check(study, monkeypatch, capsys):
    tmp_path, inp, mapping_file, terms = study
    settings = tmp_path / "environment.json"
    settings.write_text(json.dumps({"input_dir": str(inp), "output_dir": str(tmp_path / "output"),
                                    "mapping_file": str(mapping_file), "workers": 1}),
                        encoding="utf8")
    monkeypatch.setenv("DICOMPSEUDO_SALT", SALT)
    assert main(["validate", "--config", str(settings)]) == 0
    assert main(["inspect", "--config", str(settings), "--tags"]) == 0
    assert main(["run", "--config", str(settings)]) == 0
    assert main(["check", str(tmp_path / "output"), "--config", str(settings),
                 "--mapping", str(mapping_file), "--terms", str(terms)]) == 0
    out = capsys.readouterr().out
    for leak in TERMS + ["Max_Fakeman", syn.FAKE_ID]:
        assert leak not in out, leak
