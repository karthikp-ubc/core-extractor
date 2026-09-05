"""Regression tests for stage1_core.py's real, headerless Export CSV
format (see inputs/4606-all.csv) — SPEC.md §9: fixtures only, no network.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from stage1_core import parse_csv, build_output  # noqa: E402

MASTER_CSV = Path(__file__).resolve().parent.parent / "inputs" / "4606-all.csv"


def test_detects_headerless_positional_format():
    df, issues, extraction_path, headers = parse_csv(MASTER_CSV)
    assert extraction_path == "csv_export_positional"
    assert len(df) == 211
    assert not any("unlabelled_flag" in str(i) for i in issues)


def test_core_id_and_detail_url_populated():
    df, issues, extraction_path, headers = parse_csv(MASTER_CSV)
    row = df[df["core_id"] == "11"].iloc[0]
    assert row["acronym"] == "SIGCOMM"
    assert row["detail_url"] == "https://portal.core.edu.au/conf-ranks/11/"


def test_multi_for_code_preserved():
    df, issues, extraction_path, headers = parse_csv(MASTER_CSV)
    row = df[df["core_id"] == "30"].iloc[0]  # ICS, known multi-FoR row
    assert row["for_code"] == "CSE,4606"


def test_round_mismatch_flagged_not_silently_overwritten():
    df, issues, extraction_path, headers = parse_csv(MASTER_CSV)
    out, mapping = build_output(df, headers, "4606", "Distributed computing",
                                  "CORE2023", issues)
    # every real row's own source_round is ICORE2026, but we asked for
    # CORE2023 — should be flagged, not silently accepted
    assert any("source_round" in i["issue"] for i in issues)
    assert (out["source_round"] == "CORE2023").all()
