"""Engine tests on synthetic data only."""
from __future__ import annotations

from io import BytesIO

import pytest
from pydicom import dcmread

from dicompseudo.config import from_dict
from dicompseudo.engine import Pseudonymiser
from dicompseudo.mapping import Participant, derived_shift
from dicompseudo.uids import new_uid

import make_synthetic as syn

SALT = "test-salt"
P = Participant("folder_A", "JRF003", "Owl")


def run(ds=None, **settings):
    cfg = from_dict(settings, require_paths=False, salt=SALT)
    ds = ds or syn.synthetic_dataset()
    rep = Pseudonymiser(cfg).process(ds, P)
    return ds, rep


def all_text(ds) -> str:
    """Every value in the dataset (recursively) as one string, for leak checks."""
    return " ".join(str(e.value) for e in ds.iterall() if e.VR != "SQ")


# ---------------------------------------------------------------- defaults
REMOVED = ("", None, "ANONYMIZED", "ANONYMOUS")   # X (absent), Z (empty) or D (dummy)


def test_defaults_replace_and_remove():
    ds, rep = run()
    assert ds.PatientName == "Owl"
    assert ds.PatientID == "JRF003"
    for kw in ("PatientAge", "PatientSex", "PatientSize", "PatientWeight", "PatientAddress",
               "EthnicGroup", "PatientComments", "InstitutionName", "InstitutionAddress",
               "InstitutionalDepartmentName", "StationName", "DeviceSerialNumber",
               "OtherPatientIDs"):
        assert ds.get(kw) in REMOVED, kw
    for kw in ("PatientBirthDate", "ReferringPhysicianName", "OperatorsName"):
        assert ds.get(kw) in REMOVED, kw
    assert ds.PatientIdentityRemoved == "YES"
    assert "113100" in [i.CodeValue for i in ds.DeidentificationMethodCodeSequence]


def test_no_fake_identifying_value_survives():
    ds, _ = run()
    text = all_text(ds)
    for leak in ("Testperson", "Erika", syn.FAKE_ID, syn.FAKE_INSTITUTION, "Fake Street",
                 "Fake Road", "FAKESTATION01", "SN-FAKE-0001", "Doctor^Fake",
                 "Operator^Fake", "ACC-FAKE-1", "Nested^Fake", "Evaluator^Fake",
                 "private fake value", "overlay comment fake", "19800515"):
        assert leak not in text, leak


def test_technical_attributes_survive():
    ds, _ = run()
    assert ds.Rows == 4 and ds.Columns == 4
    assert ds.Modality == "MR"
    assert str(ds.SliceThickness) == "3.0"


def test_private_and_overlays_removed():
    ds, _ = run()
    assert not any(e.tag.is_private for e in ds.iterall())
    assert not any(0x6000 <= e.tag.group <= 0x60FF for e in ds)


def test_pn_sweep_inside_sequence():
    ds, _ = run()
    item = ds.ProcedureCodeSequence[0]
    assert not item.get("EvaluatorName")             # not in the table: swept
    assert item.get("PersonName") in REMOVED
    assert item.CodeValue == "FAKE"                  # codes kept


def test_output_is_writable_and_readable():
    ds, _ = run()
    buf = BytesIO()
    ds.save_as(buf, enforce_file_format=True)
    buf.seek(0)
    back = dcmread(buf)
    assert back.PatientID == "JRF003"
    assert back.file_meta.MediaStorageSOPInstanceUID == back.SOPInstanceUID


# ---------------------------------------------------------------- switches
@pytest.mark.parametrize("category, kw", [
    ("sex", "PatientSex"), ("body_measurements", "PatientWeight"),
    ("body_measurements", "PatientSize"), ("institution", "InstitutionName"),
    ("device", "StationName"), ("staff", "OperatorsName"), ("contact", "PatientAddress"),
    ("medical_social", "EthnicGroup"), ("identifiers", "AccessionNumber"),
])
def test_category_off_keeps_value(category, kw):
    orig = syn.synthetic_dataset().get(kw)
    ds, _ = run(categories={category: False})
    assert ds.get(kw) == orig


