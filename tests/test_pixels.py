"""Phase 2: pixel masking, preview and pixel checks (synthetic data only)."""
from __future__ import annotations

import json

import pytest
from pydicom.uid import ExplicitVRLittleEndian, RLELossless

from dicompseudo.check import Checker
from dicompseudo.config import ConfigError, from_dict
from dicompseudo.io import run
from dicompseudo.mapping import Mapping
from dicompseudo.pixels import apply_mask, find_template, parse_templates, rectangles_are_black
from dicompseudo.preview import build

import make_synthetic as syn

SALT = "test-salt"
US_MASK = {"name": "fake_us_top_band",
           "match": {"Modality": "US", "Manufacturer": "FakeVendor", "Rows": 64, "Columns": 80},
           "rectangles": [[0, 0, 80, 10]]}


def us(frames=3, colour=True):
    return syn.with_pixels(syn.synthetic_dataset("US"), frames=frames, colour=colour)


# ------------------------------------------------------------------ templates
def test_template_validation_messages():
    with pytest.raises(ConfigError, match="rectangles"):
        from_dict({"pixel_masks": [{"name": "a", "match": {"Modality": "US"}, "rectangles": []}]},
                  require_paths=False, salt=SALT)
    with pytest.raises(ConfigError, match="extends past Columns"):
        from_dict({"pixel_masks": [{"name": "a", "match": {"Columns": 80},
                                    "rectangles": [[70, 0, 20, 5]]}]},
                  require_paths=False, salt=SALT)
    with pytest.raises(ConfigError, match="cannot match on"):
        from_dict({"pixel_masks": [{"name": "a", "match": {"PatientName": "x"},
                                    "rectangles": [[0, 0, 1, 1]]}]},
                  require_paths=False, salt=SALT)
    with pytest.raises(ConfigError, match="duplicate name"):
        from_dict({"pixel_masks": [US_MASK, US_MASK]}, require_paths=False, salt=SALT)


def test_template_matching():
    (t,) = parse_templates([US_MASK])
    assert find_template(us(), [t]) is t
    assert find_template(syn.with_pixels(syn.synthetic_dataset("MR")), [t]) is None
    other = us()
    other.Rows = 65
    assert find_template(other, [t]) is None


# ------------------------------------------------------------------ masking
@pytest.mark.parametrize("frames, colour", [(1, False), (3, False), (1, True), (4, True)])
def test_mask_blackens_only_rectangles(frames, colour):
    (t,) = parse_templates([US_MASK])
    ds = us(frames, colour)
    apply_mask(ds, t)
    arr = ds.pixel_array
    band = arr[..., :10, :, :] if colour else arr[..., :10, :]
    rest = arr[..., 10:, :, :] if colour else arr[..., 10:, :]
    assert (band == 0).all() and (rest == 100).all()
    assert ds.BurnedInAnnotation == "NO"
    assert ds.file_meta.TransferSyntaxUID == ExplicitVRLittleEndian
    assert rectangles_are_black(ds, t)


def test_mask_on_compressed_input_writes_uncompressed():
    (t,) = parse_templates([US_MASK])
    ds = us(2, False)
    ds.compress(RLELossless, generate_instance_uid=False)
    assert ds.file_meta.TransferSyntaxUID == RLELossless
    apply_mask(ds, t)
    assert ds.file_meta.TransferSyntaxUID == ExplicitVRLittleEndian
    assert (ds.pixel_array[:, :10] == 0).all()


def test_monochrome1_black_is_max():
    (t,) = parse_templates([US_MASK])
    ds = us(1, False)
    ds.PhotometricInterpretation = "MONOCHROME1"
    apply_mask(ds, t)
    assert (ds.pixel_array[:10] == 255).all()


def test_clears_burned_in_false_keeps_flag():
    (t,) = parse_templates([dict(US_MASK, clears_burned_in=False)])
    ds = us()
    ds.BurnedInAnnotation = "YES"
    apply_mask(ds, t)
    assert ds.BurnedInAnnotation == "YES"
    assert "113101" in [i.CodeValue for i in ds.DeidentificationMethodCodeSequence]


