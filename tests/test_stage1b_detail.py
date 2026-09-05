"""Regression tests against the real saved detail pages in inputs/ — these
are recorded fixtures (SPEC.md §9: network layer tested against recorded
fixtures only), not live pages. Covers two real bugs found and fixed while
building this pipeline: DBLP Source being plain text (not a link) on every
real page, and "unranked: merged" not being recognized as a rank value.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from stage1b_detail import parse_detail_page  # noqa: E402

INPUTS = Path(__file__).resolve().parent.parent / "inputs"


def test_dblp_source_found_as_plain_text():
    detail, links, review = parse_detail_page(INPUTS / "sigcomm.html", None)
    assert review["dblp_source_url"] == \
        "https://dblp.uni-trier.de/db/conf/sigcomm"
    assert all(d["dblp_source_url"] == review["dblp_source_url"] for d in detail)


def test_unranked_merged_captured_as_rank():
    detail, links, review = parse_detail_page(INPUTS / "sensys.html", None)
    icore2026 = next(d for d in detail if d["round"] == "ICORE2026")
    assert icore2026["rank"] == "unranked: merged"


def test_core2008_for_code_genuinely_absent_not_a_bug():
    # CORE2008 detail blocks have no "Field Of Research:" line at all on
    # the real page — confirmed by inspection, not a parser gap.
    detail, links, review = parse_detail_page(INPUTS / "sigcomm.html", None)
    core2008 = next(d for d in detail if d["round"] == "CORE2008")
    assert core2008["for_code"] is None
    assert any(i["round"] == "CORE2008" for i in review["issues"])


def test_all_three_real_pages_resolve_dblp():
    for name in ["sigcomm.html", "sensys.html", "conext.html"]:
        _, _, review = parse_detail_page(INPUTS / name, None)
        assert review["dblp_source_url"] is not None, name
