import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aliases import load_aliases, Alias, AliasValidationError, check_openalex_ids_live  # noqa: E402


def write_yaml(tmp_path, data):
    p = tmp_path / "aliases.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_empty_file_is_fine(tmp_path):
    p = write_yaml(tmp_path, [])
    assert load_aliases(p) == []


def test_missing_file_is_fine(tmp_path):
    assert load_aliases(tmp_path / "does_not_exist.yaml") == []


def test_valid_entry_round_trips(tmp_path):
    p = write_yaml(tmp_path, [{
        "key": "DSN", "core_id": "42", "dblp_key": "conf/dsn",
        "openalex_source_ids": ["S123"], "exclude_notes": "excludes DSN-W",
    }])
    aliases = load_aliases(p)
    assert len(aliases) == 1
    assert aliases[0].key == "DSN"
    assert aliases[0].openalex_source_ids == ["S123"]


def test_duplicate_key_is_hard_error(tmp_path):
    p = write_yaml(tmp_path, [
        {"key": "DSN", "core_id": "1"},
        {"key": "DSN", "core_id": "2"},
    ])
    with pytest.raises(AliasValidationError, match="duplicate key"):
        load_aliases(p)


def test_unknown_field_is_hard_error(tmp_path):
    p = write_yaml(tmp_path, [{"key": "DSN", "not_a_real_field": "x"}])
    with pytest.raises(AliasValidationError):
        load_aliases(p)


def test_not_a_list_is_hard_error(tmp_path):
    p = write_yaml(tmp_path, {"key": "DSN"})
    with pytest.raises(AliasValidationError, match="expected a YAML list"):
        load_aliases(p)


def test_404ing_openalex_id_detected():
    alias = Alias(key="DSN", openalex_source_ids=["S_good", "S_bad"])
    errors = check_openalex_ids_live([alias], lambda sid: sid == "S_good")
    assert len(errors) == 1
    assert "S_bad" in errors[0]
