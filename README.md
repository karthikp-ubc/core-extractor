# ICORE Conference Metrics Explorer

Assembles a dataset about computing conferences in a given CORE/ICORE Field
of Research: rankings, ICORE's own computed citation/author-strength
metrics, and (for conferences actually under review) the submission and
decision documents ICORE publishes. See `SPEC.md` for the full design
spec — this file is the practical "how do I run this" companion.

## Why this doesn't just crawl the CORE website

`portal.core.edu.au/robots.txt` disallows AI-agent user-agents across the
entire site. Every stage that would otherwise need something from that
site is built to parse **locally-saved files you collect yourself**
instead of fetching them — this isn't a missing feature, it's a
deliberate, permanent design constraint. The only stage that makes network
calls at all is `stage3_verify.py`, against OpenAlex's public API (a
different host, with a documented polite pool).

Two helper scripts (`list_detail_pages.py`, `list_downloads.py`) can
generate the exact list of URLs to fetch and, on request, a `curl` script
for **you** to review and run yourself from your own terminal — they never
run it for you.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Requires `pdftotext` (poppler-utils) on `PATH` for PDF text extraction.

Copy/edit `config.yaml` before running anything that needs it:
- `contact_email` — used **only** by `stage3_verify.py`, as OpenAlex's
  `mailto=` polite-pool parameter. Left as a placeholder deliberately; set
  your own address before running Stage 3.
- `in_scope_rounds` — the two most recent rounds (metrics/documents are
  scoped to these; rank *history* in Stage 1b covers every round
  regardless). SPEC.md says not to hardcode round labels — set this by
  hand after checking the portal's own Source dropdown.
- `paths` — where each stage reads/writes (`inputs/`, `data/`, `images/`,
  `pdfs/`, `extracted/`, `cache/`). All gitignored.

`ANTHROPIC_API_KEY` must be set in the environment for Stage 2 (chart
reading) and Stage 2b's LLM-assisted paths (legacy-document fallback,
argument coding).

## Pipeline order

```
Stage 1   stage1_core.py      search-results listing  -> core_rankings.parquet
Stage 1b  stage1b_detail.py   per-conference detail pages -> conference_detail.parquet, links.csv
  [helper] list_detail_pages.py   what detail pages to go fetch next
  [helper] list_downloads.py      what images/PDFs to go fetch next (needs links.csv)
  [helper] verify_downloads.py    did the images/PDFs you fetched actually land intact
Stage 2   stage2_metrics.py   chart images (local)   -> icore_metrics.parquet
Stage 2b  stage2b_docs.py     Data/Decision PDFs (local) -> extracted.parquet, documents.csv, argument_matrix.csv
Stage 3   stage3_verify.py    OpenAlex cross-check (network, optional) -> metric_discrepancies.csv
Stage 4   stage4_join.py      join everything          -> analysis.parquet
          export_combined.py  flatten into one CSV      -> combined_export.csv (+ combined_export_<round>.csv)
```

Beyond the pipeline itself, `src/plot_dsn_case.py` is a one-off analysis
script (not a pipeline stage — nothing else depends on it) that reads
`combined_export*.csv` and renders comparison/trend figures for a specific
report; see **Analysis scripts** below for the pattern if you want to
build a similar one for another venue.

`run_all.py` runs Stages 1/1b/2/2b/3/4 in order against whatever local
inputs already exist, skipping (loudly) any stage whose inputs aren't
there yet:

```bash
python3 run_all.py \
  --master-csv inputs/4606-all.csv --round ICORE2026 --for-code 4606 \
  --detail-pages inputs/*.html \
  --skip-stage3
```

It cannot autonomously go from zero to a finished dataset — Stages
1/1b/2/2b all need you to have collected the relevant local files first
(see below). A rerun after adding more inputs picks up exactly where it
left off.

### Step by step

**1. Get the FoR search-results CSV** from the portal yourself (Export
CSV on the search page), save it under `inputs/`, then:
```bash
python3 src/stage1_core.py inputs/<your-export>.csv \
  --round ICORE2026 --for-code 4606 --for-name "..."
```
Writes `data/core_rankings.parquet` (+ `.csv` mirror) and
`data/core_rankings.review.json` — always check the review file; column
detection is heuristic.

**2. Get each conference's detail page.** Generate the checklist:
```bash
python3 src/list_detail_pages.py --min-rank B   # or --emit-curl
```
Save each `https://portal.core.edu.au/conf-ranks/{id}/` page as `.html`
into `inputs/` (filename doesn't matter — `core_id` is read from page
content). Then:
```bash
python3 src/stage1b_detail.py inputs/*.html --out-dir data --append
```
Writes `data/conference_detail.parquet` (full rank history, every round)
and `data/links.csv` (every `h_index`/`citation`/`data`/`decision` link
found). Check `data/detail_{core_id}.review.json` per page.

