# dicom-pseudonymiser: plan

A small, configurable Python package. It replaces personal information in DICOM files with a participant's experimental number or nickname, and removes all other identifying information. Every category can be switched on or off in one settings file, `environment.json`.

> **Pseudonymised, not anonymised.** As long as a key exists that links experimental numbers to real people (the mapping file), the output is still personal data under the GDPR. Keep the mapping file outside this repository and outside any shared folder.

---

## 1. Goals

1. **Substitute:** set `PatientName` to the nickname and `PatientID` to the experimental number. Each can be set to either value, and both can come from a mapping file.
2. **Remove:** delete every attribute that can identify a person. That covers age, birth date, sex, height, weight, BMI, address, phone numbers, other IDs, and staff names (referring physician, operator and so on).
3. **Remove the site:** delete the institution's name, address and department, the station name and the device serial number.
4. **Make every step optional:** each category is a switch in `environment.json`. Single tags can be kept or removed on top of the category settings.
5. **Check the output:** a `check` command scans the output for anything left over and reports only tag names and counts, never values. That way the tool can be debugged, including with Claude, without exposing personal data.
6. **Reproducible:** a fixed environment, a versioned rule table, and an audit log for each run.

## 2. Where the rules come from

DICOM fields are standardised. The rule table is built from **DICOM PS3.15 Annex E, Table E.1-1** (the Application Level Confidentiality Profile), which lists every attribute with possible identifying content and the default action for each. The table ships with the package as `src/dicompseudo/data/ps315_annexE.csv`, with the DICOM edition recorded. Each attribute is then given one of the categories below so the switches can work by category.

| Category (`environment.json` key) | Examples (keyword · tag) | Default |
|---|---|---|
| `identity` | PatientName (0010,0010) · PatientID (0010,0020) · OtherPatientIDs (0010,1000) · OtherPatientNames (0010,1001) · PatientBirthName (0010,1005) · PatientMotherBirthName (0010,1060) · IssuerOfPatientID (0010,0021) | **replace** (name, ID), remove (others) |
| `age` | PatientAge (0010,1010) · PatientBirthDate (0010,0030) · PatientBirthTime (0010,0032) | remove |
| `sex` | PatientSex (0010,0040) · PatientSexNeutered (0010,2203) | remove (*often needed for analysis, so consider keeping it*) |
| `body_measurements` | PatientSize = height (0010,1020) · PatientWeight (0010,1030) · PatientBodyMassIndex (0010,1022) | remove |
| `contact` | PatientAddress (0010,1040) · PatientTelephoneNumbers (0010,2154) · CountryOfResidence (0010,2150) · RegionOfResidence (0010,2152) | remove |
| `medical_social` | EthnicGroup (0010,2160) · Occupation (0010,2180) · AdditionalPatientHistory (0010,21B0) · PatientComments (0010,4000) · MedicalAlerts (0010,2000) · Allergies (0010,2110) · PregnancyStatus (0010,21C0) · SmokingStatus (0010,21A0) · PatientReligiousPreference (0010,21F0) | remove |
| `institution` | InstitutionName (0008,0080) · InstitutionAddress (0008,0081) · InstitutionalDepartmentName (0008,1040) · InstitutionCodeSequence (0008,0082) | remove |
| `device` | StationName (0008,1010) · DeviceSerialNumber (0018,1000) · other device identifiers (PS3.15 "Retain Device Identity") | remove |
| `staff` | ReferringPhysicianName (0008,0090) · PerformingPhysicianName (0008,1050) · OperatorsName (0008,1070) · NameOfPhysiciansReadingStudy (0008,1060) · PhysiciansOfRecord (0008,1048) · RequestingPhysician (0032,1032) | remove |
| `all_person_names` | **any** element with VR = PN that is not already covered (this keeps the behaviour of the old `PN_callback`) | blank |
| `dates` | StudyDate, SeriesDate, AcquisitionDate, ContentDate and their times | `keep` \| `remove` \| `year_only` \| `shift` (fixed offset per participant, so intervals are preserved) |
| `identifiers` | AccessionNumber (0008,0050) · StudyID (0020,0010) · RequestAttributesSequence (0040,0275) | remove |
| `uids` | StudyInstanceUID · SeriesInstanceUID · SOPInstanceUID · FrameOfReferenceUID | `keep` \| `regenerate` (deterministic, from a secret salt, so links between series are kept) |
| `free_text` | StudyDescription · SeriesDescription · ImageComments · ProtocolName | `keep` \| `remove` (these can contain names) |
| `private_tags` | all odd-group (vendor) tags | remove |
| `curves_overlays` | groups 50xx (curves) and 60xx (overlays, which may contain burned-in text) | remove |
| `graphics` · `structured_content` | graphic annotations · SR content sequences (PS3.15 "Clean Graphics" / "Clean Structured Content") | remove |
| *(not switchable)* `other` | clinical-trial IDs, GPS, pronouns and gender identity, digital signatures, … (142 attributes) | always the Basic Profile action; override single tags with `keep_tags` |

