#!/usr/bin/env python3
"""What to download from the CORE portal, and exactly where to put it.

Reads links.csv (already produced by stage1b_detail.py) and, for every
in-scope-round h_index/citation/data/decision link not yet present
locally, prints the source URL and the exact local path Stage 2/2b expect
it at. Filenames matter — stage2_metrics.py matches images by URL
basename, and stage2b_docs.py looks for PDFs at an exact renamed path — so
this exists to remove the guesswork rather than have you improvise it by
hand for 200+ conferences.
"""
import argparse
import shlex
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import load_config  # noqa: E402

PENDING_COLUMNS = ["core_id", "acronym", "round", "link_type", "url",
                    "target_path", "already_present"]


def target_path_for(row, acronym, images_dir, pdfs_dir):
    """Single source of truth for where a link's local file belongs —
    shared with verify_downloads.py so the two can't silently drift apart."""
    if row["link_type"] in ("h_index", "citation"):
        # namespaced by round — see stage2_metrics.py's docstring for why
        return images_dir / row["round"] / row["url"].rsplit("/", 1)[-1]
    doc_index = int(row["doc_index"]) if pd.notna(row["doc_index"]) else 1
    return pdfs_dir / acronym / f"{row['round']}_{row['link_type']}_{doc_index}.pdf"


def compute_rows(links_csv, rankings_path, images_dir, pdfs_dir,
                  in_scope_rounds, for_code=None):
    """Filtering + path resolution in one place — shared with
    verify_downloads.py so "what should exist" can never drift from "what
    list_downloads.py told you to fetch". Returns (rows, n_dropped_for_code)."""
    links = pd.read_csv(links_csv)
    in_scope = links[links["round"].isin(in_scope_rounds)].copy()

    dropped_for_code = 0
    if for_code:
        is_image = in_scope["link_type"].isin(["h_index", "citation"])
        matches = in_scope["url"].str.contains(f"FOR{for_code}", case=False)
        dropped_for_code = int((is_image & ~matches).sum())
        in_scope = in_scope[~is_image | matches]

    acronym_by_core_id = {}
    if rankings_path.exists():
        rankings = pd.read_parquet(rankings_path)
        acronym_by_core_id = dict(zip(rankings["core_id"].astype(str),
                                        rankings["acronym"]))

    rows = []
    for _, row in in_scope.iterrows():
        core_id = str(row["core_id"])
        acronym = acronym_by_core_id.get(core_id, f"core{core_id}")
        target = target_path_for(row, acronym, images_dir, pdfs_dir)
        rows.append({
            "core_id": core_id, "acronym": acronym, "round": row["round"],
            "link_type": row["link_type"], "url": row["url"],
            "target_path": str(target), "already_present": target.exists(),
        })
    return rows, dropped_for_code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--links-csv", type=Path, default=None)
    parser.add_argument("--rankings-parquet", type=Path, default=None)
    parser.add_argument("--out-csv", type=Path, default=None,
                         help="Default: <data_dir>/pending_downloads.csv")
    parser.add_argument("--for-code", default=None,
                         help="Only include h_index/citation chart images "
                              "labelled for this FoR code in their URL "
                              "(e.g. 4606). A conference cross-listed under "
                              "multiple FoR codes gets one chart per code; "
                              "this drops the ones not relevant to your "
                              "analysis. Does not affect data/decision "
                              "PDFs, which aren't FoR-specific. Omit to "
                              "include every FoR code's chart.")
    parser.add_argument("--emit-curl", action="store_true",
                         help="Also write commands.sh listing one `mkdir "
                              "-p` + `curl` pair per not-yet-downloaded "
                              "file, for you to review and run yourself. "
                              "This script never runs them — see its "
                              "module docstring for why.")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config) if args.config else load_config()
    links_csv = args.links_csv or (cfg.resolve("data_dir") / "links.csv")
    rankings_path = args.rankings_parquet or \
        (cfg.resolve("data_dir") / "core_rankings.parquet")
    out_csv = args.out_csv or (cfg.resolve("data_dir") / "pending_downloads.csv")
    images_dir = cfg.resolve("images_dir")
    pdfs_dir = cfg.resolve("pdfs_dir")

    if not links_csv.exists():
        print(f"error: {links_csv} not found — run stage1b_detail.py on at "
              f"least one detail page first", file=sys.stderr)
        return 1

    rows, dropped_for_code = compute_rows(
        links_csv, rankings_path, images_dir, pdfs_dir,
        cfg.in_scope_rounds, args.for_code)
    if args.for_code:
        print(f"--for-code {args.for_code}: dropped {dropped_for_code} "
              f"chart(s) labelled for a different FoR code", file=sys.stderr)

    out_df = pd.DataFrame(rows, columns=PENDING_COLUMNS)
    out_df.to_csv(out_csv, index=False)

    pending = out_df[~out_df["already_present"]]
    print(f"{len(pending)}/{len(out_df)} in-scope link(s) not yet downloaded "
          f"(rounds: {cfg.in_scope_rounds})\n", file=sys.stderr)
    for _, r in pending.iterrows():
        print(f"[{r['link_type']:8s}] {r['url']}\n"
              f"           -> save as: {r['target_path']}", file=sys.stderr)
    print(f"\nfull list (including already-downloaded) written to {out_csv}",
          file=sys.stderr)

    if args.emit_curl:
        commands_path = out_csv.parent / "download_commands.sh"
        lines = [
            "#!/bin/sh",
            "# Generated by list_downloads.py — NOT run automatically by",
            "# anything in this project. Review before running any of it.",
            "# portal.core.edu.au's robots.txt disallows AI-agent",
            "# user-agents site-wide; these commands exist for YOU to run",
            "# from your own terminal, not to be invoked by a script or",
            "# agent on your behalf. A 1-second gap is left between",
            "# requests to match this project's own rate-limit policy",
            "# (SPEC.md §4) even for a manual run.",
            "set -e",
        ]
        seen_dirs = set()
        for _, r in pending.iterrows():
            target = Path(r["target_path"])
            if target.parent not in seen_dirs:
                lines.append(f"mkdir -p {shlex.quote(str(target.parent))}")
                seen_dirs.add(target.parent)
            lines.append(
                f"curl -sS --fail -o {shlex.quote(str(target))} "
                f"{shlex.quote(r['url'])} && sleep 1")
        commands_path.write_text("\n".join(lines) + "\n")
        print(f"\n{len(pending)} curl command(s) written to {commands_path} "
              f"— open it, review it, and run it yourself (e.g. "
              f"`sh {commands_path}`) if you're satisfied with it. Nothing "
              f"in this project executes it for you.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
