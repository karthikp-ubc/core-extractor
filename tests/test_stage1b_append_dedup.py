"""Regression tests for two --append dedup bugs found by actually running
the pipeline against real data (not caught by inspection):

1. core_id is a Python str when freshly parsed but round-trips through
   links.csv as int64 — "11" != 11 defeats dedup across appends.
2. A conference cross-listed under multiple FoR codes gets one h_index/
   citation chart PER FoR code (same core_id/round/link_type/doc_index,
   different url) — deduping without `url` in the subset would treat
   those as duplicates and silently drop a real, distinct link.
"""
import subprocess
import sys
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parent.parent / "src"


def run_stage1b(args, cwd):
    return subprocess.run(
        [sys.executable, str(SRC / "stage1b_detail.py"), *args],
        cwd=cwd, capture_output=True, text=True)


FIXTURE_HTML = """\
<html><body>
<form action="/conf-ranks/{cid}/"></form>
<a href="https://dblp.uni-trier.de/db/conf/fict">dblp</a>
Source: ICORE2026
Rank: A
Field Of Research: 4606 - Distributed computing
(<a href="https://portal.core.edu.au/core/media/2025/centiles_graphs/FICT_{cid}_h_index_FOR4606_centileGraph.png">h-index</a>)
(<a href="https://portal.core.edu.au/core/media/2025/centiles_graphs/FICT_{cid}_cited_FOR4606_centileGraph.png">citation</a>)
(<a href="https://portal.core.edu.au/core/media/2025/centiles_graphs/FICT_{cid}_h_index_FORCSE_centileGraph.png">h-index</a>)
(<a href="https://portal.core.edu.au/core/media/2025/centiles_graphs/FICT_{cid}_cited_FORCSE_centileGraph.png">citation</a>)
</body></html>
"""


def test_multi_for_charts_not_collapsed_and_append_matches_int_core_id(tmp_path):
    html_path = tmp_path / "fict.html"
    html_path.write_text(FIXTURE_HTML.format(cid="999"))
    out_dir = tmp_path / "data"

    # first pass: plain write (no --append)
    r1 = run_stage1b([str(html_path), "--out-dir", str(out_dir)], tmp_path)
    assert r1.returncode == 0, r1.stderr
    links = pd.read_csv(out_dir / "links.csv")
    assert len(links) == 4  # two link_types x two FoR codes, all distinct

    # second pass: --append the SAME page again — should upsert to the same
    # 4 rows, not double them, even though links.csv round-trips core_id as
    # int64 while the fresh parse produces a str "999"
    r2 = run_stage1b([str(html_path), "--out-dir", str(out_dir), "--append"],
                      tmp_path)
    assert r2.returncode == 0, r2.stderr
    links2 = pd.read_csv(out_dir / "links.csv")
    assert len(links2) == 4, \
        f"expected 4 rows after re-appending the same page, got {len(links2)}"

    detail2 = pd.read_parquet(out_dir / "conference_detail.parquet")
    assert len(detail2) == 1, \
        f"expected 1 rank-history row after re-appending, got {len(detail2)}"
