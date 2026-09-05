"""End-to-end test of stage2b_docs.py's batch driver (not just
extract_icore.py's core logic) against the synthetic fixture, with --no-llm
so it needs no network/API access. Covers: local-file lookup by the
{acronym}/{round}_{link_type}_{n}.pdf convention, documents.csv/
extracted.parquet/verify_worklist.csv/argument_matrix.csv all getting
written, and a genuinely-missing document landing in unmatched.csv instead
of crashing the batch.
"""
import shutil
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import stage2b_docs  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "sample_data_doc.pdf"


def make_repo(tmp_path):
    pdfs_dir = tmp_path / "pdfs" / "FICT"
    pdfs_dir.mkdir(parents=True)
    shutil.copy(FIXTURE, pdfs_dir / "ICORE2026_data_1.pdf")
    # deliberately no CORE2023_decision_1.pdf — should land in unmatched.csv

    links_csv = tmp_path / "links.csv"
    pd.DataFrame([
        {"core_id": "TEST", "round": "ICORE2026", "link_type": "data",
         "doc_index": 1, "url": "https://example.org/data1.pdf"},
        {"core_id": "TEST", "round": "CORE2023", "link_type": "decision",
         "doc_index": None, "url": "https://example.org/decision1.pdf"},
    ]).to_csv(links_csv, index=False)

    rankings_parquet = tmp_path / "core_rankings.parquet"
    pd.DataFrame([{"core_id": "TEST", "acronym": "FICT", "title": "Fixture Conf"}]) \
        .to_parquet(rankings_parquet, index=False)

    return links_csv, rankings_parquet, tmp_path / "pdfs", tmp_path


def test_batch_driver_end_to_end(tmp_path):
    links_csv, rankings_parquet, pdfs_dir, out_dir = make_repo(tmp_path)

    rc = stage2b_docs.main([
        "--links-csv", str(links_csv),
        "--rankings-parquet", str(rankings_parquet),
        "--pdfs-dir", str(pdfs_dir),
        "--out-dir", str(out_dir),
        "--extracted-dir", str(out_dir / "extracted"),
        "--no-llm",
    ])
    assert rc == 0

    docs = pd.read_csv(out_dir / "documents.csv")
    assert len(docs) == 1
    assert docs.iloc[0]["acronym"] == "FICT"
    assert docs.iloc[0]["sha256"]

    extracted = pd.read_parquet(out_dir / "extracted.parquet")
    assert len(extracted) == 1
    assert extracted.iloc[0]["current_rank"] == "A"

    unmatched = pd.read_csv(out_dir / "unmatched.csv")
    assert len(unmatched) == 1
    assert unmatched.iloc[0]["round"] == "CORE2023"
    assert "not found locally" in unmatched.iloc[0]["reason"]

    worklist = pd.read_csv(out_dir / "verify_worklist.csv")
    # with --no-llm every captured field is method=regex/confidence=high,
    # so the worklist should only contain the 10% high-confidence sample,
    # never empty given how many fields the fixture populates
    assert len(worklist) > 0

    # no LLM call requested -> no arguments coded
    matrix = pd.read_csv(out_dir / "argument_matrix.csv")
    assert len(matrix) == 0


def test_non_pdf_file_logged_not_crashed(tmp_path):
    links_csv, rankings_parquet, pdfs_dir, out_dir = make_repo(tmp_path)
    # corrupt the "PDF" into an HTML error page saved with the right name
    bad_path = pdfs_dir / "FICT" / "ICORE2026_data_1.pdf"
    bad_path.write_text("<html><body>404 not found</body></html>")

    rc = stage2b_docs.main([
        "--links-csv", str(links_csv),
        "--rankings-parquet", str(rankings_parquet),
        "--pdfs-dir", str(pdfs_dir),
        "--out-dir", str(out_dir),
        "--extracted-dir", str(out_dir / "extracted"),
        "--no-llm",
    ])
    assert rc == 0
    unmatched = pd.read_csv(out_dir / "unmatched.csv")
    assert any("not a PDF" in r for r in unmatched["reason"])