After processing, the tool sets `PatientIdentityRemoved = YES` (0012,0062) and records the method in `DeidentificationMethod` (0012,0063) and `DeidentificationMethodCodeSequence` (0012,0064).

### Known limits, handled explicitly
- **Text burned into the pixels:** ultrasound and some X-ray and secondary-capture images print the name or date into the image itself. The tool reads `BurnedInAnnotation` (0028,0301) and the modality. The `on_burned_in_annotation` setting can be `flag`, `skip` or `allow`. Masking pixel regions is planned for phase 2.
- **File and folder names** often contain names. Outputs are always written as `<experimental_number>/study_<hash8>/<SeriesNumber>_<Modality>/<InstanceNumber>.dcm`, so original file names never reach the output. Duplicate names get a `_1`, `_2` suffix, and DICOMDIR files are never copied.
- **The file meta header** (group 0002) is cleaned too. Source, sending and receiving AE titles can name the site.
- **Nested sequences:** every rule applies recursively inside sequences.

## 3. `environment.json` (settings, set up before running)

See `environment.example.json`. Copy it to `environment.json`, which git ignores, and edit it.

```jsonc
{
  "input_dir":  "D:/study/raw_dicom",
  "output_dir": "D:/study/pseudo_dicom",
  "mapping_file": "D:/study/keys/participants.csv",   // outside the repo!
  "audit_dir": "D:/study/keys/dicompseudo_audit",     // private: lists original paths
  "match_participant_by": "folder",                   // "folder" | "PatientID"
  "replace": { "PatientName": "nickname", "PatientID": "experimental_number" },
  "categories": {
    "identity": true, "age": true, "sex": true, "body_measurements": true,
    "contact": true, "medical_social": true, "institution": true, "device": true,
    "staff": true, "all_person_names": true, "identifiers": true,
    "structured_content": true, "graphics": true,
    "private_tags": true, "curves_overlays": true
  },
  "dates": "shift",            // keep | remove | year_only | shift
  "uids": "regenerate",        // keep | regenerate
  "free_text": "remove",       // keep | remove
  "keep_tags": [],             // e.g. ["PatientSex"]: overrides categories
  "extra_remove_tags": [],     // e.g. ["(0019,10A1)"]
  "on_burned_in_annotation": "flag",
  "workers": 4,
  "overwrite": false
}
```

The mapping file is a CSV with the columns `source_key, experimental_number, nickname[, date_shift_days]`. `source_key` holds the folder name or the original PatientID, whichever `match_participant_by` says. The secret salt is set through an environment variable (`DICOMPSEUDO_SALT`) and is never written into the JSON file. It is required only for `"uids": "regenerate"`, and for `"dates": "shift"` when a participant has no `date_shift_days` in the mapping.

## 4. Command-line interface

```
dicompseudo validate --config environment.json   # check settings + mapping, no files written
dicompseudo inspect  --config environment.json   # list which sensitive TAGS are present (counts only, no values)
dicompseudo run      --config environment.json   # pseudonymise -> output_dir + audit log
dicompseudo check    <output_dir> [--config environment.json] [--mapping participants.csv] [--terms words.txt]
        # leak scan: any remaining tag the settings should have removed, any PN value, private
        # tags, overlays, file-meta AE titles, any original source_key or private term found
        # (as a whole word) in a text element or output path. Reports tag names, mapping row
        # numbers, terms line numbers and counts only. Exit status 1 when a leak is found.
```

## 5. Package layout

