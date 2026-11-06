# dicompseudo

Configurable pseudonymisation of DICOM files, based on the DICOM standard's confidentiality profile (PS3.15 Annex E, Table E.1-1, edition 2026d). It replaces PatientName and PatientID with each participant's nickname or experimental number. It removes everything else that can identify a person or a site, and each category can be switched on or off in `environment.json`. See `PLAN.md` for the design and the full list of categories.

## Set up (once)

```powershell
cd C:\Users\User\OneDrive\Documents\WORK\dicom-pseudonymiser
conda env create -f environment.yml      # first time
conda env update -f environment.yml      # after pulling changes (e.g. phase 2 image libraries)
conda activate dicompseudo
dicompseudo --version                    # should print: dicompseudo 0.1.0
```

If `conda activate` doesn't work in PowerShell, run `conda init powershell` once and reopen the window, or use the Anaconda Prompt.

## Quick start: first run in PowerShell

Work through the chapters in order. Test on a **copy** of 2–3 participants first, never on the only copy of your data.

### 1. Create the folder layout

Make one study folder with three folders side by side, for example `D:\dicom_test\`:

```
D:\dicom_test\
├── input\                 copy of the original DICOM data
│   ├── <folder A>\…       one folder per participant, any name, any depth below it
│   └── <folder B>\…
├── output\                created by `run`; this is the only folder you share
└── keys\                  PRIVATE: never share, never put inside input\ or output\
    ├── participants.csv   the key (you create it, see below)
    ├── terms.txt          optional: private words that must not appear in the output
    └── dicompseudo_audit\ created automatically (audit log, previews)