# ------------------------------------------------------------------ in a run
@pytest.fixture
def us_study(tmp_path):
    inp = tmp_path / "input"
    files = [("Erika_Testperson", "US"), ("Erika_Testperson", "US"), ("Max_Fakeman", "MR")]
    for i, (folder, modality) in enumerate(files, 1):
        ds = syn.with_pixels(syn.synthetic_dataset(modality),
                             frames=2 if modality == "US" else 1, colour=modality == "US")
        uid = f"{syn.FAKE_SOP_UID}.{i}"
        ds.SOPInstanceUID = uid
        ds.file_meta.MediaStorageSOPInstanceUID = uid
        ds.InstanceNumber = i
        p = inp / folder / f"f{i}"
        p.parent.mkdir(parents=True, exist_ok=True)
        ds.save_as(p, enforce_file_format=True)
    keys = tmp_path / "keys"
    keys.mkdir()
    m = keys / "participants.csv"
    m.write_text("source_key,experimental_number,nickname\n"
                 "Erika_Testperson,JRF001,Owl\nMax_Fakeman,JRF002,Fox\n", encoding="utf8")
    return tmp_path, inp, m


def cfg_for(tmp_path, inp, m, out="output", **kw):
    return from_dict({"input_dir": str(inp), "output_dir": str(tmp_path / out),
                      "mapping_file": str(m), "workers": 1, **kw}, salt=SALT)


def test_run_masks_matching_files_and_check_verifies(us_study):
    tmp_path, inp, m = us_study
    cfg = cfg_for(tmp_path, inp, m, pixel_masks=[US_MASK], on_burned_in_annotation="skip")
    mapping = Mapping.load(m)
    rr = run(cfg, mapping)
    assert rr.status_counts == {"written": 3}           # masked US files are not skipped
    assert rr.masks == {"fake_us_top_band": 2}
    assert rr.burned_in == {"masked": 2, "no": 1}
    res = Checker(cfg, mapping).run(cfg.output_dir)
    assert res.ok, dict(res.leaks)
    assert not any("burned-in" in w for w in res.warnings)


def test_unmasked_us_is_skipped(us_study):
    tmp_path, inp, m = us_study
    rr = run(cfg_for(tmp_path, inp, m, on_burned_in_annotation="skip"), Mapping.load(m))
    assert rr.status_counts == {"written": 1, "skipped_burned_in": 2}


def test_check_detects_unmasked_pixels(us_study):
    tmp_path, inp, m = us_study
    cfg_nomask = cfg_for(tmp_path, inp, m)
    run(cfg_nomask, Mapping.load(m))
    cfg_mask = cfg_for(tmp_path, inp, m, pixel_masks=[US_MASK])
    res = Checker(cfg_mask, Mapping.load(m)).run(cfg_nomask.output_dir)
    assert any("pixel mask 'fake_us_top_band' not applied" in k for k in res.leaks)


# ------------------------------------------------------------------ preview
def test_preview_is_private_and_writes_skeleton(us_study):
    tmp_path, inp, m = us_study
    out, summary = build(cfg_for(tmp_path, inp, m))
    assert out == tmp_path / "keys" / "dicompseudo_audit" / "preview"
    assert len(summary) == 1 and summary[0]["template"] is None     # only the US device
    assert len(list(out.glob("sig*.png"))) == 2
    sk = json.loads((out / "pixel_masks_skeleton.json").read_text(encoding="utf8"))
    assert sk[0]["match"] == {"Modality": "US", "Manufacturer": "FakeVendor",
                              "Rows": 64, "Columns": 80}
    assert (out / "index.html").exists()
    out, summary = build(cfg_for(tmp_path, inp, m, pixel_masks=[US_MASK]), include_all=True)
    assert {s["template"] for s in summary} == {"fake_us_top_band", None}
