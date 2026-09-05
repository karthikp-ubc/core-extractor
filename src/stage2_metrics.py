#!/usr/bin/env python3
"""Stage 2 — ICORE-computed metrics (PRIMARY metric source, SPEC.md §6.3).

Discovery finding (reported before this was built, per SPEC.md §11): the
`(h-index)` and `(citation)` links on a detail page are bare PNG chart
images — `.../centiles_graphs/{ACRONYM}_{core_id}_{h_index|cited}_FOR{code}_
centileGraph.png` — with no surrounding caption text and no JSON/HTML data
endpoint visible in the page markup. This is unlike the Stage 2b PDF case,
where the same kind of chart *is* followed by a caption sentence that can be
regexed instead. Here there's no text fallback, and this is the only metric
source for venues that never submitted a Data document.

This script does not fetch the images itself — same reasoning as
stage1_core.py/stage1b_detail.py re: robots.txt. You place the downloaded
PNGs (keep their original filenames — those filenames are already the join
key back to links.csv) into --images-dir (default: images/). Files not
found there are logged to unmatched.csv, not guessed at.

Extraction method: the "centileGraph" filename plus the fact these are the
same percentile-by-rank-tier chart used elsewhere on ICORE's own site
strongly suggests this is the same chart embedded (with a caption) in
Stage 2b's PDFs. That's inference, not a confirmed match — no real sample
of one of these standalone images has been inspected yet. Values are read
off the image with a vision-capable Claude model (method="llm" always;
there is no regex path for a chart image), constrained to report only what
is visibly printed on the chart and to return null rather than guess.
Every value keeps the source image (copied into cache/) as raw_payload_ref.
"""
import argparse
import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import load_config  # noqa: E402

METRICS_COLUMNS = ["core_id", "round", "metric_name", "value", "window",
                    "retrieved_at", "raw_payload_ref"]
UNMATCHED_COLUMNS = ["core_id", "round", "link_type", "url", "reason"]

VISION_PROMPT = """\
This image is a chart from the ICORE conference-ranking portal, showing a \
conference's citation or author-strength (h-index) percentile relative to \
rank tiers (A*, A, B, C) within a Field of Research. Read ONLY numbers that \
are visibly printed on the chart (axis labels, data labels, legend, title, \
any subtitle). Do not infer, round, or estimate a value that is not \
actually printed.

Return strict JSON with this shape, using null for anything not visibly \
printed:
{
  "venue_top25_pct": <int or null>,
  "tier_baseline_pct": {"A*": <int or null>, "A": <int or null>,
                         "B": <int or null>, "C": <int or null>},
  "venue_raw_value": <int or null, the venue's own raw h-index or citation
                       count if printed anywhere on the chart, distinct
                       from the percentile>,
  "window_years": [<int>, ...] or null,
  "confidence": "high" | "medium" | "low",
  "notes": "<one sentence on what's actually visible, or why fields are null>"
}
Respond with ONLY the JSON object, no other text.
"""


def call_vision_model(image_path: Path, model: str) -> dict:
    import anthropic
    client = anthropic.Anthropic()
    media_type = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    data = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
    resp = client.messages.create(
        model=model,
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64",
                                              "media_type": media_type,
                                              "data": data}},
                {"type": "text", "text": VISION_PROMPT},
            ],
        }],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    return json.loads(text)


def metric_prefix(link_type: str) -> str:
    return {"h_index": "author_strength", "citation": "citation"}[link_type]