```

```powershell
mkdir D:\dicom_test\input, D:\dicom_test\keys
```

Copy the participants' folders into `input\`. Nothing should sit directly in `input\` (loose files are skipped as unmatched), and `keys\` must not be inside it.

Then create `keys\participants.csv`, for example with `notepad D:\dicom_test\keys\participants.csv`:

```
source_key,experimental_number,nickname,date_shift_days
Erika_Testperson,JRF001,Owl,0
003,JRF003,Fox,-412
```

- `source_key` must be **exactly** the participant's folder name inside `input\`: same characters, same case, same leading zeros. If it differs, all of that participant's files end up `unmatched`.
- `experimental_number` and `nickname` replace PatientID and PatientName, and `experimental_number` also names the participant's output folder.
- `date_shift_days` is optional; see [Dates, birth dates and the salt](#dates-birth-dates-and-the-salt).
- If your folders aren't organised by participant, use the original PatientID as `source_key` and set `"match_participant_by": "PatientID"` in chapter 2.

### 2. Go to the tool and create `environment.json`

```powershell
conda activate dicompseudo
cd C:\Users\User\OneDrive\Documents\WORK\dicom-pseudonymiser
copy environment.example.json environment.json
notepad environment.json
```

Point the four paths at the folders from chapter 1, using **forward slashes** (or doubled backslashes):

```json
"input_dir":    "D:/dicom_test/input",
"output_dir":   "D:/dicom_test/output",
"mapping_file": "D:/dicom_test/keys/participants.csv",
"audit_dir":    "D:/dicom_test/keys/dicompseudo_audit",
```

Set `"workers": 1` for the first run. Leave the other settings at their defaults for now; the chapters further down explain them. `environment.json` is git-ignored, so your paths stay out of the repository.

### 3. Set the salt

```powershell
$env:DICOMPSEUDO_SALT = "long-random-phrase"
```

The salt is needed for `"uids": "regenerate"` (the default) and for `"dates": "shift"` when a participant has no `date_shift_days` in the CSV. `validate` tells you whether it is needed.

- Set it again in **every new PowerShell window**, because it is lost when the window closes.
- Use the **same salt every time** for the same study. With a different salt, dates shift differently and UIDs no longer link up across runs.
- Store it privately, alongside the key file.

### 4. Test: validate, inspect, preview

None of these commands writes any DICOM output.

```powershell
dicompseudo validate          # checks settings, paths, mapping and salt; must end with OK
dicompseudo inspect --tags    # counts of sensitive data in the input, and matched/unmatched files
dicompseudo preview           # only if inspect reports burned-in text "yes" or "possible"
```

- `validate`: fix every error it reports before going on.
- `inspect`: check `unmatched: 0`. If it isn't 0, a folder name differs from its `source_key` (chapter 1).
- `preview`: afterwards, open `keys\dicompseudo_audit\preview\index.html` in a browser. It shows images only for files that may have text burned into the image, so an empty page means there is nothing to mask. See [Text burned into the pixels](#text-burned-into-the-pixels-ultrasound-screenshots).

### 5. Run and check

```powershell
dicompseudo run
dicompseudo check D:\dicom_test\output --config environment.json --mapping D:\dicom_test\keys\participants.csv --terms D:\dicom_test\keys\terms.txt
```

`--terms` is optional. It takes a private text file with one word per line: real names, birth dates, the lab's name, the town and so on. `check` reports where a term was found by its line number, never the word itself.

**What to look for:**

- `run`: every file `written`. `unmatched` means a folder name is not in the CSV; `not_dicom` usually means non-DICOM files sit in `input\`.
- `check`: must end with `RESULT: OK, no leaks found`.
- Open a few output files in Slicer to confirm they still load and look right.

When you share data, share only `output\`, never the whole study folder, because it contains `keys\`.

The console output of these commands holds counts, tag names and pseudonymous paths only, so it is safe to share when asking for help. The preview images and the audit `_files.csv` are not.

## Command reference

| Command | What it does |
|---|---|
| `dicompseudo validate` | checks settings, paths, mapping and whether the salt is needed |
| `dicompseudo inspect --tags` | what sensitive data is in the input (counts only) |
| `dicompseudo preview [--all]` | private images for drawing pixel masks |
| `dicompseudo run` | writes `output_dir`, plus the audit log in `audit_dir` |
| `dicompseudo check <output_dir> --config environment.json --mapping <participants.csv> [--terms <words.txt>]` | leak check of the output |

In bash, set the salt with `export DICOMPSEUDO_SALT=long-random-phrase`.

## Dates, birth dates and the salt

**What the salt is:** a secret phrase that only you know. The tool mixes it into its calculations, so that new UIDs and date shifts cannot be recalculated from the original data by anyone else. It never decides *which* dates are kept; the settings below do.

**When it is needed:**

| Setting | Salt needed? |
|---|---|
| `"uids": "regenerate"` (default) | **yes**, always |
| `"uids": "keep"` | no (but the original UIDs stay in the files) |
| `"dates": "shift"` and a participant has **no** `date_shift_days` in the CSV | **yes** (their shift is derived from the salt) |
| `"dates": "shift"` and **every** participant has a `date_shift_days` | no |
| `"dates": "keep"`, `"year_only"` or `"remove"` | no |

`dicompseudo validate` prints `salt: set` or `salt: not set (not needed with these settings)`, plus how many shifts come from the CSV and how many from the salt.

**Choosing dates for a study:**

| Research needs | Settings |
|---|---|
| Real scan dates **and** real birth dates | `"dates": "keep"`, `"categories": {"age": false}` |
| Exact age at scan and intervals, but hidden calendar dates | `"dates": "shift"`, `"categories": {"age": false}`. The birth date moves by the same number of days as the scans. |
| Your own shift per participant | add `date_shift_days` to `participants.csv` (e.g. `-412`). `0` keeps that participant's real dates. |
| Only the year | `"dates": "year_only"` (dates become `YYYY0101`) |
| No dates at all | `"dates": "remove"` |
| A few specific dates only | `"keep_tags": ["PatientBirthDate", "StudyDate"]`, together with `"dates": "keep"` or `"remove"`. With `"shift"`, the kept dates would be real and the rest shifted. |

Example `participants.csv` with your own shifts:

```
source_key,experimental_number,nickname,date_shift_days
Erika_Testperson,JRF001,Owl,0
003,JRF003,Fox,-412
```

**Caution:** real birth dates together with real scan dates make people far easier to re-identify than names alone. Make sure your ethics approval and data agreement allow it, and keep such outputs as restricted as the key file.

## Keep private, never share with the output

- `participants.csv` (the key)
- the salt
- `audit_dir` (it lists original input paths)
- your terms file

The output is **pseudonymised**, not anonymised: while the key exists, it is still personal data under the GDPR.

## Text burned into the pixels (ultrasound, screenshots)

Header editing cannot remove names or dates printed *into the image*. For these files, define mask templates:

1. Run `dicompseudo preview`, then open `<audit_dir>/preview/index.html`. It is **private**, because it shows the original pixels. Every device that may carry burned-in text is listed with sample images and its image size.

   > **Note:** images are shown only for files that may have text burned into the image (BurnedInAnnotation "yes" or "possible"), or for devices that already match one of your `pixel_masks` templates. If no file qualifies, `index.html` contains only the heading and no pictures. That is expected, not an error. To see sample images for every device anyway, run `dicompseudo preview --all`.
2. Copy the entries you need from `<audit_dir>/preview/pixel_masks_skeleton.json` into `"pixel_masks"` in `environment.json`. Fill in the rectangles as `[x, y, width, height]` in pixels from the top-left corner, for example:

```json
"pixel_masks": [
  { "name": "us_vendorX_800x600",
    "match": { "Modality": "US", "Manufacturer": "VendorX", "ManufacturerModelName": "ModelY",
               "Rows": 600, "Columns": 800 },
    "rectangles": [[0, 0, 800, 60], [600, 560, 200, 40]] }
]
```

3. Run `dicompseudo preview` again. The left image shows your rectangles in red and the right image shows the masked result. Adjust until all the text is covered.
4. `dicompseudo run` masks the matching files, and `dicompseudo check ... --config environment.json` verifies that every rectangle is black.

Files that may carry burned-in text but match no template follow `"on_burned_in_annotation"`: `flag` (written, and listed in the audit log), `skip` (left out) or `allow`.

## Known limits

- **Masks are drawn by you, not detected.** If a device sometimes puts text somewhere else (another preset, a measurement overlay), that text is not covered. Check the preview across several sessions.
- **Calling `run()` from your own Python script on Windows:** put the call under `if __name__ == "__main__":`, or set `"workers": 1`. In notebooks, always use `"workers": 1`.

## Development

```bash
python -m pytest            # synthetic data only
python tools/build_rules.py chapter_E.html   # rebuild the rule table from a new PS3.15 edition
```
