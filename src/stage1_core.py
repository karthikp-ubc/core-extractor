#!/usr/bin/env python3
"""Stage 1 — parse a locally-saved CORE rankings search-results page.

SPEC.md §6.1 calls for crawling https://portal.core.edu.au/conf-ranks/
directly. This project does not do that: the site's robots.txt explicitly
disallows `Claude-User` (and other AI-agent user-agents) from the entire
site, so an automated fetch from here would go against the operator's
stated policy. Instead, the user downloads the search-results page
themselves (saved HTML, or the Export CSV file if that turns out to exist)
and this script parses whichever was supplied.

Because the real page structure has never been inspected, column detection
is heuristic and defensive: every run writes a `.review.json` file
alongside the output showing exactly what was found, what was guessed, and
what couldn't be matched, so the parsing can be verified/calibrated against
real input before being trusted.
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

DETAIL_URL_RE = re.compile(r"/conf-ranks/(\d+)/?")

CANDIDATE_COLUMNS = {
    "title": ["title", "conference title", "name", "conference", "conference name"],
    "acronym": ["acronym", "short name", "abbreviation", "abbrev"],
    "rank": ["rank", "core rank", "current rank", "core2023 rank"],
    "for_code": ["for", "for code", "field of research", "field of research code"],
    "for_name": ["for name", "field of research name"],
}

OUTPUT_COLUMNS = ["core_id", "title", "acronym", "rank", "source_round",
                  "for_code", "for_name", "detail_url"]


def normalize_header(h):
    return re.sub(r"\s+", " ", h.strip().lower())


def best_match(headers, candidates):
    for cand in candidates:
        if cand in headers:
            return cand
    # loose substring fallback
    for cand in candidates:
        for h in headers:
            if cand in h:
                return h
    return None


def absolutize(url):
    if url.startswith("http"):
        return url
    if url.startswith("/"):
        return "https://portal.core.edu.au" + url
    return "https://portal.core.edu.au/" + url


CSV_ID_CANDIDATES = ["core_id", "id", "conf_id", "ranking_id"]
CSV_URL_CANDIDATES = ["url", "link", "detail_url", "profile", "conf-ranks"]

# Confirmed against a real "Export CSV" download (FoR 4606, ICORE2026): no
# header row at all. Columns, positionally:
#   0 core_id, 1 title, 2 acronym, 3 source_round, 4 rank,
#   5 unlabelled Yes/No flag — meaning unknown even to the user who pulled
#     this export; deliberately dropped rather than guessed at or carried
#     through unexplained,
#   6..N for_code_1..for_code_k — a *variable-length*, left-packed list of
#     FoR classifications for that conference (one of which is the FoR this
#     export was searched under; values seen include bare division tags
#     like "CSE" alongside numeric codes). Trailing empty cells pad short
#     lists to the widest row in the file.
HEADERLESS_FIXED_COLS = ["core_id", "title", "acronym", "source_round", "rank"]
_DROPPED_COL_INDEX = 5  # the unlabelled Yes/No flag; skipped, see above


def _looks_headerless(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        first_row = next(csv.reader(f), [])
    return bool(first_row) and first_row[0].strip().isdigit()


def parse_positional_csv(path):
    """Parse the real no-header Export CSV layout (see HEADERLESS_FIXED_COLS
    above). Column count beyond the fixed columns varies row to row (the
    for_code list), so read raw rather than via pandas' fixed-header path."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    issues = []
    n_fixed = len(HEADERLESS_FIXED_COLS) + 1  # +1 for the dropped flag column
    short_rows = [i for i, r in enumerate(rows) if len(r) < n_fixed]
    if short_rows:
        issues.append({
            "issue": f"{len(short_rows)} row(s) had fewer than "
                     f"{n_fixed} columns; skipped",
            "row_indices": short_rows[:10],
        })

    records = []
    for i, r in enumerate(rows):
        if len(r) < n_fixed:
            continue
        fixed_cells = r[:_DROPPED_COL_INDEX] + r[_DROPPED_COL_INDEX + 1:n_fixed]
        rec = dict(zip(HEADERLESS_FIXED_COLS, fixed_cells))
        for_codes = [c.strip() for c in r[n_fixed:] if c.strip()]
        rec["for_code"] = ",".join(for_codes) if for_codes else None
        rec["title"] = rec["title"].strip()
        rec["detail_url"] = absolutize(f"/conf-ranks/{rec['core_id']}/")
        records.append(rec)

    df = pd.DataFrame(records)
    headers = HEADERLESS_FIXED_COLS + ["for_code", "detail_url"]
    return df, issues, "csv_export_positional", headers


