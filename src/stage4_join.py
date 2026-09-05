#!/usr/bin/env python3
"""Stage 4 — join everything into one analysis table (SPEC.md §6.6).

Canonical key is the DBLP key exposed by each detail page (SPEC.md §7),
normalized from `dblp_source_url` (e.g.
"https://dblp.uni-trier.de/db/conf/sensys" -> "conf/sensys"). aliases.yaml
only needs to cover venues with no DBLP link, multiple OpenAlex ids, or
deliberate exclusions — everything we've collected so far has a DBLP link
already and needs no alias entry.
"""
import argparse
import re
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import load_config  # noqa: E402
from aliases import load_aliases, Alias, DEFAULT_ALIASES_PATH  # noqa: E402
from ranks import rank_key  # noqa: E402

DBLP_KEY_RE = re.compile(r"/db/([^?#]+?)/?$")

ANALYSIS_COLUMNS = ["core_id", "acronym", "title", "join_key", "join_source",
                     "round", "rank", "for_code", "for_name",
                     "dblp_source_url"]
UNMATCHED_COLUMNS = ["core_id", "acronym", "reason"]


def dblp_key_from_url(url):
    if not url:
        return None
    m = DBLP_KEY_RE.search(url)
    return m.group(1) if m else None


def resolve_join_key(core_id, dblp_source_url, aliases_by_core_id):
    key = dblp_key_from_url(dblp_source_url)
    if key:
        return key, "dblp_url"
    alias = aliases_by_core_id.get(str(core_id))
    if alias and alias.dblp_key:
        return alias.dblp_key, "aliases.yaml"
    if alias and alias.exclude_notes:
        return None, "excluded"
    return None, None


