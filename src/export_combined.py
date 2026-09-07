#!/usr/bin/env python3
"""Combine every parquet output into one flat CSV — one row per
(conference, round), every gathered field as its own column.

Sources:
  - analysis.parquet   identity, rank history, and the chart-derived
                        metrics (icore_metrics.parquet, already pivoted in
                        by stage4_join.py)
  - extracted/*.json   document-derived fields (PC composition, area
                        leaders, impact figures, decision outcome, ...).
                        Read from the raw per-document JSON files rather
                        than extracted.parquet — that parquet's columns
                        are auto-flattened by pd.json_normalize and end up
                        inconsistent (a bare `citation_top25_tier_
                        baseline_pct` float column *and* a separately
                        flattened `.A*`/`.A`/`.B`/`.C` set, depending on
                        which rows had the dict populated). The JSON files
                        have a fixed, consistent shape straight from the
                        pydantic schema.

Grain is (core_id, round) — same as analysis.parquet, matching every other
stage's join key. A venue with both a Data and a Decision document this
round gets its fields merged into that one row (they're almost entirely
disjoint: Data documents carry impact/PC/leader data, Decision documents
carry the outcome). List/dict fields (area_leaders, last_instances,
comparators, arguments, for_codes, etc.) are serialized as JSON strings so
the file stays a valid flat CSV — parse them back with json.loads if you
need the structure.

Provenance is deliberately NOT included here — this is a "what did we
learn" export, not an audit trail. Use extracted/{core_id}_{round}_
{link_type}_{n}.json directly, or verify_worklist.csv, for that.
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import load_config  # noqa: E402

# Populated by the crawl/identity layer, not document content — never
# collapsed into the JSON-string treatment below.
_IDENTITY_FIELDS = {"core_id", "round", "link_type", "doc_url"}
_DROP_FIELDS = {"provenance", "extra_fields"}


def load_extracted_docs(extracted_dir: Path) -> pd.DataFrame:
    rows = []
    for path in sorted(extracted_dir.glob("*.json")):
        doc = json.loads(path.read_text())
        row = {"core_id": str(doc.get("core_id")), "round": doc.get("round"),
               "doc_url": doc.get("doc_url")}
        for field, value in doc.items():
            if field in _IDENTITY_FIELDS or field in _DROP_FIELDS:
                continue
            if isinstance(value, (list, dict)):
                if not value:  # empty list/dict -> blank cell, not "[]"/"{}"
                    row[field] = None
                else:
                    row[field] = json.dumps(value, ensure_ascii=False)
            else:
                row[field] = value
        rows.append(row)

    if not rows:
        return pd.DataFrame(columns=["core_id", "round"])

    docs_df = pd.DataFrame(rows)

    def first_non_null(s: pd.Series):
        non_null = s.dropna()
        return non_null.iloc[0] if len(non_null) else None

    field_cols = [c for c in docs_df.columns if c not in ("core_id", "round", "doc_url")]
    merged = docs_df.groupby(["core_id", "round"], as_index=False).agg(
        {**{c: first_non_null for c in field_cols},
         "doc_url": lambda s: "; ".join(s.dropna().unique()) or None})
    return merged.rename(columns={"doc_url": "document_urls"})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--analysis-parquet", type=Path, default=None,
                         help="Default: <data_dir>/analysis.parquet")
    parser.add_argument("--extracted-dir", type=Path, default=None,
                         help="Default: <extracted_dir> from config.yaml")
    parser.add_argument("--out-csv", type=Path, default=None,
                         help="Default: <data_dir>/combined_export.csv")
    parser.add_argument("--round", dest="round_", default=None,
                         help="Restrict to one round (e.g. CORE2023 or "
                              "ICORE2026). Default: all rounds, matching "
                              "analysis.parquet's own grain.")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config) if args.config else load_config()
    analysis_path = args.analysis_parquet or (cfg.resolve("data_dir") / "analysis.parquet")
    extracted_dir = args.extracted_dir or cfg.resolve("extracted_dir")
    default_name = f"combined_export_{args.round_}.csv" if args.round_ else "combined_export.csv"
    out_csv = args.out_csv or (cfg.resolve("data_dir") / default_name)

    if not analysis_path.exists():
        print(f"error: {analysis_path} not found — run stage4_join.py first",
              file=sys.stderr)
        return 1

    analysis = pd.read_parquet(analysis_path)
    analysis["core_id"] = analysis["core_id"].astype(str)
    if args.round_:
        available = sorted(analysis["round"].unique())
        if args.round_ not in available:
            print(f"error: round {args.round_!r} not found in "
                  f"{analysis_path}. Available: {available}", file=sys.stderr)
            return 1
        analysis = analysis[analysis["round"] == args.round_]
    # This is superseded by the fuller, document-derived version below —
    # drop here rather than end up with duplicate/suffixed columns.
    analysis = analysis.drop(columns=["outcome"], errors="ignore")

    docs = load_extracted_docs(extracted_dir)
    combined = analysis.merge(docs, on=["core_id", "round"], how="left")
    n_with_doc = combined["document_urls"].notna().sum() if "document_urls" in combined else 0
    print(f"{n_with_doc} conference-round(s) with at least one extracted "
          f"document, out of {len(analysis)} in scope", file=sys.stderr)

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(out_csv, index=False)
    print(f"wrote {len(combined)} row(s) x {len(combined.columns)} column(s) "
          f"to {out_csv}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