def parse_csv(path):
    if _looks_headerless(path):
        return parse_positional_csv(path)

    df = pd.read_csv(path)
    df.columns = [normalize_header(c) for c in df.columns]
    headers = list(df.columns)
    issues = []

    id_col = best_match(headers, CSV_ID_CANDIDATES)
    url_col = best_match(headers, CSV_URL_CANDIDATES)

    if url_col is not None:
        df["detail_url"] = df[url_col].astype(str).map(absolutize)
        extracted = df[url_col].astype(str).str.extract(DETAIL_URL_RE)[0]
        df["core_id"] = df[id_col] if id_col is not None else extracted
    elif id_col is not None:
        df["core_id"] = df[id_col]
        df["detail_url"] = df["core_id"].map(
            lambda v: absolutize(f"/conf-ranks/{v}/"))
    else:
        # SPEC.md §6.1 flags this as an open question: it's not established
        # that the Export CSV even carries a stable per-conference
        # identifier or link. Without one, core_id/detail_url can't be
        # populated from the CSV alone — don't guess, flag it instead.
        df["core_id"] = None
        df["detail_url"] = None
        issues.append({
            "issue": "CSV has no recognizable id/link column "
                     f"(looked for {CSV_ID_CANDIDATES + CSV_URL_CANDIDATES} "
                     f"among {headers}); core_id and detail_url left empty. "
                     "This CSV export may not carry a stable per-conference "
                     "identifier at all — cross-check against the HTML "
                     "table path before relying on this.",
        })

    return df, issues, "csv_export", headers


def parse_html_table(path):
    html = Path(path).read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    tables = soup.find_all("table")
    if not tables:
        raise ValueError("no <table> elements found in the supplied HTML — "
                          "the listing may be rendered client-side (JS), in "
                          "which case a saved 'HTML only' copy won't contain "
                          "the data. Try 'Webpage, Complete' or check "
                          "whether Export CSV works instead.")

    # Prefer whichever table actually links to conf-ranks detail pages.
    target = None
    for t in tables:
        if t.find("a", href=DETAIL_URL_RE):
            target = t
            break
    if target is None:
        target = max(tables, key=lambda t: len(t.find_all("tr")))

    rows = target.find_all("tr")
    if not rows:
        raise ValueError("matched table has no rows")

    header_cells = rows[0].find_all(["th", "td"])
    headers = [normalize_header(c.get_text()) for c in header_cells]

    records = []
    issues = []
    for tr in rows[1:]:
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        link = tr.find("a", href=DETAIL_URL_RE)
        if not link:
            issues.append({
                "row_text": tr.get_text(" ", strip=True)[:200],
                "issue": "no /conf-ranks/{core_id}/ link found in this row; skipped",
            })
            continue
        m = DETAIL_URL_RE.search(link["href"])
        row = {
            "core_id": m.group(1),
            "detail_url": absolutize(link["href"]),
        }
        for i, cell in enumerate(cells):
            key = headers[i] if i < len(headers) else f"col_{i}"
            row[key] = cell.get_text(strip=True)
        records.append(row)

    if not records:
        raise ValueError("table matched but zero rows contained a "
                          "/conf-ranks/{core_id}/ link — wrong table picked?")

    df = pd.DataFrame(records)
    return df, issues, "html_table", headers


