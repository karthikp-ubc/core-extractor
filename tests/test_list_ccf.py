import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from list_ccf import resolve  # noqa: E402
from stage4_join import dblp_key_from_url  # noqa: E402


@pytest.mark.parametrize("url,key", [
    ("https://dblp.uni-trier.de/db/conf/sigcomm", "conf/sigcomm"),
    ("http://dblp.uni-trier.de/db/conf/sigcomm/", "conf/sigcomm"),
    ("http://dblp.uni-trier.de/db/conf/sigcomm/index.html", "conf/sigcomm"),
    ("https://dblp.org/db/conf/aft/index.html", "conf/aft"),
    ("https://dblp.org/db/conf/usenix/index.html?x=1", "conf/usenix"),
    (None, None),
])
def test_dblp_key_strips_index_html(url, key):
    assert dblp_key_from_url(url) == key


def ccf(abbr, url):
    return {"abbr": abbr, "name": abbr, "category": "", "rank": "A", "url": url}


def test_resolve_statuses():
    detail = pd.DataFrame({
        "core_id": ["11", "11", "99"],
        "dblp_source_url": ["https://dblp.uni-trier.de/db/conf/sigcomm"] * 2
                           + ["https://dblp.org/db/conf/other"],
    })
    rankings = pd.DataFrame({
        "core_id": ["11", "1789", "99"],
        "acronym": ["SIGCOMM", "NSDI", "FM"],
        "rank": ["A*", "National: USA", "B"],
        "detail_url": [None] * 3,
    })
    rows = resolve([
        ccf("SIGCOMM", "http://dblp.uni-trier.de/db/conf/sigcomm/index.html"),
        ccf("NSDI", "http://dblp.uni-trier.de/db/conf/nsdi/"),
        ccf("FM", "http://dblp.uni-trier.de/db/conf/fm/"),
        ccf("ICML", "http://dblp.uni-trier.de/db/conf/icml/"),
    ], detail, rankings)
    got = {r["ccf_abbr"]: (r["status"], r["core_id"]) for r in rows}
    assert got == {
        "SIGCOMM": ("collected", "11"),
        "NSDI": ("detail_page_needed", "1789"),
        "FM": ("dblp_mismatch", "99"),      # acronym hit, DBLP disagrees
        "ICML": ("search_needed", None),
    }


SEARCH_HTML = """<html><body><table>
<tr><th><b>Title</b></th><th><b>Acronym</b></th><th><b>Source</b></th>
<th><b>Rank</b></th><th><b>Note</b></th><th><b>DBLP</b></th>
<th><b>Primary FoR</b></th></tr>
<tr class="evenrow" onclick="navigate('/conf-ranks/196/')">
<td>Association for Computational Linguistics</td><td>ACL</td><td>ICORE2026</td>
<td>A*</td><td>none</td><td><a href="https://dblp.uni-trier.de/db/conf/acl"><div>view</div></a></td>
<td>4602</td></tr>
<tr class="oddrow" onclick="navigate('/conf-ranks/468/')">
<td>European Association for Computational Linguistics</td><td>EACL</td><td>ICORE2026</td>
<td>A</td><td>none</td><td><a href="https://dblp.uni-trier.de/db/conf/eacl"><div>view</div></a></td>
<td>4602</td></tr>
</table></body></html>"""


def test_search_page_dblp_match_picks_right_row(tmp_path):
    from list_ccf import load_search_pages
    (tmp_path / "ACL.html").write_text(SEARCH_HTML)
    search = load_search_pages(tmp_path)
    assert search["core_id"].tolist() == ["196", "468"]
    assert search["dblp_key"].tolist() == ["conf/acl", "conf/eacl"]

    empty = pd.DataFrame(columns=["core_id", "dblp_source_url"])
    rows = resolve([ccf("ACL", "http://dblp.uni-trier.de/db/conf/acl/")],
                   empty, pd.DataFrame(columns=["core_id", "acronym", "rank"]),
                   search)
    assert (rows[0]["status"], rows[0]["core_id"], rows[0]["match"]) == \
        ("detail_page_needed", "196", "search_dblp")


def test_stage1_parses_onclick_rows(tmp_path):
    from stage1_core import parse_html_table
    f = tmp_path / "ACL.html"
    f.write_text(SEARCH_HTML)
    df, issues, _, _ = parse_html_table(f)
    assert df["core_id"].tolist() == ["196", "468"]
    assert df["detail_url"].iloc[0] == "https://portal.core.edu.au/conf-ranks/196/"
