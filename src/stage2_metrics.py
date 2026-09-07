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
PNGs into --images-dir (default: images/), under a subfolder per round:
images/{round}/{basename}. The subfolder matters — the 2026- and
2023-round URLs for the same venue/link_type share an identical filename
(they differ only in their parent year folder on the source site), so
bare-basename matching would silently collide the two. See
list_downloads.py for the exact per-file source URL and target path. Files
not found there are logged to unmatched.csv, not guessed at.

Extraction method: CONFIRMED against real downloaded charts (2026-09-07) —
see the CONFIRMED comment above VISION_PROMPT below for the actual layout.
It is a grouped bar chart (4 percentile bands x up to 4 series), related to
but NOT the same granularity as the Stage 2b PDF caption sentence — the
chart's "Top Confs (A*/A)" series combines A* and A into one bar, it does
not break them out separately the way the PDF caption does. Values are
read off the image with a vision-capable Claude model (method="llm"
always; there is no regex path for a chart image), constrained to report
only what is visibly printed on the chart and to return null rather than
guess. Every value keeps the source image (copied into cache/) as
raw_payload_ref.
"""
import argparse
import base64
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import load_config  # noqa: E402

METRICS_COLUMNS = ["core_id", "round", "metric_name", "value", "window",
                    "retrieved_at", "raw_payload_ref"]
UNMATCHED_COLUMNS = ["core_id", "round", "link_type", "url", "reason"]
LABELS_COLUMNS = ["core_id", "round", "link_type", "rank_of_venue",
                    "most_recent_ranking"]

# CONFIRMED against real downloaded charts (2026-09-07), REVISED same day
# after finding a second template: a grouped bar chart, x-axis = 4
# percentile bands (5/10/25/50), y-axis = "Percentage of papers in Band".
# The venue always gets its own bar. The comparison groups vary by
# template — confirmed BOTH of these exist in the real image set:
#   (a) 3 comparison bars: "Top Confs (A*/A)" (A* and A COMBINED into one
#       series), "B Ranks", "C Ranks" — e.g. CC/AINA-style charts.
#   (b) 4 comparison bars: "A* Ranks", "A Ranks" SEPARATELY, "B Ranks", "C
#       Ranks" — e.g. ASPLOS-style charts.
# The first version of this prompt only had slots for template (a). Fed a
# template (b) image, the model still returned exactly 4 values per band
# but shifted into the wrong slots (the "A Ranks" bar's value landed in
# "b_ranks", "B Ranks" landed in "c_ranks", "C Ranks" was dropped
# entirely) — silently WRONG data, not just missing data. Every field
# below is now named for its own specific legend entry so there is no slot
# to misalign into; a chart using template (a) simply leaves the
# template-(b)-only fields null, and vice versa. Side-text labels also
# vary ("Papers in venue: N" vs "#papers published: N"; "Rank of venue: X"
# vs "Rank: X") — both are covered. Same layout family for h_index and
# citation charts (only "h index"/"cited" differs in the title).
VISION_PROMPT = """\
This is a grouped bar chart from the ICORE conference-ranking portal. The \
x-axis has 4 percentile bands: 5, 10, 25, 50. Read the actual legend on \
THIS image carefully — there are two known template variants and you must \
report whichever one this image actually uses, leaving the other variant's \
fields null:

Variant A: legend has "Top Confs (A*/A)" as ONE combined bar (plus the \
venue, "B Ranks", "C Ranks").
Variant B: legend has "A* Ranks" and "A Ranks" as TWO SEPARATE bars (plus \
the venue, "B Ranks", "C Ranks").

Do not guess which variant applies — read the legend text on this specific \
image. Read the approximate height of each bar against the y-axis \
gridlines (round to the nearest labelled value, or use a printed data \
label above the bar if present). A bar absent from a band is null, not 0.

Also read the side text (labelled "Papers in venue"/"#papers published", \
and "Rank of venue"/"Rank", and "Most recent ranking" if present) and the \
title (window years).