def test_age_off_keeps_age_but_shifts_birthdate():
    ds, rep = run(categories={"age": False}, dates="shift")
    assert ds.PatientAge == "044Y"
    assert ds.PatientBirthDate and ds.PatientBirthDate != "19800515"


def test_keep_tags_overrides_category():
    ds, _ = run(keep_tags=["PatientSex", "(0010,1030)"])
    assert ds.PatientSex == "F" and str(ds.PatientWeight) == "61.5"


def test_extra_remove_tags():
    ds, _ = run(extra_remove_tags=["Manufacturer"])
    assert "Manufacturer" not in ds


def test_private_tags_switch_off():
    ds, _ = run(categories={"private_tags": False})
    assert any(e.tag.is_private for e in ds.iterall())


def test_replace_swapped_sources():
    ds, _ = run(replace={"PatientName": "experimental_number", "PatientID": "nickname"})
    assert ds.PatientName == "JRF003" and ds.PatientID == "Owl"


def test_free_text_keep():
    ds, _ = run(free_text="keep")
    assert ds.SeriesDescription == "T1 sag"
    ds, _ = run(free_text="remove")
    assert not ds.get("StudyDescription")


# ---------------------------------------------------------------- dates
def test_dates_shift_preserves_intervals_and_is_deterministic():
    ds, rep = run(dates="shift")
    shift = derived_shift(SALT, "JRF003")
    assert rep.date_shift_days == shift
    assert ds.StudyDate != "20240310" and len(ds.StudyDate) == 8
    assert ds.StudyDate == ds.SeriesDate
    assert ds.AcquisitionDateTime.endswith("101530.000000")
    assert ds.LongitudinalTemporalInformationModified == "MODIFIED"
    ds2, _ = run(dates="shift")
    assert ds2.StudyDate == ds.StudyDate


def test_dates_shift_from_mapping_column():
    cfg = from_dict({"dates": "shift"}, require_paths=False, salt=SALT)
    ds = syn.synthetic_dataset()
    Pseudonymiser(cfg).process(ds, Participant("a", "JRF009", "Fox", -10))
    assert ds.StudyDate == "20240229"


def test_dates_year_only_keep_remove():
    ds, _ = run(dates="year_only")
    assert ds.StudyDate == "20240101"
    ds, _ = run(dates="keep")
    assert ds.StudyDate == "20240310"
    assert ds.LongitudinalTemporalInformationModified == "UNMODIFIED"
    ds, _ = run(dates="remove")
    assert not ds.get("StudyDate")


# ---------------------------------------------------------------- UIDs
def test_uids_regenerated_consistently():
    ds, _ = run(uids="regenerate")
    assert ds.StudyInstanceUID == new_uid(syn.FAKE_STUDY_UID, SALT)
    assert ds.SOPInstanceUID != syn.FAKE_SOP_UID
    assert len(ds.SOPInstanceUID) <= 64 and ds.SOPInstanceUID.startswith("2.25.")
    assert ds.file_meta.MediaStorageSOPInstanceUID == ds.SOPInstanceUID
    assert ds.SOPClassUID == "1.2.840.10008.5.1.4.1.1.4"   # class UIDs untouched


def test_uids_keep():
    ds, _ = run(uids="keep", dates="keep")
    assert ds.StudyInstanceUID == syn.FAKE_STUDY_UID
    assert ds.SOPInstanceUID == syn.FAKE_SOP_UID


# ---------------------------------------------------------------- burned-in
@pytest.mark.parametrize("modality, flag, expected", [
    ("MR", None, "no"), ("US", None, "possible"), ("MR", "YES", "yes"), ("US", "NO", "no"),
])
def test_burned_in_status(modality, flag, expected):
    ds = syn.synthetic_dataset(modality)
    if flag:
        ds.BurnedInAnnotation = flag
    assert Pseudonymiser.burned_in_status(ds) == expected


def test_report_has_no_values():
    _, rep = run()
    text = " ".join(rep.tags) + " ".join(map(str, rep.actions))
    assert "Testperson" not in text and syn.FAKE_ID not in text