def suggest_aliases(unresolved_rows, out_path: Path):
    suggestions = []
    for r in unresolved_rows:
        suggestions.append({
            "key": r["acronym"], "core_id": str(r["core_id"]),
            "dblp_key": None, "openalex_source_ids": [],
            "exclude_notes": None,
        })
    out_path.write_text(yaml.safe_dump(suggestions, sort_keys=False))
    return len(suggestions)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Join core_rankings/conference_detail/icore_metrics "
                     "into analysis.parquet (SPEC.md §6.6).")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--aliases", type=Path, default=DEFAULT_ALIASES_PATH)
    parser.add_argument("--suggest-aliases", action="store_true",
                         help="Write aliases.suggested.yaml for unresolved "
                              "venues instead of (in addition to) the "
                              "normal join. Never writes aliases.yaml.")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config) if args.config else load_config()
    data_dir = args.data_dir or cfg.resolve("data_dir")

    rankings_path = data_dir / "core_rankings.parquet"
    detail_path = data_dir / "conference_detail.parquet"
    if not detail_path.exists():
        print(f"error: {detail_path} not found — run stage1b_detail.py first",
              file=sys.stderr)
        return 1

    detail = pd.read_parquet(detail_path)
    rankings = pd.read_parquet(rankings_path) if rankings_path.exists() else \
        pd.DataFrame(columns=["core_id", "title", "acronym", "for_code", "for_name"])
    rankings = rankings.astype({"core_id": str})
    detail = detail.astype({"core_id": str})

    try:
        aliases = load_aliases(args.aliases)
    except Exception as exc:  # AliasValidationError, raised loudly per SPEC §7
        print(f"error: {exc}", file=sys.stderr)
        return 1
    aliases_by_core_id = {a.core_id: a for a in aliases if a.core_id}

    identity = detail[["core_id", "dblp_source_url"]].drop_duplicates("core_id")
    identity = identity.merge(
        rankings[["core_id", "title", "acronym"]].drop_duplicates("core_id"),
        on="core_id", how="left")

    joins, unresolved = [], []
    for _, row in identity.iterrows():
        key, source = resolve_join_key(
            row["core_id"], row["dblp_source_url"], aliases_by_core_id)
        joins.append({"core_id": row["core_id"], "join_key": key,
                       "join_source": source})
        if key is None and source != "excluded":
            unresolved.append({
                "core_id": row["core_id"],
                "acronym": row.get("acronym") or f"core{row['core_id']}",
                "reason": "no DBLP Source URL on the detail page and no "
                          "matching aliases.yaml entry",
            })

    if args.suggest_aliases:
        out_path = Path("aliases.suggested.yaml")
        n = suggest_aliases(unresolved, out_path)
        print(f"wrote {n} suggested alias entr{'y' if n == 1 else 'ies'} to "
              f"{out_path} for human review (never written to aliases.yaml "
              f"directly)", file=sys.stderr)
        return 0

    # `detail`'s own for_code is per-round (the FoR shown against that
    # specific historical round on the detail page, which can differ round
    # to round — e.g. CORE2018 shows "0805" where ICORE2026 shows "4606"
    # for the same venue). rankings' for_code is the FoR(s) this venue is
    # currently searchable under (Stage 1's cohort search, constant per
    # venue). Both are genuinely useful; renamed rather than collided so
    # neither silently shadows the other or gets mistaken for a metric.
    join_df = pd.DataFrame(joins)
    merged = detail.merge(join_df, on="core_id", how="left")
    merged = merged.merge(
        rankings[["core_id", "title", "acronym", "for_code", "for_name"]]
        .drop_duplicates("core_id")
        .rename(columns={"for_code": "for_code_current"}),
        on="core_id", how="left")

    metric_cols, outcome_cols = [], []

    metrics_path = data_dir / "icore_metrics.parquet"
    if metrics_path.exists():
        metrics = pd.read_parquet(metrics_path).astype({"core_id": str})
        if not metrics.empty:
            wide = metrics.pivot_table(
                index=["core_id", "round"], columns="metric_name",
                values="value", aggfunc="first").reset_index()
            metric_cols = [c for c in wide.columns if c not in ("core_id", "round")]
            merged = merged.merge(wide, on=["core_id", "round"], how="left")

    extracted_path = data_dir / "extracted.parquet"
    if extracted_path.exists():
        extracted = pd.read_parquet(extracted_path).astype({"core_id": str})
        if {"core_id", "round", "outcome"} <= set(extracted.columns):
            merged = merged.merge(
                extracted[["core_id", "round", "outcome"]]
                .drop_duplicates(["core_id", "round"]),
                on=["core_id", "round"], how="left")
            outcome_cols = ["outcome"]

    base_cols = [c for c in ANALYSIS_COLUMNS if c in merged.columns]
    other_cols = [c for c in merged.columns
                  if c not in base_cols + metric_cols + outcome_cols]
    merged = merged[base_cols + other_cols + metric_cols + outcome_cols]

    out_path = data_dir / "analysis.parquet"
    merged.to_parquet(out_path, index=False)
    print(f"wrote {len(merged)} row(s) to {out_path}", file=sys.stderr)

    unmatched_path = data_dir / "unmatched.csv"
    unmatched_df = pd.DataFrame(unresolved, columns=UNMATCHED_COLUMNS)
    if unmatched_path.exists():
        existing = pd.read_csv(unmatched_path)
        combined_cols = sorted(set(existing.columns) | set(unmatched_df.columns))
        unmatched_df = pd.concat(
            [existing.reindex(columns=combined_cols),
             unmatched_df.reindex(columns=combined_cols)],
            ignore_index=True)
    unmatched_df.to_csv(unmatched_path, index=False)

    n_venues = identity["core_id"].nunique()
    n_resolved = sum(1 for j in joins if j["join_key"])
    print(f"\ncoverage summary:", file=sys.stderr)
    print(f"  venues: {n_resolved}/{n_venues} resolved to a join key",
          file=sys.stderr)
    coverage_cols = metric_cols + outcome_cols
    if "round" in merged.columns:
        for round_label, group in merged.groupby("round"):
            has_metric = group[coverage_cols].notna().any(axis=1).sum() \
                if coverage_cols else 0
            print(f"  {round_label}: {len(group)} venue-round row(s), "
                  f"{has_metric} with at least one metric", file=sys.stderr)
    if unresolved:
        print(f"  {len(unresolved)} venue(s) unresolved — see {unmatched_path} "
              f"(run --suggest-aliases to draft entries)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
