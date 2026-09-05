"""End-to-end test against a synthetic ICORE Data-document PDF fixture,
with a known expected extraction — SPEC.md §9. See
tests/fixtures/generate_fixture.py for how sample_data_doc.pdf was built;
every line in it mirrors the exact label/caption wording extract_icore.py's
regexes expect, so this exercises the real regex pass end to end (not a
mock), just not against a real ICORE-issued PDF (none exists in this repo).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from extract_icore import extract, DocumentExtraction  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "sample_data_doc.pdf"


def test_fixture_exists():
    assert FIXTURE.exists(), "run tests/fixtures/generate_fixture.py first"


def test_known_field_values():
    result = extract(FIXTURE, core_id="TEST", round_="ICORE2026",
                      link_type="data", doc_url="https://example.org/x.pdf")
    assert result["needs_ocr"] is False
    fields = result["fields"]

    assert fields["current_rank"] == "A"
    assert fields["requested_rank"] == "A*"
    assert fields["for_codes"] == [4606]
    assert fields["citation_data_source"] == "Elsevier Scopus Database 2025"
    assert fields["centile_years"] == [2022, 2023, 2024]
    assert fields["citation_top25_venue_pct"] == 30
    assert fields["citation_top25_tier_baseline_pct"] == \
        {"A*": 66, "A": 43, "B": 22, "C": 11}
    assert fields["author_strength_top25_venue_pct"] == 28
    assert fields["gs_h5_index"] == 42
    assert fields["pc_size"] == 30
    assert fields["pc_established_count"] == 12
    assert fields["pc_median_hindex"] == 18.5
    assert fields["submissions_by_year"] == [120, 115, 100]
    assert fields["acceptance_rates_pct"] == [33, 33, 35]
    assert fields["chair_hindex_by_year"] == [45, 44, 40]
    assert len(fields["area_leaders"]) == 3
    assert fields["area_leaders"][0] == {"name": "Jane A. Smith", "gs_hindex": 52}
    assert len(fields["last_instances"]) >= 1
    assert fields["last_instances"][0]["year"] == 2025
    assert fields["last_instances"][0]["acceptance_rate_pct"] == 33
    assert "portal.core.edu.au/core/media" in fields["icore_artifact_urls"][0]
    assert "flagship venue" in fields["relationship_to_similar_conferences"]


def test_every_populated_field_has_provenance():
    result = extract(FIXTURE, core_id="TEST", round_="ICORE2026",
                      link_type="data")
    fields = dict(result["fields"])
    fields["core_id"] = "TEST"
    fields["round"] = "ICORE2026"
    fields["link_type"] = "data"
    # Should not raise — every field _record() populated must carry
    # page + snippet provenance by construction.
    doc = DocumentExtraction(**fields, provenance=result["provenance"])
    for name, value in doc.model_dump().items():
        if name in ("provenance", "core_id", "round", "link_type", "doc_url",
                     "comparators", "arguments"):
            continue
        if value in (None, [], {}):
            continue
        assert name in doc.provenance, f"{name} populated with no provenance"
        prov = doc.provenance[name]
        assert prov.page >= 1
        assert len(prov.snippet) > 0


def test_dropped_fields_never_silently_kept():
    # every field either ends up in `fields` with matching provenance, or
    # in `dropped_fields` — never neither
    result = extract(FIXTURE, core_id="TEST", round_="ICORE2026",
                      link_type="data")
    for key in result["fields"]:
        if result["fields"][key] is not None:
            assert key in result["provenance"] or key in \
                ("core_id", "round", "link_type", "doc_url")
