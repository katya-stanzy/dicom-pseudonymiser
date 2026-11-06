"""Build FAKE DICOM datasets for tests. All values are invented; never use real data here."""
from __future__ import annotations

from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import ExplicitVRLittleEndian

FAKE_NAME = "Testperson^Erika"
FAKE_ID = "FAKE-123456"
FAKE_INSTITUTION = "Fake Imaging Lab"
FAKE_STUDY_UID = "1.2.826.0.1.3680043.8.498.1"
FAKE_SERIES_UID = "1.2.826.0.1.3680043.8.498.2"
FAKE_SOP_UID = "1.2.826.0.1.3680043.8.498.3"
FAKE_FOR_UID = "1.2.826.0.1.3680043.8.498.4"


def synthetic_dataset(modality: str = "MR") -> Dataset:
    ds = Dataset()
    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.4"   # MR Image Storage
    fm.MediaStorageSOPInstanceUID = FAKE_SOP_UID
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta = fm

    ds.SOPClassUID = fm.MediaStorageSOPClassUID
    ds.SOPInstanceUID = FAKE_SOP_UID
    ds.StudyInstanceUID = FAKE_STUDY_UID
    ds.SeriesInstanceUID = FAKE_SERIES_UID
    ds.FrameOfReferenceUID = FAKE_FOR_UID
    ds.Modality = modality

    # identity / demographics
    ds.PatientName = FAKE_NAME
    ds.PatientID = FAKE_ID
    ds.OtherPatientIDs = "FAKE-OTHER-1"
    ds.PatientBirthDate = "19800515"
    ds.PatientAge = "044Y"
    ds.PatientSex = "F"
    ds.PatientSize = "1.68"
    ds.PatientWeight = "61.5"
    ds.PatientAddress = "1 Fake Street, Faketown"
    ds.EthnicGroup = "FAKE"
    ds.PatientComments = "fake comment"

    # site and people
    ds.InstitutionName = FAKE_INSTITUTION
    ds.InstitutionAddress = "2 Fake Road, Faketown"
    ds.InstitutionalDepartmentName = "Fake Department"
    ds.StationName = "FAKESTATION01"
    ds.DeviceSerialNumber = "SN-FAKE-0001"
    ds.ReferringPhysicianName = "Doctor^Fake"
    ds.OperatorsName = "Operator^Fake"

    # identifiers, dates, free text
    ds.AccessionNumber = "ACC-FAKE-1"
    ds.StudyID = "STUDY-FAKE"
    ds.StudyDate = "20240310"
    ds.SeriesDate = "20240310"
    ds.StudyTime = "101500"
    ds.AcquisitionDateTime = "20240310101530.000000"
    ds.StudyDescription = "Knee MRI of Erika Testperson"
    ds.SeriesDescription = "T1 sag"

    # technical attributes that must survive
    ds.Rows = 4
    ds.Columns = 4
    ds.SliceThickness = "3.0"
    ds.Manufacturer = "FakeVendor"

    # inside a sequence: a PN that IS in the table and one that is NOT (PN sweep test)
    item = Dataset()
    item.CodeValue = "FAKE"
    item.CodingSchemeDesignator = "99FAKE"
    item.CodeMeaning = "fake code"
    item.PersonName = "Nested^Fake"          # (0040,A123): in the PS3.15 table
    item.EvaluatorName = "Evaluator^Fake"    # (0014,2006): PN, not in the table
    ds.ProcedureCodeSequence = Sequence([item])

    # private tags
    blk = ds.private_block(0x0029, "FAKE VENDOR", create=True)
    blk.add_new(0x01, "LO", "private fake value")

    # overlay group
    ds.add_new(0x60000010, "US", 4)          # OverlayRows
    ds.add_new(0x60004000, "LT", "overlay comment fake")
    return ds
