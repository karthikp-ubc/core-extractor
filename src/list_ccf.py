#!/usr/bin/env python3
"""Map a CCF ranking list onto CORE conferences, and list what to fetch.

The rest of the pipeline is keyed by CORE `core_id` and normally scoped to
one CORE FoR code. CCF's list (e.g. CCF_Ranking_2026.json) spans many FoRs
and carries no core_id — only an abbreviation, a name, and a DBLP URL. This
helper bridges the two, as an alternative to list_detail_pages.py's
"FoR + min-rank" selection:

  1. Filters the CCF list to one rank (default: A).
  2. Resolves each venue to a CORE core_id, in order of reliability:
       dblp      — DBLP key matches a collected detail page
                   (conference_detail.parquet). Authoritative.
       search_dblp — DBLP key matches a row of a saved CORE search page
                   (inputs/ccf_search/*.html — the search table shows each
                   venue's DBLP link). Reliable; picks the right row even
                   when an acronym search returns many venues.
       acronym   — abbreviation matches a row in core_rankings.parquet
                   (any FoR loaded via stage1_core.py --append). Unverified
                   until its detail page is collected; a later run then
                   upgrades it to `dblp` or flags `dblp_mismatch`.
  3. Writes, for anything not yet collected:
       - the CORE detail-page URL, if core_id is known, or
       - a CORE search URL (by acronym) to find it, if not.

Like the other helpers, this never fetches anything (portal.core.edu.au's
robots.txt disallows AI-agent user-agents — see stage1_core.py). It writes
URL lists, and with --emit-curl a script for YOU to review and run.

After saving search pages, rerun this script: venues it can pin down by
DBLP link move to "detail_page_needed", and it writes
inputs/ccf_<rank>_master.csv — just those venues' rows, in Stage 1's
positional Export-CSV layout — to load with
    stage1_core.py inputs/ccf_A_master.csv --round ICORE2026 --append
(so unrelated search hits, e.g. EACL from an "ACL" search, never enter
the cohort).
"""
import argparse
import json
import re
import shlex
import sys
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import load_config, REPO_ROOT  # noqa: E402
from stage4_join import dblp_key_from_url  # noqa: E402

PORTAL = "https://portal.core.edu.au/conf-ranks/"

STATUS_COLUMNS = ["ccf_abbr", "ccf_name", "ccf_category", "ccf_rank",
                  "dblp_key", "core_id", "core_acronym", "core_rank",
                  "match", "status", "url", "target_path"]

# CCF abbreviations whose CORE acronym/search term differs. Only used to
# build search terms and acronym matches — DBLP matching doesn't need it.
SEARCH_TERM_OVERRIDES = {
    "ACM SIGOPS ATC （原 USENIX ATC）": "USENIX",
    "S&P": "SP",
    "USENIX Security": "USENIX-Security",
    "SIGKDD": "KDD",
    "ACM MM": "ACMMM",
    "IEEE VIS": "VIS",
}


def norm_acronym(s):
    return re.sub(r"[^A-Z0-9]", "", str(s).upper()) if s else ""


def search_term(abbr):
    return SEARCH_TERM_OVERRIDES.get(abbr, abbr)


def search_url(term, round_):
    # Query shape confirmed from a link in a saved detail page
    # (?search=4606&by=for&source=ICORE2026&sort=atitle&page=1);
    # `by=acronym` is the portal's acronym-search option.
    return PORTAL + "?" + urlencode({"search": term, "by": "acronym",
                                     "source": round_, "sort": "atitle",
                                     "page": 1})


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_") or "venue"


NAVIGATE_RE = re.compile(r"/conf-ranks/(\d+)/")
SEARCH_COLUMNS = ["core_id", "title", "acronym", "source", "rank",
                  "dblp_key", "for_code", "search_file"]


def parse_search_page(path):
    """Rows of one saved CORE search-results page. The core_id is only in
    each <tr>'s onclick="navigate('/conf-ranks/{id}/')", and the DBLP
    column's value is the href of its "view" link."""
    soup = BeautifulSoup(Path(path).read_text(encoding="utf-8", errors="replace"),
                         "html.parser")
    out = []
    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        if not trs:
            continue
        headers = [h.get_text(strip=True).lower() for h in trs[0].find_all(["th", "td"])]
        for tr in trs[1:]:
            m = NAVIGATE_RE.search(tr.get("onclick", ""))
            if not m:
                continue
            cells = dict(zip(headers, tr.find_all("td")))
            text = lambda h: cells[h].get_text(strip=True) if h in cells else None
            a = cells["dblp"].find("a", href=True) if "dblp" in cells else None
            out.append({"core_id": m.group(1), "title": text("title"),
                        "acronym": text("acronym"), "source": text("source"),
                        "rank": text("rank"),
                        "dblp_key": dblp_key_from_url(a["href"]) if a else None,
                        "for_code": text("primary for"),
                        "search_file": Path(path).name})
    return out


