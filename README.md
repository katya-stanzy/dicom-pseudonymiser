# dicompseudo

Configurable pseudonymisation of DICOM files, based on the DICOM standard's confidentiality profile (PS3.15 Annex E, Table E.1-1, edition 2026d). It replaces PatientName and PatientID with each participant's nickname or experimental number. It removes everything else that can identify a person or a site, and each category can be switched on or off in `environment.json`. See `PLAN.md` for the design and the full list of categories.

## Set up (once)

```bash
conda env create -f environment.yml
conda activate dicompseudo
```

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
3. Set a secret salt. It is needed for UID regeneration and date shifting, so keep it with the mapping file:
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
