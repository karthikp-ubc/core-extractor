"""export_combined.py: analysis.parquet + extracted/*.json -> one flat CSV,
one row per (core_id, round), Data/Decision fields merged per venue-round.
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from export_combined import load_extracted_docs, main  # noqa: E402


def write_doc(path, **fields):
    base = {"core_id": "1", "round": "ICORE2026", "link_type": "data",
            "doc_url": "https://example.org/doc.pdf", "current_rank": None,
            "requested_rank": None, "outcome": None, "outcome_verbatim": None,
            "for_codes": [], "area_leaders": [], "arguments": [],
            "provenance": {}}
    base.update(fields)
    path.write_text(json.dumps(base))


def test_data_and_decision_docs_merge_into_one_row(tmp_path):
    write_doc(tmp_path / "1_ICORE2026_data_1.json", link_type="data",
              current_rank="A", pc_size=50,
              area_leaders=[{"name": "Jane Doe", "gs_hindex": 40}])
    write_doc(tmp_path / "1_ICORE2026_decision_1.json", link_type="decision",
              doc_url="https://example.org/decision.pdf",
              outcome="A", outcome_verbatim="Conference to be ranked as A")

    docs = load_extracted_docs(tmp_path)
    assert len(docs) == 1  # merged into one row for (core_id=1, round=ICORE2026)
    row = docs.iloc[0]
    assert row["current_rank"] == "A"
    assert row["outcome"] == "A"
    assert row["pc_size"] == 50
    assert "doc.pdf" in row["document_urls"] and "decision.pdf" in row["document_urls"]
    assert json.loads(row["area_leaders"]) == [{"name": "Jane Doe", "gs_hindex": 40}]


def test_empty_list_becomes_blank_not_bracket_string(tmp_path):
    write_doc(tmp_path / "1_ICORE2026_data_1.json", for_codes=[])
    docs = load_extracted_docs(tmp_path)
    assert pd.isna(docs.iloc[0]["for_codes"])


def test_no_docs_at_all_returns_empty_frame_with_join_keys(tmp_path):
    docs = load_extracted_docs(tmp_path)
    assert list(docs.columns[:2]) == ["core_id", "round"]
    assert len(docs) == 0


def test_end_to_end_output_has_no_duplicate_outcome_column(tmp_path):
    analysis_path = tmp_path / "analysis.parquet"
    pd.DataFrame([
        {"core_id": "1", "round": "ICORE2026", "acronym": "FICT",
         "rank": "A", "outcome": "stale-should-be-dropped"},
    ]).to_parquet(analysis_path, index=False)

    extracted_dir = tmp_path / "extracted"
    extracted_dir.mkdir()
    write_doc(extracted_dir / "1_ICORE2026_decision_1.json",
              link_type="decision", outcome="B")

    out_csv = tmp_path / "combined.csv"
    rc = main(["--analysis-parquet", str(analysis_path),
               "--extracted-dir", str(extracted_dir),
               "--out-csv", str(out_csv)])
    assert rc == 0

    out = pd.read_csv(out_csv)
    assert list(out.columns).count("outcome") == 1
    assert out.iloc[0]["outcome"] == "B"


def test_round_filter_writes_separate_file_with_default_name(tmp_path):
    analysis_path = tmp_path / "analysis.parquet"
    pd.DataFrame([
        {"core_id": "1", "round": "ICORE2026", "acronym": "FICT", "rank": "A"},
        {"core_id": "1", "round": "CORE2023", "acronym": "FICT", "rank": "A"},
    ]).to_parquet(analysis_path, index=False)
    extracted_dir = tmp_path / "extracted"
    extracted_dir.mkdir()

    rc = main(["--analysis-parquet", str(analysis_path),
               "--extracted-dir", str(extracted_dir),
               "--round", "CORE2023",
               "--out-csv", str(tmp_path / "core2023_only.csv")])
    assert rc == 0
    out = pd.read_csv(tmp_path / "core2023_only.csv")
    assert len(out) == 1
    assert out.iloc[0]["round"] == "CORE2023"


def test_unknown_round_is_a_clear_error(tmp_path):
    analysis_path = tmp_path / "analysis.parquet"
    pd.DataFrame([{"core_id": "1", "round": "ICORE2026", "acronym": "FICT"}]) \
        .to_parquet(analysis_path, index=False)
    extracted_dir = tmp_path / "extracted"
    extracted_dir.mkdir()

    rc = main(["--analysis-parquet", str(analysis_path),
               "--extracted-dir", str(extracted_dir),
               "--round", "ICORE2023",
               "--out-csv", str(tmp_path / "out.csv")])
    assert rc == 1
    assert not (tmp_path / "out.csv").exists()