def process_row(row, image_path, cache_dir, model):
    """Returns (metric_records, error_or_None)."""
    try:
        parsed = call_vision_model(image_path, model)
    except Exception as exc:  # noqa: BLE001 — surfaced into unmatched.csv, not swallowed
        return [], f"vision extraction failed: {exc}"

    cache_copy = cache_dir / image_path.name
    if not cache_copy.exists():
        cache_copy.write_bytes(image_path.read_bytes())

    retrieved_at = datetime.now(timezone.utc).isoformat()
    prefix = metric_prefix(row["link_type"])
    window = ",".join(str(y) for y in parsed.get("window_years") or []) or None

    records = []
    venue_pct = parsed.get("venue_top25_pct")
    if venue_pct is not None:
        records.append((f"{prefix}_top25_venue_pct", venue_pct))
    raw_value = parsed.get("venue_raw_value")
    if raw_value is not None:
        records.append((f"{prefix}_raw_value", raw_value))
    for tier, key in [("A*", "A_star"), ("A", "A"), ("B", "B"), ("C", "C")]:
        val = (parsed.get("tier_baseline_pct") or {}).get(tier)
        if val is not None:
            records.append((f"{prefix}_top25_tier_{key}_pct", val))

    if not records:
        return [], (f"vision model returned no readable values "
                     f"(notes: {parsed.get('notes', 'none given')})")

    return [{
        "core_id": row["core_id"], "round": row["round"],
        "metric_name": name, "value": value, "window": window,
        "retrieved_at": retrieved_at,
        "raw_payload_ref": str(cache_copy.relative_to(cache_dir.parent)),
    } for name, value in records], None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read ICORE h-index/citation centile charts (local "
                     "images only — see module docstring) into "
                     "icore_metrics.parquet.")
    parser.add_argument("--links-csv", type=Path, default=None,
                         help="Default: <data_dir>/links.csv from config.yaml.")
    parser.add_argument("--images-dir", type=Path, default=None,
                         help="Default: <images_dir> from config.yaml.")
    parser.add_argument("--out-dir", type=Path, default=None,
                         help="Default: <data_dir> from config.yaml.")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config) if args.config else load_config()
    links_csv = args.links_csv or (cfg.resolve("data_dir") / "links.csv")
    images_dir = args.images_dir or cfg.resolve("images_dir")
    out_dir = args.out_dir or cfg.resolve("data_dir")
    cache_dir = cfg.resolve("cache_dir") / "images"
    cache_dir.mkdir(parents=True, exist_ok=True)

    if not links_csv.exists():
        print(f"error: {links_csv} not found — run stage1b_detail.py first",
              file=sys.stderr)
        return 1

    links = pd.read_csv(links_csv)
    in_scope = links[
        links["link_type"].isin(["h_index", "citation"])
        & links["round"].isin(cfg.in_scope_rounds)
    ]
    print(f"{len(in_scope)} h_index/citation link(s) in scope "
          f"(rounds: {cfg.in_scope_rounds})", file=sys.stderr)

    metric_rows, unmatched_rows = [], []
    for _, row in in_scope.iterrows():
        basename = row["url"].rsplit("/", 1)[-1]
        image_path = images_dir / basename
        if not image_path.exists():
            unmatched_rows.append({
                "core_id": row["core_id"], "round": row["round"],
                "link_type": row["link_type"], "url": row["url"],
                "reason": f"image not found locally at {image_path} — "
                          f"download it yourself and place it there "
                          f"(filename must match the URL's basename)",
            })
            continue
        recs, err = process_row(row, image_path, cache_dir, cfg.anthropic.model)
        if err:
            unmatched_rows.append({
                "core_id": row["core_id"], "round": row["round"],
                "link_type": row["link_type"], "url": row["url"],
                "reason": err,
            })
        metric_rows.extend(recs)

    out_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = out_dir / "icore_metrics.parquet"
    metrics_df = pd.DataFrame(metric_rows, columns=METRICS_COLUMNS)
    metrics_df.to_parquet(metrics_path, index=False)
    print(f"wrote {len(metrics_df)} metric row(s) to {metrics_path}",
          file=sys.stderr)

    unmatched_path = out_dir / "unmatched.csv"
    unmatched_df = pd.DataFrame(unmatched_rows, columns=UNMATCHED_COLUMNS)
    if unmatched_path.exists() and not unmatched_df.empty:
        existing = pd.read_csv(unmatched_path)
        unmatched_df = pd.concat([existing, unmatched_df], ignore_index=True)
    if not unmatched_df.empty or not unmatched_path.exists():
        unmatched_df.to_csv(unmatched_path, index=False)

    n_covered = in_scope.shape[0] - len(unmatched_rows)
    print(f"coverage: {n_covered}/{len(in_scope)} in-scope links extracted "
          f"({len(unmatched_rows)} logged to {unmatched_path})",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