**3. Get the chart images and Data/Decision PDFs.**
```bash
python3 src/list_downloads.py --for-code 4606   # or --emit-curl
```
Save images to `images/<round>/<basename>` and PDFs to
`pdfs/<acronym>/<round>_<link_type>_<n>.pdf` — exact paths are printed.
Then verify what actually landed before trusting it:
```bash
python3 src/verify_downloads.py --for-code 4606
```
Checks presence, non-zero size, and correct magic bytes (catches a
login-redirect or 404 page saved under the right filename).

**4. Extract.**
```bash
python3 src/stage2_metrics.py     # reads images/, writes icore_metrics.parquet
python3 src/stage2b_docs.py       # reads pdfs/, writes extracted.parquet, documents.csv, argument_matrix.csv, verify_worklist.csv
```

**5. (Optional) OpenAlex cross-check** — only useful once `aliases.yaml`
has entries with `openalex_source_ids`, and `config.yaml`'s
`contact_email` is set:
```bash
python3 src/stage3_verify.py
```

**6. Join and export.**
```bash
python3 src/stage4_join.py                    # -> data/analysis.parquet, coverage summary printed
python3 src/export_combined.py                # -> data/combined_export.csv (all rounds)
python3 src/export_combined.py --round CORE2023   # -> data/combined_export_CORE2023.csv
```
`combined_export.csv` is the single flat file to actually work with — one
row per (conference, round), rank/identity + chart metrics + document
fields in one place. `--round` filters to one round and writes to a
separate, auto-named file (never overwrites the all-rounds export); an
unknown round name fails with the list of actual available rounds instead
of silently writing an empty file.

Missing values render as the literal string `-`, not an empty cell — a
deliberate choice (see Known issues below) so "no data gathered" reads
unambiguously when scanning the file by eye. **If you write your own
script against `combined_export*.csv`**, pass `na_values=["-"]` to
`pd.read_csv` — otherwise any numeric column that has even one missing
value comes back as `dtype=object` (strings) and arithmetic on it breaks
with a confusing `TypeError`, not a `NaN`. `plot_dsn_case.py` does this;
copy that pattern.

**Verification workflow.** `data/verify_worklist.csv` lists every
LLM-derived field (`confidence != "high"`) plus a 10% sample of
high-confidence regex fields. Fill in `verified_value`/`verified_by`/
`notes` by hand, then:
```bash
python3 src/stage2b_docs.py --apply-verification
```
overwrites the corresponding `extracted/*.json` fields with
`method="manual"`, `confidence="high"`.

## Analysis scripts

Not every question needs a new pipeline stage — some are a one-off script
against the already-joined `combined_export*.csv`. `src/plot_dsn_case.py`
is the example of this pattern: it builds the two comparison/trend figures
used in a report making the quantitative case for reclassifying DSN
(core_id 787) from A to A*, reading only the exported CSVs (no PDFs/images
touched directly).