Return strict JSON with this exact shape:
{
  "bands": {
    "5":  {"venue": <int|null>, "topconfs_A_star_A": <int|null>, "a_star_ranks": <int|null>, "a_ranks": <int|null>, "b_ranks": <int|null>, "c_ranks": <int|null>},
    "10": {"venue": <int|null>, "topconfs_A_star_A": <int|null>, "a_star_ranks": <int|null>, "a_ranks": <int|null>, "b_ranks": <int|null>, "c_ranks": <int|null>},
    "25": {"venue": <int|null>, "topconfs_A_star_A": <int|null>, "a_star_ranks": <int|null>, "a_ranks": <int|null>, "b_ranks": <int|null>, "c_ranks": <int|null>},
    "50": {"venue": <int|null>, "topconfs_A_star_A": <int|null>, "a_star_ranks": <int|null>, "a_ranks": <int|null>, "b_ranks": <int|null>, "c_ranks": <int|null>}
  },
  "papers_in_venue": <int|null>,
  "rank_of_venue": <string|null, e.g. "B">,
  "most_recent_ranking": <string|null, e.g. "CORE2021", only if that exact label is present>,
  "window_years": [<int>, ...] or null,
  "confidence": "high" | "medium" | "low",
  "notes": "<one brief sentence: which variant (A or B), and chart quality>"
}
Respond with ONLY the JSON object — no markdown code fence, no other text.
"""

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


def call_vision_model(image_path: Path, model: str) -> dict:
    import anthropic
    client = anthropic.Anthropic()
    media_type = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    data = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
    resp = client.messages.create(
        model=model,
        max_tokens=1500,
        # Extended thinking (an account/workspace default here, not
        # something this call opted into) burned the entire max_tokens
        # budget on an empty "thinking" block before any text on ~50% of
        # real chart images, leaving nothing to parse. Disabled explicitly
        # — this is a simple bounded-visual-read task, not a reasoning one.
        thinking={"type": "disabled"},
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
    # Model wraps in ```json fences on a large fraction of real responses
    # despite the "no markdown fence" instruction — strip before parsing
    # rather than fail on every one of them.
    text = _FENCE_RE.sub("", text).strip()
    return json.loads(text)


def metric_prefix(link_type: str) -> str:
    return {"h_index": "author_strength", "citation": "citation"}[link_type]


# json_key -> metric_name suffix. topconfs_A_star_A (variant A) and
# a_star_ranks/a_ranks (variant B) are mutually exclusive per image — see
# VISION_PROMPT above — but both are always offered so whichever the model
# actually finds on this image lands in its own named field, never a slot
# shared with something else.
BAND_SERIES = [("venue", "venue"), ("topconfs_A_star_A", "topconfs"),
               ("a_star_ranks", "A_star"), ("a_ranks", "A"),
               ("b_ranks", "B"), ("c_ranks", "C")]


def process_row(row, image_path, cache_dir, model):
    """Returns (metric_records, label_records, error_or_None)."""
    try:
        parsed = call_vision_model(image_path, model)
    except Exception as exc:  # noqa: BLE001 — surfaced into unmatched.csv, not swallowed
        return [], [], f"vision extraction failed: {exc}"

    # Same collision as the source images_dir layout (see caller): namespace
    # by round so the 2026/2023 versions of an identically-named chart don't
    # overwrite each other in cache/ either.
    cache_copy = cache_dir / row["round"] / image_path.name
    cache_copy.parent.mkdir(parents=True, exist_ok=True)
    if not cache_copy.exists():
        cache_copy.write_bytes(image_path.read_bytes())

    retrieved_at = datetime.now(timezone.utc).isoformat()
    prefix = metric_prefix(row["link_type"])
    window = ",".join(str(y) for y in parsed.get("window_years") or []) or None

    def as_number(val):
        # The model's JSON typing isn't fully trustworthy (found: a stray
        # non-numeric value crashed the whole batch's to_parquet() call
        # because it silently landed in a column meant to be numeric-only).
        # Coerce defensively; drop rather than let one bad value corrupt
        # the entire output file.
        try:
            return int(val)
        except (TypeError, ValueError):
            try:
                return float(val)
            except (TypeError, ValueError):
                return None

    records = []
    bands = parsed.get("bands") or {}
    for band in ["5", "10", "25", "50"]:
        band_vals = bands.get(band) or {}
        for json_key, name_part in BAND_SERIES:
            num = as_number(band_vals.get(json_key))
            if num is not None:
                records.append((f"{prefix}_p{band}_{name_part}_pct", num))
    papers = as_number(parsed.get("papers_in_venue"))
    if papers is not None:
        records.append((f"{prefix}_papers_in_venue", papers))

    # rank_of_venue / most_recent_ranking are text labels, not numeric
    # metrics — they don't belong in a `value` column meant to be numeric
    # (SPEC's icore_metrics schema), so they're captured separately rather
    # than dropped outright; useful as a cross-check against Stage 1b's
    # own rank history for the same venue/round.
    label_record = None
    rank_label = parsed.get("rank_of_venue")
    recent_ranking = parsed.get("most_recent_ranking")
    if rank_label is not None or recent_ranking is not None:
        label_record = {
            "core_id": row["core_id"], "round": row["round"],
            "link_type": row["link_type"],
            "rank_of_venue": rank_label, "most_recent_ranking": recent_ranking,
        }

    if not records and label_record is None:
        return [], [], (f"vision model returned no readable values "
                         f"(notes: {parsed.get('notes', 'none given')})")

    metric_records = [{
        "core_id": row["core_id"], "round": row["round"],
        "metric_name": name, "value": value, "window": window,
        "retrieved_at": retrieved_at,
        "raw_payload_ref": str(cache_copy.relative_to(cache_dir.parent)),
    } for name, value in records]
    return metric_records, ([label_record] if label_record else []), None


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

    metric_rows, label_rows, unmatched_rows = [], [], []
    for _, row in in_scope.iterrows():
        # The 2026- and 2023-round chart URLs for the same venue/link_type
        # differ only in their parent year folder (.../2025/... vs
        # .../2023/...) — the filename itself is identical between rounds.
        # Matching on bare basename would silently collide the two, so the
        # local path is namespaced by round.
        basename = row["url"].rsplit("/", 1)[-1]
        image_path = images_dir / row["round"] / basename
        if not image_path.exists():
            unmatched_rows.append({
                "core_id": row["core_id"], "round": row["round"],
                "link_type": row["link_type"], "url": row["url"],
                "reason": f"image not found locally at {image_path} — "
                          f"download it yourself and place it there "
                          f"(path is images/<round>/<basename>; see "
                          f"list_downloads.py for the exact list)",
            })
            continue
        recs, labels, err = process_row(row, image_path, cache_dir, cfg.anthropic.model)
        if err:
            unmatched_rows.append({
                "core_id": row["core_id"], "round": row["round"],
                "link_type": row["link_type"], "url": row["url"],
                "reason": err,
            })
        metric_rows.extend(recs)
        label_rows.extend(labels)

    out_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = out_dir / "icore_metrics.parquet"
    metrics_df = pd.DataFrame(metric_rows, columns=METRICS_COLUMNS)
    metrics_df["value"] = pd.to_numeric(metrics_df["value"])
    metrics_df.to_parquet(metrics_path, index=False)
    print(f"wrote {len(metrics_df)} metric row(s) to {metrics_path}",
          file=sys.stderr)

    labels_path = out_dir / "icore_metrics_labels.csv"
    pd.DataFrame(label_rows, columns=LABELS_COLUMNS).to_csv(labels_path, index=False)
    print(f"wrote {len(label_rows)} label row(s) to {labels_path}",
          file=sys.stderr)

    unmatched_path = out_dir / "unmatched.csv"
    unmatched_df = pd.DataFrame(unmatched_rows, columns=UNMATCHED_COLUMNS)
    if unmatched_path.exists():
        existing = pd.read_csv(unmatched_path)
        # This stage only ever contributes h_index/citation rows — drop ALL
        # of its own prior entries before re-adding this run's, so a rerun
        # reflects current reality instead of accumulating stale failures
        # from earlier (possibly since-fixed) runs on top of fresh ones.
        # Other stages' rows (different link_type) are left untouched.
        existing = existing[~existing["link_type"].isin(["h_index", "citation"])]
        unmatched_df = pd.concat([existing, unmatched_df], ignore_index=True)
    unmatched_df.to_csv(unmatched_path, index=False)

    n_covered = in_scope.shape[0] - len(unmatched_rows)
    print(f"coverage: {n_covered}/{len(in_scope)} in-scope links extracted "
          f"({len(unmatched_rows)} logged to {unmatched_path})",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