def load_search_pages(directory):
    rows = []
    for f in sorted(Path(directory).glob("*.htm*")):
        rows += parse_search_page(f)
    return pd.DataFrame(rows, columns=SEARCH_COLUMNS)


def load_ccf(path, rank):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    cats = {c["id"]: c["english"] for c in d.get("category", [])}
    return [dict(c, category=cats.get(c["category_id"], ""))
            for c in d["conferences"] if c["rank"] == rank]


def resolve(ccf, detail, rankings, search=None):
    """Returns one status row per CCF venue (without url/target_path)."""
    if search is None:
        search = pd.DataFrame(columns=SEARCH_COLUMNS)
    search = search.drop_duplicates(["core_id", "source"])
    search_info = search.drop_duplicates("core_id").set_index("core_id")
    detail = detail.copy()
    detail["core_id"] = detail["core_id"].astype(str)
    detail["dblp_key"] = detail["dblp_source_url"].map(dblp_key_from_url)
    by_dblp = (detail.dropna(subset=["dblp_key"])
               .drop_duplicates("core_id")
               .groupby("dblp_key")["core_id"].apply(list).to_dict())
    detail_key = (detail.dropna(subset=["dblp_key"])
                  .drop_duplicates("core_id")
                  .set_index("core_id")["dblp_key"].to_dict())
    collected = set(detail["core_id"])

    rankings = rankings.copy()
    rankings["core_id"] = rankings["core_id"].astype(str)
    rankings = rankings.drop_duplicates("core_id")
    rankings["acr_norm"] = rankings["acronym"].map(norm_acronym)
    rank_info = rankings.set_index("core_id")[["acronym", "rank"]].to_dict("index")

    rows = []
    for c in ccf:
        key = dblp_key_from_url(c.get("url"))
        row = {"ccf_abbr": c["abbr"], "ccf_name": c["name"],
               "ccf_category": c["category"], "ccf_rank": c["rank"],
               "dblp_key": key, "core_id": None, "core_acronym": None,
               "core_rank": None, "match": None, "status": None}

        dblp_hits = by_dblp.get(key, []) if key else []
        if len(dblp_hits) == 1:
            row.update(core_id=dblp_hits[0], match="dblp", status="collected")
        elif len(dblp_hits) > 1:
            row.update(match="dblp", status="ambiguous",
                       core_id=";".join(dblp_hits))
        elif key and (search["dblp_key"] == key).any():
            hits = search[search["dblp_key"] == key]["core_id"].unique().tolist()
            if len(hits) == 1:
                cid = hits[0]
                row.update(core_id=cid, match="search_dblp",
                           status="collected" if cid in collected
                           else "detail_page_needed")
            else:
                row.update(match="search_dblp", status="ambiguous",
                           core_id=";".join(hits))
        else:
            terms = {norm_acronym(c["abbr"]), norm_acronym(search_term(c["abbr"]))}
            hits = rankings[rankings["acr_norm"].isin(terms - {""})]
            if hits.empty and len(search):
                # Saved search pages had no DBLP match: fall back to an exact
                # acronym hit there, flagged for review rather than trusted.
                s_hits = search[search["acronym"].map(norm_acronym).isin(terms - {""})]
                s_hits = s_hits.drop_duplicates("core_id")
                if len(s_hits):
                    row.update(match="search_acronym", status="review_no_dblp_match",
                               core_id=";".join(s_hits["core_id"]))
            if len(hits) == 1:
                cid = hits.iloc[0]["core_id"]
                row.update(core_id=cid, match="acronym")
                if cid in collected:
                    # Detail page exists but its DBLP link disagrees with
                    # CCF's — the acronym match is probably a different venue.
                    row["status"] = "dblp_mismatch"
                    row["core_dblp_key"] = detail_key.get(cid)
                else:
                    row["status"] = "detail_page_needed"
            elif len(hits) > 1:
                row.update(match="acronym", status="ambiguous",
                           core_id=";".join(hits["core_id"]))
            elif row["status"] is None:
                row["status"] = "search_needed"

        cid = row["core_id"]
        if cid and ";" not in cid and cid in rank_info:
            row["core_acronym"] = rank_info[cid]["acronym"]
            row["core_rank"] = rank_info[cid]["rank"]
        elif cid and ";" not in cid and cid in search_info.index:
            row["core_acronym"] = search_info.at[cid, "acronym"]
            row["core_rank"] = search_info.at[cid, "rank"]
        rows.append(row)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ccf-json", type=Path,
                        default=REPO_ROOT / "CCF_Ranking_2026.json")
    parser.add_argument("--rank", default="A", choices=["A", "B", "C"],
                        help="CCF rank to select (default: A).")
    parser.add_argument("--round", default=None,
                        help="CORE round for search URLs (default: first "
                             "of config.yaml's in_scope_rounds).")
    parser.add_argument("--search-dir", type=Path, default=None,
                        help="Saved CORE search pages (default: "
                             "<inputs_dir>/ccf_search).")
    parser.add_argument("--default-chart-for", default="4606",
                        help="Chart FoR for venues NOT identified via the "
                             "CCF search pages (i.e. the original FoR "
                             "cohort), when it's among their FoR codes. "
                             "Default: 4606.")
    parser.add_argument("--emit-curl", action="store_true",
                        help="Also write data/ccf_download_commands.sh for "
                             "you to review and run yourself.")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config) if args.config else load_config()
    data_dir = cfg.resolve("data_dir")
    inputs_dir = cfg.resolve("inputs_dir")
    round_ = args.round or (cfg.in_scope_rounds[0] if cfg.in_scope_rounds
                            else "ICORE2026")

    detail_path = data_dir / "conference_detail.parquet"
    rankings_path = data_dir / "core_rankings.parquet"
    detail = (pd.read_parquet(detail_path) if detail_path.exists() else
              pd.DataFrame(columns=["core_id", "dblp_source_url"]))
    rankings = (pd.read_parquet(rankings_path) if rankings_path.exists() else
                pd.DataFrame(columns=["core_id", "acronym", "rank", "detail_url"]))

    search_dir = args.search_dir or (inputs_dir / "ccf_search")
    search = (load_search_pages(search_dir) if search_dir.is_dir()
              else pd.DataFrame(columns=SEARCH_COLUMNS))

    ccf = load_ccf(args.ccf_json, args.rank)
    rows = resolve(ccf, detail, rankings, search)

    detail_urls = (rankings.assign(core_id=rankings["core_id"].astype(str))
                   .drop_duplicates("core_id")
                   .set_index("core_id")["detail_url"].to_dict())
    for r in rows:
        if r["status"] == "detail_page_needed":
            cid = r["core_id"]
            r["url"] = detail_urls.get(cid) or f"{PORTAL}{cid}/"
            acr = safe_name(r["core_acronym"] or r["ccf_abbr"])
            r["target_path"] = str(inputs_dir / f"{acr}_{cid}.html")
        elif r["status"] == "search_needed":
            term = search_term(r["ccf_abbr"])
            r["url"] = search_url(term, round_)
            r["target_path"] = str(inputs_dir / "ccf_search" /
                                   f"{safe_name(term)}.html")
        else:
            r["url"] = r["target_path"] = None

    tag = f"ccf_{args.rank}"
    df = pd.DataFrame(rows)

    # Stage 1 input for venues identified via search pages, in the real
    # Export CSV's positional layout (core_id, title, acronym, source_round,
    # rank, <dropped flag>, for_code...). Only the matched venues' rows.
    from_search = df[df["match"] == "search_dblp"]["core_id"].dropna()
    from_search = [c for c in from_search if ";" not in c]
    master = (search[search["core_id"].isin(from_search) &
                     (search["source"] == round_)]
              .drop_duplicates("core_id"))
    master_csv = inputs_dir / f"{tag}_master.csv"
    if len(master):
        out = master[["core_id", "title", "acronym", "source", "rank"]].copy()
        out.insert(5, "flag", "")
        out["for_code"] = master["for_code"]
        out.to_csv(master_csv, index=False, header=False)
    missing_round = sorted(set(from_search) - set(master["core_id"]))
    if missing_round:
        print(f"warning: no {round_} row in the search pages for core_id(s) "
              f"{missing_round}; left out of {master_csv.name}", file=sys.stderr)
    df = df[STATUS_COLUMNS + [c for c in df.columns if c not in STATUS_COLUMNS]]
    status_csv = data_dir / f"{tag}_status.csv"
    df.to_csv(status_csv, index=False)

    # Which FoR's chart Stage 2 should read per venue (see stage2_metrics.py
    # --chart-for-csv): CORE's primary FoR for venues found via search
    # pages; --default-chart-for for the original cohort.
    primary = (search[search["source"] == round_].drop_duplicates("core_id")
               .set_index("core_id")["for_code"].to_dict())
    chart_for = []
    for cid, codes in (rankings.assign(core_id=rankings["core_id"].astype(str))
                       .drop_duplicates("core_id")[["core_id", "for_code"]]
                       .itertuples(index=False)):
        code_list = [c.strip() for c in str(codes or "").split(",") if c.strip()]
        # Original-cohort rows carry their full FoR list (incl. the default);
        # rows loaded from ccf_*_master.csv carry only the primary FoR.
        if args.default_chart_for in code_list:
            chart_for.append((cid, args.default_chart_for, "default_chart_for"))
        elif cid in primary:
            chart_for.append((cid, primary[cid], "ccf_search_primary_for"))
        else:
            chart_for.append((cid, None, "unmapped_keep_all"))
    chart_for_csv = data_dir / "chart_for_code.csv"
    pd.DataFrame(chart_for, columns=["core_id", "for_code", "source"]) \
        .to_csv(chart_for_csv, index=False)

    detail_rows = df[df["status"] == "detail_page_needed"]
    search_rows = df[df["status"] == "search_needed"]
    review_rows = df[df["status"].isin(["ambiguous", "dblp_mismatch",
                                        "review_no_dblp_match"])]

    urls_txt = data_dir / f"{tag}_urls.txt"
    lines = [f"# CCF-{args.rank} venues -> CORE pages to save by hand "
             f"(generated by list_ccf.py; see {status_csv.name}).", ""]
    lines.append(f"# 1. Detail pages ({len(detail_rows)}) — save as .html "
                 f"into {inputs_dir}/, then: stage1b_detail.py inputs/*.html --append")
    lines += [f"{r.url}\t{r.ccf_abbr}" for r in detail_rows.itertuples()]
    lines += ["", f"# 2. Search pages ({len(search_rows)}) — core_id not known "
              f"yet. Save each result page (or note the core_id it shows), "
              f"run stage1_core.py <file> --round {round_} --append, then "
              f"rerun list_ccf.py. Search terms may need adjusting by hand."]
    lines += [f"{r.url}\t{r.ccf_abbr}" for r in search_rows.itertuples()]
    if len(review_rows):
        lines += ["", f"# 3. Need manual review ({len(review_rows)}) — see "
                  f"`status` in {status_csv.name}"]
        lines += [f"# {r.ccf_abbr}: {r.status} (core_id {r.core_id})"
                  for r in review_rows.itertuples()]
    urls_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")

    counts = df["status"].value_counts().to_dict()
    print(f"CCF-{args.rank}: {len(df)} conferences -> {counts}", file=sys.stderr)
    for r in df.sort_values("status").astype(object).where(df.notna(), None).itertuples():
        print(f"  {r.status:<19s} {r.ccf_abbr[:22]:<22s} core_id={r.core_id or '-':<6} "
              f"{r.url or ''}", file=sys.stderr)
    print(f"\nstatus: {status_csv}\nurls:   {urls_txt}", file=sys.stderr)
    if len(master):
        print(f"stage 1 input ({len(master)} venues): {master_csv}\n"
              f"  -> stage1_core.py {master_csv} --round {round_} --append",
              file=sys.stderr)

    if args.emit_curl:
        sh = data_dir / "ccf_download_commands.sh"
        out = ["#!/bin/sh",
               "# Generated by list_ccf.py — NOT run automatically by anything",
               "# in this project. portal.core.edu.au's robots.txt disallows",
               "# AI-agent user-agents; these are for YOU to review and run.",
               "# Spot-check the first saved page before running the rest.",
               "set -e",
               f"mkdir -p {shlex.quote(str(inputs_dir / 'ccf_search'))}"]
        for r in pd.concat([detail_rows, search_rows]).itertuples():
            out.append(f"curl -sS --fail -L -o {shlex.quote(r.target_path)} "
                       f"{shlex.quote(r.url)} && sleep 1")
        sh.write_text("\n".join(out) + "\n", encoding="utf-8")
        print(f"curl script: {sh} — review before running.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
