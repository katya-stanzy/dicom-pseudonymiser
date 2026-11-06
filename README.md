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

1. **Test on a copy:** copy 2–3 participants' folders to, e.g., `D:\dicom_test\input\`. Create `D:\dicom_test\keys\participants.csv`, where `source_key` must equal each folder name exactly.
2. **Settings:** run `copy environment.example.json environment.json`, then `notepad environment.json`. Set `input_dir`, `output_dir`, `mapping_file` and `audit_dir`, using **forward slashes** (`D:/dicom_test/input`) or doubled backslashes. Set `"workers": 1` for the first run. `environment.json` is git-ignored.
3. **Salt (if needed):** run `$env:DICOMPSEUDO_SALT = "long-random-phrase"`. It is needed only for `"uids": "regenerate"` (the default), or for `"dates": "shift"` when a participant has no `date_shift_days` in the CSV (see [Dates, birth dates and the salt](#dates-birth-dates-and-the-salt)). `dicompseudo validate` tells you whether it is needed. Set it in **every new window**, because it is lost when the window closes. Use the **same salt every time** for the same study: with a different salt, dates shift differently and UIDs no longer link up across runs. Store it privately with the key file.
4. **Run, in order:**

```powershell
conda activate dicompseudo
cd C:\Users\User\OneDrive\Documents\WORK\dicom-pseudonymiser
$env:DICOMPSEUDO_SALT = "<your salt>"
dicompseudo validate                 # must end with OK
dicompseudo inspect --tags
dicompseudo preview                  # only if inspect reports possible/yes burned-in files
dicompseudo run
dicompseudo check D:\dicom_test\output --config environment.json --mapping D:\dicom_test\keys\participants.csv --terms D:\dicom_test\keys\terms.txt
```

5. **What to look for:**
   - `run`: mostly `written`. `unmatched` means a folder name is not in the CSV.
   - `check`: should end with `RESULT: OK, no leaks found`.
   - Open a few output files in Slicer to confirm they still load and look right.

The console output of these commands holds counts, tag names and pseudonymous paths only, so it is safe to share when asking for help. The preview images and the audit `_files.csv` are not.

## Folder layout

```
input_dir/                 one folder per participant, any name; that name is the source_key
├── <folder A>/…/IM0001    any depth and file names below it
└── <folder B>/…
keys/                      keep OUTSIDE input_dir and output_dir
├── participants.csv       source_key,experimental_number,nickname[,date_shift_days]
└── dicompseudo_audit/     created automatically (private: audit log, previews)
```

The folder name doesn't need to contain the experimental number: the number and nickname come from the CSV. Files directly inside `input_dir` are skipped as unmatched. If your folders aren't organised by participant, set `"match_participant_by": "PatientID"`.

## Use

1. Copy `environment.example.json` to `environment.json` and edit it.
2. Create `participants.csv` **outside this repository**, with the columns
   `source_key,experimental_number,nickname[,date_shift_days]`.
   `source_key` is the participant's folder name inside `input_dir` (or their original PatientID if `"match_participant_by": "PatientID"`).
3. If needed, set the secret salt. It is needed for UID regeneration, and for date shifts that are not given in the CSV:
   - PowerShell: `$env:DICOMPSEUDO_SALT = "long-random-secret"`
   - bash: `export DICOMPSEUDO_SALT=long-random-secret`
4. Run:

```bash
dicompseudo validate                 # settings + mapping
dicompseudo inspect --tags           # what sensitive data is in the input (counts only)
dicompseudo preview                  # private images for drawing pixel masks (if needed)
dicompseudo run                      # writes output_dir + audit log in audit_dir
dicompseudo check <output_dir> --config environment.json --mapping <participants.csv> --terms <words.txt>
```

`--terms` takes a private text file with one word per line: real names, the lab's name, the town and so on. `check` reports where a term was found by its line number, never the word itself.

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