```bash
python3 src/plot_dsn_case.py       # -> data/dsn_case_peer_comparison.png, data/dsn_case_trend.png
```
(`matplotlib` is in `requirements.txt` for this script's sake — it's the
only thing in the repo that uses it; the pipeline itself doesn't.)

The finished report itself — `data/dsn_astar_brief.html`, published as a
Claude Artifact — embeds both figures as base64 and cites every claim back
to a specific `extracted/*.json` field or `combined_export*.csv` column.
If you build a similar script for another venue: keep it out of `run_all.py`
(it's not part of the reproducible pipeline, it's a report), and remember
the `na_values=["-"]` gotcha above.

## Testing

```bash
.venv/bin/python3 -m pytest tests/ -q
```
No test touches the network. Fixtures: real saved HTML pages in `inputs/`
for regression tests, a synthetic PDF (`tests/fixtures/sample_data_doc.pdf`,
regenerate via `tests/fixtures/generate_fixture.py`) for the extraction
schema, and pure unit tests for h-index, rank ordering, and alias
validation.

## Known issues and caveats

**`combined_export*.csv`'s missing-value marker (`-`) breaks naive numeric
reads.** It's rendered that way deliberately (an empty cell reads as
ambiguous — did this field just have no data, or did a row get
misaligned? — a literal `-` doesn't). But `pd.read_csv` without
`na_values=["-"]` leaves any column that has one turn to `dtype=object`,
so arithmetic on it raises `TypeError`, not a clean `NaN`-aware result.
Hit this directly building `plot_dsn_case.py`; every consumer of these
files needs the same `na_values=["-"]` argument.

**Coverage is inherently partial, by ICORE's own process, not a bug.**
Most conferences aren't reviewed every round — only ones up for a rank
change or periodic reassessment get a Data/Decision submission. Of a
91-conference cohort, only ~16–21 typically have documents; the rest carry
their existing rank forward with only chart-based metrics (or nothing, if
they weren't in-scope-round-ranked at all). `combined_export.csv` still
has a row for every conference — document fields are simply blank (`-`)
for the rest.

**Chart images have (at least) two different template layouts**, only
discovered by inspecting real downloaded images: some show "Top Confs
(A\*/A)" as one combined bar, others (e.g. ASPLOS-style) show A\* and A as
two separate bars. The extraction schema in `stage2_metrics.py` has named
fields for both variants so neither gets misaligned into the other — but
if ICORE ever introduces a third layout, it'll need the same treatment
(silent value-shifting into wrong fields is the failure mode to watch for,
not a crash).

**No chart shows a literal single h-index number.** The `(h-index)` charts
show percentile-band position (`author_strength_p{5,10,25,50}_*_pct`), not
a raw h5 figure. A real `gs_h5_index` number only exists in a Data
document's self-reported Google Scholar block — so it's only available
for reviewed conferences.

**Some `(Data N)` links point to a PNG, not a PDF** — confirmed (12 real
cases, e.g. SIGCOMM's CORE2021 round, ECRTS, ICNP): in every case checked,
the url is the *exact same url* already linked as that venue's own
`h_index`/`citation` chart for that round, not a distinct document. There
is nothing new to extract — Stage 2 already has it. `stage2b_docs.py`
sniffs file headers, recognizes this specific case by URL match against
`links.csv`, and routes it to `unmatched.csv` with that explanation rather
than crashing, fabricating an extraction, or mislabeling it as a possible
download failure. (Vision-parsing these separately was considered and
rejected — it would just re-derive numbers already in
`icore_metrics.parquet`.)

**Older rounds (CORE2018 and earlier) frequently lack a FoR code** on the
detail page itself — confirmed by inspection, not a parser gap. Flagged
per-round in `data/detail_{core_id}.review.json`, not silently dropped.

**Decision-document outcome extraction is regex-based against the
confirmed 2026-round format** (`Decision` heading, verdict on the next
line). Older rounds' decision documents are free-form prose without that
heading and rely on the LLM fallback instead — expect a lower hit rate
there (in practice, roughly 16/19 outcomes captured across the current
in-scope rounds; the rest are genuinely ambiguous phrasing, not silently
guessed at).

**LLM-derived fields need your own verification pass before citing them.**
Every field extracted via the legacy-document fallback or argument coding
is marked `method="llm"` in its `extracted/*.json` provenance and is never
upgraded above `confidence="medium"`. Occasionally the model mis-maps a
value to the wrong field entirely (e.g. returning boilerplate text for
`pc_top3_venues`) — the verbatim-quote guard stops *invented* values, not
*mismapped* ones. Use `verify_worklist.csv`.

**Detail-page fetching assumes no login is required.** The search page
shows a "Sign in with LinkedIn" link; every page collected so far has come
through with full content anyway, but this hasn't been exhaustively
confirmed across all 91+ conferences. `list_detail_pages.py --emit-curl`'s
generated script says to spot-check the first download before running the
rest of the batch.

**`app.py` (the Streamlit explorer, SPEC.md §8) is not built yet.** The
data pipeline (Stages 1–4 + `export_combined.py`) is the current
deliverable; the interactive app is the natural next piece.

**Stage 3 (OpenAlex) is effectively unexercised.** It requires
`aliases.yaml` entries with `openalex_source_ids`, which none of the
current conferences have (they all resolve via their DBLP link instead,
which Stage 3 doesn't use). The code path is real, not a stub, but hasn't
processed a real venue end-to-end yet.

**Git history**: `.gitignore` now excludes `inputs/`, `images/`, `pdfs/`,
`data/`, `extracted/`, `cache/`, `.venv/`, and `__pycache__/`, and all of
those were untracked from the index — but anything committed *before*
that cleanup (notably a full `.venv/`, ~16,900 files) still bloats past
commits. Untracking only affects future commits; rewriting history to
actually shrink the repo is a separate, more invasive step, not done as
part of this cleanup.