```
dicom-pseudonymiser/
├── pyproject.toml              # package metadata, `dicompseudo` entry point
├── environment.yml             # conda env: python 3.12, pydicom>=3, (pylibjpeg for compressed data)
├── environment.example.json    # settings template (copy to environment.json)
├── README.md
├── PLAN.md
├── src/dicompseudo/
│   ├── cli.py                  # argparse commands above
│   ├── config.py               # load + validate environment.json (clear error messages)
│   ├── rules.py                # category → tag sets, built from data/ps315_annexE.csv
│   ├── engine.py               # apply rules to one Dataset (recursive into sequences)
│   ├── mapping.py              # participants.csv → experimental number / nickname / date shift
│   ├── dates.py                # keep / remove / year_only / shift
│   ├── uids.py                 # deterministic UID regeneration (salted hash → 2.25.<int>)
│   ├── io.py                   # find DICOMs (by preamble, not extension), parallel processing, safe writing
│   ├── survey.py               # `inspect`: sensitive tags + device signatures, counts only
│   ├── check.py                # leak scan
│   ├── audit.py                # per-run log: config hash, version, tag actions + counts (no values)
│   └── data/ps315_annexE.csv
└── tests/
    ├── make_synthetic.py       # builds FAKE DICOMs with fake names, never real data
    └── test_*.py               # every category on/off, sequences, PN sweep, dates, UIDs, check
```

## 6. Work plan

| Step | What | Done when |
|---|---|---|
| 1 ✅ | Set up the repo, `pyproject.toml`, `environment.yml`, `.gitignore` | `conda env create` works and `dicompseudo --help` runs |
| 2 ✅ | Build the rule table from PS3.15 Annex E and assign categories | the CSV is versioned and every row has a category |
| 3 ✅ | Write `config.py` and `mapping.py` with validation | bad JSON or a missing participant gives a clear message |
| 4 ✅ | Port `engine.py` from `anonymise.py` (PN sweep, private tags, curves) and add the categories, dates and UIDs | the synthetic tests pass |
| 5 ✅ | Add `io.py`: walk folders, run in parallel, use new output names, never overwrite the input; `survey.py` for `inspect` | input folder unchanged, output tree in the new layout |
| 6 ✅ | Add `check.py` and `audit.py` | `check` finds deliberately planted leaks in the synthetic data |
| 7 | Dry run on real data, run locally by Katya: `inspect`, then `run`, then `check` | `check` reports 0 leaks, and spot checks in Slicer still load correctly |
| 8 | Write the README with a quick start | a colleague can run it from the README alone |
| Phase 2 ✅ | Pixel masking for burned-in text: device templates, `preview` contact sheet, masked rewrite, `check` verifies masks | synthetic masks verified by tests |
| Phase 2b | Optional OCR suggestions (Tesseract) for unmatched images · optional GUI · defacing only if head scans are ever included | — |

## 6b. Phase 2: text burned into the pixels (implemented)

- **Templates** in `environment.json` → `pixel_masks`. Each one has a `name`, a `match` on technical header values (Modality, Manufacturer, ManufacturerModelName, Rows, Columns, SoftwareVersions, SOPClassUID, ImageType), and `rectangles` given as `[x, y, width, height]` in pixels from the top-left. The first matching template wins.
- **Masking** fills every rectangle with black in every frame (MONOCHROME1: maximum value). It runs after the header has been processed, matched on the original header. Compressed images are decoded and rewritten uncompressed. The file then gets `BurnedInAnnotation = NO` (unless `clears_burned_in: false`) and the DICOM code 113101 "Clean Pixel Data Option".
- **`on_burned_in_annotation`** (`flag` / `skip` / `allow`) now applies only to files that **no** template matches.
- **`dicompseudo preview`** writes a private contact sheet (`audit_dir/preview/index.html`). For each device it shows the original with the rectangles in red next to the masked result, at the same brightness scale. It also writes `pixel_masks_skeleton.json` with one empty template per device that has none. The images show original pixels, so they stay in the private audit folder.
- **`dicompseudo check --config`** re-reads the pixels of every file that matches a template and reports a LEAK if a rectangle is not black.
- **Not automatic:** the tool never guesses where text is. A person draws the rectangles after looking at the preview.

## 7. What happens to the old scripts

The old scripts are `JRF_GaitAnalysis/anonymise_py_dicom/anonymise.py` and `anonym.py`, and the notebooks `pseudonymise.ipynb`, `template.ipynb` and `try.ipynb`. Their logic (PN callback, curves, private tags, institution removal, parallel processing) moves into `engine.py` and `io.py`. The notebooks are not copied: their saved outputs may contain header values. `read_file`, which pydicom 3 removed, is replaced by `dcmread`.

## 8. Privacy rules for this repo

- Never commit real DICOMs, mapping files or real `environment.json` files (see `.gitignore`).
- Tests use synthetic data only.
- The tool never prints tag values. `inspect`, `check` and the audit log report tag names and counts only.