def build_output(df, headers, for_code_arg, for_name_arg, round_arg, issues):
    mapping = {}
    for canonical, candidates in CANDIDATE_COLUMNS.items():
        mapping[canonical] = best_match(headers, candidates)

    out = pd.DataFrame()
    out["core_id"] = df["core_id"]
    out["detail_url"] = df["detail_url"]

    for canonical in ["title", "acronym", "rank", "for_code", "for_name"]:
        col = mapping[canonical]
        out[canonical] = df[col] if col else None

    if out["for_code"].isna().all() and for_code_arg is not None:
        out["for_code"] = for_code_arg
        mapping["for_code"] = "(--for-code argument)"
    if out["for_name"].isna().all() and for_name_arg is not None:
        out["for_name"] = for_name_arg
        mapping["for_name"] = "(--for-name argument)"

    if "source_round" in df.columns:
        mismatched = df.loc[df["source_round"] != round_arg, "source_round"]
        if not mismatched.empty:
            issues.append({
                "issue": f"--round {round_arg!r} was given but the file's "
                         f"own source_round column contains other value(s): "
                         f"{sorted(mismatched.unique().tolist())}. Rows are "
                         f"still labelled with --round; re-check that this "
                         f"file is single-round before trusting the output.",
            })
    out["source_round"] = round_arg

    for canonical in ["title", "acronym", "rank"]:
        if mapping[canonical] is None:
            issues.append({
                "issue": f"could not find a column for '{canonical}' among "
                         f"headers {headers}; field left empty",
            })
    if out["for_code"].isna().all():
        issues.append({
            "issue": "for_code not found in table and --for-code not given; "
                     "field left empty",
        })

    out = out[OUTPUT_COLUMNS]
    return out, mapping


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Parse a locally-saved CORE rankings search-results "
                     "page (HTML or Export CSV) into core_rankings.parquet. "
                     "Does not fetch anything itself — see module docstring.")
    parser.add_argument("input_path", type=Path,
                         help="Saved search-results .html or Export .csv file.")
    parser.add_argument("--round", required=True,
                         help="Source round label as selected on the search "
                              "page, e.g. CORE2023 or ICORE2026. Not "
                              "hardcoded elsewhere; read it off the page's "
                              "Source dropdown yourself.")
    parser.add_argument("--for-code", default=None,
                         help="FoR code used for this search, if not present "
                              "as its own column in the table.")
    parser.add_argument("--for-name", default=None,
                         help="FoR name used for this search, if not present "
                              "as its own column in the table.")
    parser.add_argument("--out-dir", type=Path, default=Path("data"),
                         help="Directory for core_rankings.parquet/.csv and "
                              "the .review.json (default: data/).")
    parser.add_argument("--append", action="store_true",
                         help="Merge into an existing core_rankings.parquet "
                              "in --out-dir instead of overwriting it "
                              "(upserts on core_id + source_round).")
    args = parser.parse_args(argv)

    if not args.input_path.exists():
        parser.error(f"file not found: {args.input_path}")
    if not args.input_path.is_file():
        parser.error(f"not a file: {args.input_path}")
    if args.input_path.suffix.lower() not in (".html", ".htm", ".csv"):
        parser.error(f"expected a .html/.htm/.csv file, got: {args.input_path}")
    if args.input_path.stat().st_size == 0:
        parser.error(f"file is empty: {args.input_path}")

    return args


def main(argv=None):
    args = parse_args(argv)

    try:
        if args.input_path.suffix.lower() == ".csv":
            df, issues, extraction_path, headers = parse_csv(args.input_path)
        else:
            df, issues, extraction_path, headers = parse_html_table(args.input_path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out, mapping = build_output(
        df, headers, args.for_code, args.for_name, args.round, issues)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = args.out_dir / "core_rankings.parquet"
    csv_path = args.out_dir / "core_rankings.csv"
    review_path = args.out_dir / "core_rankings.review.json"

    if args.append and parquet_path.exists():
        existing = pd.read_parquet(parquet_path)
        combined = pd.concat([existing, out], ignore_index=True)
        combined = combined.drop_duplicates(
            subset=["core_id", "source_round"], keep="last")
        out = combined

    out.to_parquet(parquet_path, index=False)
    out.to_csv(csv_path, index=False)

    review = {
        "source_file": str(args.input_path),
        "extraction_path": extraction_path,
        "detected_headers": headers,
        "column_mapping": mapping,
        "rows_written": len(out),
        "issues": issues,
        "sample_rows": out.head(10).to_dict(orient="records"),
    }
    review_path.write_text(json.dumps(review, indent=2, ensure_ascii=False))

    print(f"wrote {len(out)} rows to {parquet_path} (mirror: {csv_path})",
          file=sys.stderr)
    print(f"review file: {review_path}", file=sys.stderr)
    if issues:
        print(f"{len(issues)} issue(s) — see review file. First: {issues[0]}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
