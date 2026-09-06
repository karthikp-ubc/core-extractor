#!/usr/bin/env python3
"""Check what list_downloads.py told you to fetch actually landed intact.

Presence alone doesn't mean a download succeeded — a failed/redirected
fetch can save a login page or a 404's HTML body under a .png/.pdf
filename with a nonzero size. This checks: missing, zero-byte, and
wrong-magic-bytes (not real PNG/PDF content) for every in-scope link, using
the exact same filtering + path logic as list_downloads.py (via
compute_rows) so "what should exist" can't drift from what that script
told you to fetch.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import load_config  # noqa: E402
from list_downloads import compute_rows  # noqa: E402

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PDF_MAGIC = b"%PDF-"

REPORT_COLUMNS = ["core_id", "acronym", "round", "link_type", "url",
                    "target_path", "status", "detail"]


def sniff(path: Path, expected_kind: str) -> tuple[str, str]:
    """Returns (status, detail). status is one of: ok, missing, empty,
    wrong_type."""
    if not path.exists():
        return "missing", "file not found"
    size = path.stat().st_size
    if size == 0:
        return "empty", "0 bytes"
    head = path.read_bytes()[:16]
    if expected_kind == "png":
        if head.startswith(PNG_MAGIC):
            return "ok", f"{size} bytes, valid PNG"
        if head.startswith(b"<") or b"<html" in head.lower():
            return "wrong_type", f"{size} bytes, looks like HTML not PNG"
        return "wrong_type", f"{size} bytes, not PNG magic bytes ({head[:8]!r})"
    if head.startswith(PDF_MAGIC):
        return "ok", f"{size} bytes, valid PDF"
    if head.startswith(b"\x89PNG"):
        return "wrong_type", (f"{size} bytes, this is a PNG saved with a "
                                ".pdf extension — the (Data N)/(Decision) "
                                "link pointed at a chart image, not a real "
                                "document (seen before on SIGCOMM's "
                                "CORE2021 round)")
    if head.startswith(b"<") or b"<html" in head.lower():
        return "wrong_type", f"{size} bytes, looks like HTML not PDF"
    return "wrong_type", f"{size} bytes, not PDF magic bytes ({head[:8]!r})"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--links-csv", type=Path, default=None)
    parser.add_argument("--rankings-parquet", type=Path, default=None)
    parser.add_argument("--for-code", default=None,
                         help="Same meaning as list_downloads.py's "
                              "--for-code — pass the same value you used "
                              "there, or this will report FoR codes you "
                              "deliberately skipped as missing.")
    parser.add_argument("--out-csv", type=Path, default=None,
                         help="Default: <data_dir>/download_verification.csv")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config) if args.config else load_config()
    links_csv = args.links_csv or (cfg.resolve("data_dir") / "links.csv")
    rankings_path = args.rankings_parquet or \
        (cfg.resolve("data_dir") / "core_rankings.parquet")
    out_csv = args.out_csv or (cfg.resolve("data_dir") / "download_verification.csv")
    images_dir = cfg.resolve("images_dir")
    pdfs_dir = cfg.resolve("pdfs_dir")

    if not links_csv.exists():
        print(f"error: {links_csv} not found — run stage1b_detail.py first",
              file=sys.stderr)
        return 1

    rows, _ = compute_rows(links_csv, rankings_path, images_dir, pdfs_dir,
                             cfg.in_scope_rounds, args.for_code)

    report = []
    for r in rows:
        expected_kind = "png" if r["link_type"] in ("h_index", "citation") else "pdf"
        status, detail = sniff(Path(r["target_path"]), expected_kind)
        report.append({**{k: r[k] for k in
                           ["core_id", "acronym", "round", "link_type", "url",
                            "target_path"]},
                       "status": status, "detail": detail})

    report_df = pd.DataFrame(report, columns=REPORT_COLUMNS)
    report_df.to_csv(out_csv, index=False)

    counts = report_df["status"].value_counts()
    print(f"checked {len(report_df)} in-scope link(s) "
          f"(rounds: {cfg.in_scope_rounds}"
          + (f", FoR {args.for_code} only" if args.for_code else "") + ")\n",
          file=sys.stderr)
    for status in ["ok", "missing", "empty", "wrong_type"]:
        print(f"  {status:10s}: {counts.get(status, 0)}", file=sys.stderr)

    problems = report_df[report_df["status"] != "ok"]
    if not problems.empty:
        print(f"\n{len(problems)} problem(s):", file=sys.stderr)
        for _, r in problems.iterrows():
            print(f"  [{r['status']:10s}] {r['acronym']:<10s} {r['round']:<10s} "
                  f"{r['link_type']:8s} {r['target_path']}\n"
                  f"             {r['detail']}", file=sys.stderr)
    print(f"\nfull report written to {out_csv}", file=sys.stderr)
    return 0 if problems.empty else 1


if __name__ == "__main__":
    sys.exit(main())
