#!/usr/bin/env python3
"""Stage 1b — parse locally-saved CORE conference detail pages.

Detail pages live at https://portal.core.edu.au/conf-ranks/{core_id}/. As
with Stage 1, this project does not fetch them itself (see stage1_core.py's
docstring re: robots.txt disallowing Claude-User) — the user downloads each
page and this script parses the saved HTML.

The exact DOM structure of these pages has never been inspected. What SPEC.md
does establish as fact (not "to be discovered") is the *textual* convention:
each round row carries a rank + FoR code, recent rounds carry "(h-index)" and
"(citation)" links, reviewed venues additionally carry "(Data 1)", "(Data 2)",
... and "(Decision)" links, and a "DBLP Source" URL sits near the top of the
page. This parser leans on that text, not on any assumed table/div layout, so
it should survive most reasonable markup shapes — but it has not been run
against a real page yet.

Every run writes a `.review.json` with full per-round context blocks and the
raw text around every matched (and unmatched) link, specifically so the
output can be checked against the real page before being trusted.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

ROUND_RE = re.compile(r"\b((?:ICORE|CORE)\s?20\d{2})\b", re.I)
# Not \b(A\*|...)\b: \b after the literal "*" fails when "*" is followed by
# whitespace (neither side is a "word" char), so "A*" would silently fall
# through to the bare "A" alternative. Use letter-based lookaround instead.
RANK_RE = re.compile(
    r"(?<![A-Za-z])(A\*|Australasian B|A|B|C)(?![A-Za-z*])"
    r"|(unranked(?:\s*:\s*merged)?)\b", re.I)
FOR_CODE_RE = re.compile(
    r"(?:FoR|Field of Research)\D{0,20}(\d{3,6}(?:\s*,\s*\d{3,6})*)", re.I)
DECISION_HINT_RE = re.compile(
    r"[^.\n]*\b(leave as|upgrade to|downgrade to|used as comparator|"
    r"no change)\b[^.\n]*", re.I)

LINK_PATTERNS = [
    ("h_index", re.compile(r"^h-?\s*index$", re.I)),
    ("citation", re.compile(r"^citation$", re.I)),
    ("decision", re.compile(r"^decision$", re.I)),
    ("data", re.compile(r"^data\s*(\d*)$", re.I)),
]

DETAIL_CORE_ID_RE = re.compile(r"/conf-ranks/(\d+)/?")

DETAIL_COLUMNS = ["core_id", "round", "rank", "for_code",
                  "dblp_source_url", "decision_phrase"]
LINKS_COLUMNS = ["core_id", "round", "link_type", "doc_index", "url"]


def absolutize(url):
    if url.startswith("http"):
        return url
    if url.startswith("/"):
        return "https://portal.core.edu.au" + url
    return "https://portal.core.edu.au/" + url


def classify_link(text):
    text = text.strip().strip("()").strip()
    for link_type, rx in LINK_PATTERNS:
        m = rx.match(text)
        if m:
            doc_index = None
            if link_type == "data":
                doc_index = int(m.group(1)) if m.group(1) else 1
            return link_type, doc_index
    return None, None


DBLP_URL_RE = re.compile(r"https?://\S*dblp\S*", re.I)


def find_dblp_source(soup):
    for a in soup.find_all("a", href=True):
        # dblp.org is a newer alias; dblp.uni-trier.de is its original,
        # still-common domain. Match either.
        if "dblp" in a["href"].lower():
            return absolutize(a["href"])
    # Confirmed on real pages: "DBLP Source:" is rendered as plain text in a
    # <div class="row ...">, not as a hyperlink. Fall back to a URL pattern
    # over the page text.
    m = DBLP_URL_RE.search(soup.get_text(" "))
    if m:
        return m.group(0).rstrip(".,)")
    return None


def guess_core_id(soup, filename):
    # Confirmed on a real page: the self-referential /conf-ranks/{id}/ shows
    # up in a <form action=...> (a sort/filter form), not an <a href>. Rather
    # than hardcode that one tag/attribute, scan every attribute on every
    # tag — safer against the next page using yet another element for it.
    for tag in soup.find_all(True):
        for value in tag.attrs.values():
            if not isinstance(value, str):
                continue
            m = DETAIL_CORE_ID_RE.search(value)
            if m:
                return m.group(1)
    m = re.search(r"(\d{2,6})", filename)
    return m.group(1) if m else None


def nearest_previous_round(tag):
    for s in tag.find_all_previous(string=True):
        m = ROUND_RE.search(s)
        if m:
            return m.group(1).upper().replace(" ", "")
    return None


def extract_links(soup):
    """One row per matched anchor, tagged with the nearest preceding round
    label in document order."""
    links = []
    unmatched = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        link_type, doc_index = classify_link(text)
        if link_type is None:
            if text and len(text) < 40:
                unmatched.append({"text": text, "href": a["href"]})
            continue
        round_label = nearest_previous_round(a)
        links.append({
            "round": round_label,
            "link_type": link_type,
            "doc_index": doc_index,
            "url": absolutize(a["href"]),
            "anchor_text": text,
        })
    return links, unmatched


def extract_rank_history(full_text):
    """Split the page's flat text on round-label boundaries and pull rank /
    FoR-code / decision hints out of each block.

    A round label typically appears more than once on a page (e.g. once in
    a summary table with the rank, again as a per-round section heading
    with just links) — merge all text following every occurrence of a given
    round label before searching, so data isn't lost to whichever
    occurrence happened to carry it.
    """
    matches = list(ROUND_RE.finditer(full_text))
    blocks_by_round = {}
    order = []
    for i, m in enumerate(matches):
        round_label = m.group(1).upper().replace(" ", "")
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        block = full_text[start:end]
        if round_label not in blocks_by_round:
            blocks_by_round[round_label] = []
            order.append(round_label)
        blocks_by_round[round_label].append(block)

    rows = []
    blocks = []
    for round_label in order:
        merged = "\n".join(blocks_by_round[round_label])
        blocks.append({
            "round": round_label,
            "occurrences": len(blocks_by_round[round_label]),
            "block_text": merged[:800],
        })

        rank_m = RANK_RE.search(merged)
        for_m = FOR_CODE_RE.search(merged)
        decision_m = DECISION_HINT_RE.search(merged)

        rows.append({
            "round": round_label,
            "rank": (rank_m.group(1) or rank_m.group(2)) if rank_m else None,
            "for_code": for_m.group(1) if for_m else None,
            "decision_phrase": decision_m.group(0).strip() if decision_m else None,
        })
    return rows, blocks


def parse_detail_page(path, core_id_arg):
    html = Path(path).read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    core_id = core_id_arg or guess_core_id(soup, Path(path).name)
    if core_id is None:
        raise ValueError(
            "could not determine core_id from the page or filename; pass "
            "--core-id explicitly")

    dblp_source_url = find_dblp_source(soup)
    full_text = soup.get_text("\n")

    rank_rows, blocks = extract_rank_history(full_text)
    links, unmatched_links = extract_links(soup)

    if not rank_rows:
        raise ValueError(
            f"no round labels (e.g. CORE2023, ICORE2026) found in "
            f"{path} — wrong page, or the round format differs from "
            f"what this parser expects")

    detail_records = []
    for r in rank_rows:
        detail_records.append({
            "core_id": core_id,
            "round": r["round"],
            "rank": r["rank"],
            "for_code": r["for_code"],
            "dblp_source_url": dblp_source_url,
            "decision_phrase": r["decision_phrase"],
        })

    link_records = []
    for l in links:
        link_records.append({
            "core_id": core_id,
            "round": l["round"],
            "link_type": l["link_type"],
            "doc_index": l["doc_index"],
            "url": l["url"],
        })

    issues = []
    for l in links:
        if l["round"] is None:
            issues.append({
                "issue": "link found with no preceding round label",
                "link_type": l["link_type"], "url": l["url"],
            })
    for r in rank_rows:
        if r["rank"] is None:
            issues.append({"issue": "no rank token found in round block",
                            "round": r["round"]})
        if r["for_code"] is None:
            issues.append({"issue": "no FoR code found in round block",
                            "round": r["round"]})
    if dblp_source_url is None:
        issues.append({"issue": "no dblp.org link found anywhere on the page"})

    review = {
        "source_file": str(path),
        "core_id": core_id,
        "dblp_source_url": dblp_source_url,
        "round_blocks": blocks,
        "links_found": links,
        "unmatched_short_anchors": unmatched_links[:40],
        "issues": issues,
    }

    return detail_records, link_records, review


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Parse one or more locally-saved CORE conference "
                     "detail pages into conference_detail.parquet + "
                     "links.csv. Does not fetch anything itself — see "
                     "module docstring.")
    parser.add_argument("input_paths", type=Path, nargs="+",
                         help="One or more saved detail-page .html files.")
    parser.add_argument("--core-id", default=None,
                         help="Override core_id (only valid with a single "
                              "input file; otherwise it's read off the "
                              "page/filename per file).")
    parser.add_argument("--out-dir", type=Path, default=Path("data"),
                         help="Directory for conference_detail.parquet, "
                              "links.csv, and the .review.json files "
                              "(default: data/).")
    parser.add_argument("--append", action="store_true",
                         help="Merge into existing conference_detail.parquet "
                              "/ links.csv in --out-dir instead of "
                              "overwriting them.")
    args = parser.parse_args(argv)

    if args.core_id and len(args.input_paths) > 1:
        parser.error("--core-id can only be used with a single input file")

    for p in args.input_paths:
        if not p.exists():
            parser.error(f"file not found: {p}")
        if not p.is_file():
            parser.error(f"not a file: {p}")
        if p.suffix.lower() not in (".html", ".htm"):
            parser.error(f"expected a .html/.htm file, got: {p}")
        if p.stat().st_size == 0:
            parser.error(f"file is empty: {p}")

    return args


def main(argv=None):
    args = parse_args(argv)

    all_detail, all_links = [], []
    any_issues = False

    for path in args.input_paths:
        try:
            detail_records, link_records, review = parse_detail_page(
                path, args.core_id)
        except ValueError as exc:
            print(f"error parsing {path}: {exc}", file=sys.stderr)
            continue

        all_detail.extend(detail_records)
        all_links.extend(link_records)

        args.out_dir.mkdir(parents=True, exist_ok=True)
        review_path = args.out_dir / f"detail_{review['core_id']}.review.json"
        review_path.write_text(json.dumps(review, indent=2, ensure_ascii=False))
        print(f"parsed {path} -> core_id={review['core_id']}, "
              f"{len(detail_records)} round row(s), {len(link_records)} link(s) "
              f"-- review: {review_path}", file=sys.stderr)
        if review["issues"]:
            any_issues = True
            print(f"  {len(review['issues'])} issue(s), e.g. {review['issues'][0]}",
                  file=sys.stderr)

    if not all_detail:
        print("error: no pages parsed successfully", file=sys.stderr)
        return 1

    detail_df = pd.DataFrame(all_detail)[DETAIL_COLUMNS]
    links_df = pd.DataFrame(all_links)[LINKS_COLUMNS] if all_links else \
        pd.DataFrame(columns=LINKS_COLUMNS)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    detail_parquet = args.out_dir / "conference_detail.parquet"
    detail_csv = args.out_dir / "conference_detail.csv"
    links_csv = args.out_dir / "links.csv"

    if args.append and detail_parquet.exists():
        existing = pd.read_parquet(detail_parquet)
        detail_df = pd.concat([existing, detail_df], ignore_index=True) \
            .drop_duplicates(subset=["core_id", "round"], keep="last")
    if args.append and links_csv.exists():
        existing_links = pd.read_csv(links_csv)
        links_df = pd.concat([existing_links, links_df], ignore_index=True)
        # doc_index is Python None for freshly-parsed non-"data" links but
        # becomes float NaN once round-tripped through CSV; concatenating
        # the two leaves an object-dtype column where drop_duplicates no
        # longer treats None and NaN as equal, silently defeating the dedup
        # below. Normalize to a single missing-value representation first.
        links_df["doc_index"] = pd.to_numeric(links_df["doc_index"],
                                               errors="coerce")
        links_df = links_df.drop_duplicates(
            subset=["core_id", "round", "link_type", "doc_index"],
            keep="last")

    detail_df.to_parquet(detail_parquet, index=False)
    detail_df.to_csv(detail_csv, index=False)
    links_df.to_csv(links_csv, index=False)

    print(f"\nwrote {len(detail_df)} rank-history row(s) to {detail_parquet} "
          f"(mirror: {detail_csv})", file=sys.stderr)
    print(f"wrote {len(links_df)} link row(s) to {links_csv}", file=sys.stderr)
    if any_issues:
        print("some pages had issues — check the per-core-id .review.json "
              "files before trusting this output", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
