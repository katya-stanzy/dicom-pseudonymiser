# dicompseudo

Configurable pseudonymisation of DICOM files, based on the DICOM standard's confidentiality profile (PS3.15 Annex E, Table E.1-1, edition 2026d). It replaces PatientName and PatientID with each participant's nickname or experimental number. It removes everything else that can identify a person or a site, and each category can be switched on or off in `environment.json`. See `PLAN.md` for the design and the full list of categories.

## Set up (once)

```bash
conda env create -f environment.yml
conda activate dicompseudo
```

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

## Known limits

- **Text burned into the pixels** (common in ultrasound) is flagged but not removed yet; masking comes in phase 2. Use `"on_burned_in_annotation": "skip"` to leave these files out.
- **Calling `run()` from your own Python script on Windows:** put the call under `if __name__ == "__main__":`, or set `"workers": 1`. In notebooks, always use `"workers": 1`.

## Development

```bash
python -m pytest            # synthetic data only
python tools/build_rules.py chapter_E.html   # rebuild the rule table from a new PS3.15 edition
```
