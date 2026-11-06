"""Settings and mapping validation."""
from __future__ import annotations

import json

import pytest

from dicompseudo.config import ConfigError, from_dict, load, parse_tag
from dicompseudo.mapping import Mapping, MappingError
from dicompseudo.rules import load_rules, meta, resolve_action, rule_for


def test_example_settings_file_is_valid(tmp_path):
    from pathlib import Path
    ex = Path(__file__).resolve().parents[1] / "environment.example.json"
    cfg = load(ex, salt="s")
    assert cfg.dates == "shift" and cfg.categories["institution"]


ROWS_ALL_SHIFTS = [{"source_key": "a", "experimental_number": "1", "nickname": "x",
                    "date_shift_days": "-30"},
                   {"source_key": "b", "experimental_number": "2", "nickname": "y",
                    "date_shift_days": "0"}]
ROWS_ONE_MISSING = [ROWS_ALL_SHIFTS[0],
                    {"source_key": "b", "experimental_number": "2", "nickname": "y"}]


def test_salt_always_required_for_uid_regeneration():
    with pytest.raises(ConfigError, match="uids='regenerate'"):
        from_dict({"uids": "regenerate", "dates": "keep"}, require_paths=False, salt="")


def test_salt_not_needed_when_every_participant_has_a_shift():
    cfg = from_dict({"uids": "keep", "dates": "shift"}, require_paths=False, salt="")
    cfg.check_salt(Mapping.from_rows(ROWS_ALL_SHIFTS))            # no error


def test_salt_needed_when_a_shift_is_missing():
    cfg = from_dict({"uids": "keep", "dates": "shift"}, require_paths=False, salt="")
    with pytest.raises(ConfigError, match="1 participant"):
        cfg.check_salt(Mapping.from_rows(ROWS_ONE_MISSING))
    cfg.salt = "s"
    cfg.check_salt(Mapping.from_rows(ROWS_ONE_MISSING))           # salt given: fine


@pytest.mark.parametrize("dates", ["keep", "year_only", "remove"])
def test_no_salt_needed_without_shift_or_regeneration(dates):
    cfg = from_dict({"uids": "keep", "dates": dates}, require_paths=False, salt="")
    cfg.check_salt(Mapping.from_rows(ROWS_ONE_MISSING))


def test_engine_refuses_derived_shift_without_salt():
    from dicompseudo.engine import Pseudonymiser
    from dicompseudo.mapping import Participant
    import make_synthetic as syn
    cfg = from_dict({"uids": "keep", "dates": "shift"}, require_paths=False, salt="")
    with pytest.raises(ValueError, match="needs a salt"):
        Pseudonymiser(cfg).process(syn.synthetic_dataset(), Participant("a", "1", "x"))
    ds = syn.synthetic_dataset()
    Pseudonymiser(cfg).process(ds, Participant("a", "1", "x", 0))
    assert ds.StudyDate == "20240310"                              # shift 0 = real dates


@pytest.mark.parametrize("bad, msg", [
    ({"dates": "sometimes"}, "dates"),
    ({"categories": {"lab": True}}, "unknown category"),
    ({"categories": {"sex": "yes"}}, "true or false"),
    ({"replace": {"PatientSex": "nickname"}}, "replace"),
    ({"keep_tags": ["NotAKeyword"]}, "neither"),
    ({"keep_tags": ["PatientSex"], "extra_remove_tags": ["(0010,0040)"]}, "both"),
    ({"colour": "red"}, "unknown setting"),
])
def test_bad_settings(bad, msg):
    with pytest.raises(ConfigError, match=msg):
        from_dict(bad, require_paths=False, salt="s")


def test_paths_required():
    with pytest.raises(ConfigError, match="input_dir"):
        from_dict({}, salt="s")


def test_invalid_json(tmp_path):
    p = tmp_path / "environment.json"
    p.write_text("{ not json", encoding="utf8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load(p, salt="s")


def test_parse_tag_forms():
    assert parse_tag("PatientSex") == parse_tag("(0010,0040)") == parse_tag("00100040") == 0x00100040


def test_mapping_load_and_errors(tmp_path):
    p = tmp_path / "participants.csv"
    p.write_text("source_key,experimental_number,nickname,date_shift_days\n"
                 "folder_A,JRF001,Owl,-100\nfolder_B,JRF002,Fox,\n", encoding="utf8")
    m = Mapping.load(p)
    assert len(m) == 2 and m.get("folder_A").date_shift_days == -100
    assert m.get("folder_B").date_shift_days is None
    with pytest.raises(MappingError, match="row 3: duplicate experimental_number"):
        Mapping.from_rows([{"source_key": "a", "experimental_number": "1", "nickname": "x"},
                           {"source_key": "b", "experimental_number": "1", "nickname": "y"}])
    with pytest.raises(MappingError, match="missing column"):
        q = tmp_path / "bad.csv"
        q.write_text("key,number\n", encoding="utf8")
        Mapping.load(q)


def test_rule_table_loaded():
    exact, masked = load_rules()
    assert len(exact) + len(masked) == meta()["n_rows"]
    assert rule_for(0x00100010).category == "identity"        # PatientName
    assert rule_for(0x00080080).category == "institution"     # InstitutionName
    assert rule_for(0x00101030).category == "body_measurements"
    assert rule_for(0x60023000).category == "curves_overlays"  # masked (60xx,3000)
    assert rule_for(0x00080060) is None                       # Modality: not sensitive


@pytest.mark.parametrize("code, action", [
    ("X", "X"), ("Z", "Z"), ("D", "D"), ("U", "U"), ("X/Z", "Z"), ("X/D", "D"),
    ("Z/D", "D"), ("X/Z/D", "D"), ("X/Z/U*", "U"),
])
def test_resolve_action(code, action):
    assert resolve_action(code) == action
