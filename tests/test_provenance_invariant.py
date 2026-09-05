import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from extract_icore import DocumentExtraction  # noqa: E402


def test_populated_field_without_provenance_is_rejected():
    with pytest.raises(ValidationError, match="no provenance"):
        DocumentExtraction(current_rank="A", provenance={})


def test_populated_field_with_provenance_is_accepted():
    doc = DocumentExtraction(
        current_rank="A",
        provenance={"current_rank": {
            "page": 1, "snippet": "Rank: A",
            "confidence": "high", "method": "regex",
        }},
    )
    assert doc.current_rank == "A"


def test_none_fields_need_no_provenance():
    # every schema field defaults to None/empty and should validate with an
    # entirely empty provenance dict
    DocumentExtraction()


def test_empty_list_and_dict_need_no_provenance():
    doc = DocumentExtraction(for_codes=[], area_leaders=[], provenance={})
    assert doc.for_codes == []
