# SPEC.md — ICORE Conference Metrics Explorer

## 1. Purpose

Build a Python tool that assembles a defensible, reproducible dataset about
computing conferences in a given Field of Research (FoR), combining:

- ICORE/CORE rankings for the two most recent rounds
- ICORE's own published per-conference h-index and citation data
- The submission and decision documents ICORE publishes for reviewed venues
- Independently computed bibliometrics as a verification layer

Primary use case: comparing a focal conference against its ranking cohort
within a FoR, and understanding what evidence and arguments the ranking
committee has actually accepted or rejected in recent rounds.

## 2. Scope

**Rounds: the two most recent only** (CORE2023 and the 2026 round). Do not
crawl CORE2021 and earlier for metrics — older figures are not comparable
across rounds due to citation inflation and index growth.

Exception: Stage 1b still records the full rank history from each detail
page, because rank trajectory is cheap to capture and useful. It is the
*metrics* that are scoped to recent rounds.

**Do not hardcode round labels.** The search page's Source dropdown
enumerates available rounds; read it at startup and select the two most
recent. The 2026 round's label is not yet confirmed and may be `ICORE2026`
or `CORE2026`.

## 3. Non-goals

- Do NOT scrape Google Scholar programmatically.
- Do NOT build a hosted service. Local Streamlit app only.
- Do NOT rely on fuzzy name matching as the primary join strategy. See §7.
- Do NOT treat extracted PDF numbers as ground truth. See §6.4.

## 4. Hard constraints

- Python 3.11+. `requests`, `pandas`, `beautifulsoup4`, `plotly`,
  `streamlit`, `pyyaml`, `pydantic`, `pymupdf`.
- Every network stage MUST cache to disk and MUST be resumable. A warm-cache
  run makes zero network calls unless `--refresh` is passed.
- Rate limit: max 1 request/second per host, exponential backoff on 429/5xx,
  descriptive User-Agent with a contact email from config.
- Respect `robots.txt`; check once per host at startup, abort with a clear
  error if a target path is disallowed.
- No silent failures. Anything unfetched or unmatched lands in
  `data/unmatched.csv` with a reason string.

## 5. Architecture

```
src/
  config.py          # config.yaml, paths, cache
  stage1_core.py     # rankings listing for a FoR, recent rounds
  stage1b_detail.py  # per-conference detail pages: rank history, DBLP key,
                     #   metric links, document links
  stage2_metrics.py  # follow ICORE (h-index)/(citation) links
  stage2b_docs.py    # download + extract submission/decision documents
  stage3_verify.py   # OpenAlex cross-check (optional)
  stage4_join.py     # merge into analysis table
  app.py             # Streamlit explorer
cache/  data/  pdfs/  extracted/
aliases.yaml  config.yaml
```

## 6. Stage details

### 6.1 Stage 1 — Rankings listing

Source: `https://portal.core.edu.au/conf-ranks/`, search by Field Of
Research, filtered by Source (round).

- **First investigate whether the "Export CSV" button exposes a stable
  URL.** If so use it; only fall back to HTML table parsing otherwise.
  Document which path was taken.
- Output `data/core_rankings.parquet`:
  `core_id, title, acronym, rank, source_round, for_code, for_name,
   detail_url`

### 6.2 Stage 1b — Detail pages

Detail URLs follow `https://portal.core.edu.au/conf-ranks/{core_id}/`.
Each page contains, per round row:

- Rank and FoR code
- For recent rounds: `(h-index)` and `(citation)` links to ICORE's own
  computed data. **These are present for venues that made no submission** —
  this is the primary metric source, not a fallback.
- For reviewed venues: a short decision phrase (e.g. "Used as comparator,
  leave as A") plus `(Data 1)`, `(Data 2)`, … and `(Decision)` links.
  Note there can be MORE THAN ONE Data document per round.
- A `DBLP Source` URL at the top of the page.

**Discovery task:** the anchor targets for `(h-index)` and `(citation)` are
not documented. Fetch one detail page, inspect the hrefs, and determine
whether they return JSON, HTML, or an image. Report the finding before
building Stage 2. If the data is only rendered as a chart image, check for
an underlying data endpoint before resorting to any image handling.

Output `data/conference_detail.parquet` (rank history, DBLP key) and
`data/links.csv` (`core_id, round, link_type, url`) where `link_type` is
one of `h_index`, `citation`, `data`, `decision`.

### 6.3 Stage 2 — ICORE metrics (PRIMARY)

Follow every `h_index` and `citation` link for the in-scope rounds.

- Parse into `data/icore_metrics.parquet`:
  `core_id, round, metric_name, value, window, retrieved_at, raw_payload_ref`
- Keep the raw payload in `cache/` and reference it. These numbers may end
  up in a formal submission; every value must be traceable to a stored
  response.
- Report coverage explicitly: how many venues in the FoR have ICORE metrics
  for each round. Coverage gaps are a finding, not an error.

### 6.4 Stage 2b — Submission and decision documents

Download every `data` and `decision` link for the in-scope rounds to
`pdfs/{acronym}/{round}_{link_type}_{n}.pdf`. Skip if already present with
nonzero size. Record in `data/documents.csv`:
`core_id, acronym, round, link_type, doc_index, doc_url, local_path,
 sha256, bytes, content_type, fetched_at`

If a link returns HTML or 404s, log to `unmatched.csv` and continue.

**Extraction.** VERIFIED against a real 2026-round document
(`RTSS-Data_Higherrank_2443.pdf`). These are generated by the ICORE
submission system through LaTeX (`Creator: LaTeX with hyperref`), with
embedded fonts, a clean text layer, a fixed section order and stable
`Label: value` lines. A working prototype extractor is in
`extract_icore.py`; it pulls 36 fields with regex alone, no LLM and no OCR.

Fixed section order in the 2026 form: INITIAL DETAILS, IMPACT (Citation
Centiles / Google Scholar Data / Other Ranking Lists), PROGRAM COMMITTEE
DATA, EXTENT OF STRONG PEOPLE INVOLVED (Author strength / Leading people
publishing / Top people involved), ADDITIONAL INFORMATION (Conference
Details / Last 3 instances / Policies / Relationship to similar conferences
/ Flagship area of this conference / Other Information), Attachments,
Proposers, Submitted By, Contributors.

Three findings that shape the design:

1. **The charts do not need to be read.** Each figure is followed by a
   caption sentence restating its key numbers in prose, e.g. "RTSS has 30%
   papers in the top 25% overall. A* papers have 66%, A papers have 43%, B
   papers have 22%, C papers have 11%." Regex the caption; never rasterize.

2. **The tier baselines come free.** Those A*/A/B/C figures in the caption
   are the FoR-cohort averages, computed by ICORE from Scopus over three
   consecutive years for the FoR code. Any one document in a FoR yields the
   whole tier distribution for that FoR. This replaces the per-tier
   distribution plot that Stage 3 was originally meant to compute.

3. **Follow the linked artifacts.** Documents link plain-text files at
   stable URLs under `portal.core.edu.au/core/media/{year}/` — WPP reports
   (`wpp_reports/*.txt`) and uploaded PC member lists
   (`pc_members/*.txt`). These are the underlying data, in text. Collect
   the URLs during extraction and fetch them in a follow-on pass.

Pass 1 — text layer via `pdftotext -layout`, then dehyphenate
(`(\w)-\n(\w)` → `\1\2`) before any regex; LaTeX line-breaks words inside
the caption sentences. If a document yields under ~200 characters of text,
flag `needs_ocr=true` and do not attempt extraction.

Pass 2 — deterministic capture against this schema (pydantic model, one
record per document). Older rounds' documents are NOT form-generated and
need the fallback path in the rules below.

```
DocumentExtraction:
  core_id: str
  round: str
  link_type: "data" | "decision"
  doc_url: str

  # requested action
  current_rank: str | None
  requested_rank: str | None
  outcome: str | None          # from the Decision document
  outcome_verbatim: str | None

  # ICORE-computed impact (Scopus-derived, NOT Google Scholar)
  for_codes: list[int]
  citation_data_source: str | None       # e.g. "Elsevier Scopus Database 2025"
  centile_years: list[int]               # the 3 years averaged
  citation_top25_venue_pct: int | None
  citation_top25_tier_baseline_pct: dict | None   # {"A*":..,"A":..,"B":..,"C":..}
  author_strength_top25_venue_pct: int | None
  author_strength_top25_tier_baseline_pct: dict | None

  # self-reported Google Scholar block
  gs_h5_index: int | None
  gs_h5_of_20th_in_category: int | None
  gs_position_in_subcategory: str | None
  gs_subcategory_url: str | None

  # program committee
  pc_size: int | None
  pc_established_count: int | None
  pc_median_hindex: float | None
  pc_top3_venues: str | None
  wpp_position_pc: int | None            # rank of this venue for its own PC

  # area leaders — the methodology that decides "flagship"
  leaders_selection_method: str | None   # verbatim; submitters often deviate
  area_leaders: list[{name, gs_hindex}]
  leaders_top3_venues: str | None
  wpp_position_leaders: int | None

  # per-instance data available nowhere else
  last_instances: list[{slot, year, location, papers_submitted,
                        papers_published, acceptance_rate_pct}]
  submissions_by_year: list[int]
  acceptance_rates_pct: list[int]
  chair_hindex_by_year: list[int]

  # linked machine-readable artifacts to fetch separately
  icore_artifact_urls: list[str]

  # free-text argument sections
  relationship_to_similar_conferences: str | None
  flagship_area_claim: str | None
  other_relevant_info: str | None

  # comparator venues cited in the submission
  comparators: list[Comparator]   # acronym, claimed_rank, claimed_h5

  # argument coding
  arguments: list[Argument]

  # provenance — REQUIRED for every populated field above
  provenance: dict[str, Provenance]

Provenance:
  page: int
  snippet: str        # verbatim text the value was read from, <=200 chars
  confidence: "high" | "medium" | "low"
  method: "regex" | "table" | "llm" | "manual"

Argument:
  claim_type: str     # e.g. "flagship_identity", "no_alternative_venue",
                      #   "citation_strength", "peer_inconsistency",
                      #   "community_size", "selectivity", "longevity"
  summary: str        # one sentence, extractor's words
  page: int
  snippet: str
```

Rules:

- **Every populated field must have a `provenance` entry.** A value with no
  page number and verbatim snippet is invalid and must be dropped, not
  guessed. Fail validation loudly if this invariant breaks.
- Deterministic capture is the default for every structured field. Do NOT
  send a 2026-round document to an LLM to read `Acceptance rate: 23%` off
  it — the regex is exact, free, and auditable.
- The Claude API is used for exactly two jobs, both operating on text the
  regex pass has already isolated:
  1. **Argument coding.** Classify `relationship_to_similar_conferences`,
     `flagship_area_claim` and `other_relevant_info` into the `claim_type`
     taxonomy below, one `Argument` record per distinct claim, each with
     the verbatim sentence it came from. The prompt must require the model
     to quote the source sentence, and any returned claim whose quoted
     sentence is not found verbatim in the input is discarded. This is the
     guard against invented arguments.
  2. **Legacy documents.** Pre-2026 rounds are not form-generated. Run the
     labelled-field regex first, then hand the remainder to the model with
     the same schema, and mark `method="llm"` with an honest `confidence`.
     Everything the model returns goes on the verification worklist.
- Send extracted text, never PDF bytes. It is cheaper and it keeps the
  model from reading numbers off chart images, which is exactly the failure
  mode the caption-regex approach avoids.
- Never infer a value from context. If the document doesn't state the
  acceptance rate, it's `None`.
- Write per-document JSON to `extracted/{core_id}_{round}_{n}.json`, and a
  flattened `data/extracted.parquet`.

**Verification workflow.** Generate `data/verify_worklist.csv` containing
every field with `confidence != "high"`, plus a stratified 10% sample of
high-confidence fields, with columns `doc_path, page, field,
extracted_value, snippet, verified_value, verified_by, notes`. The last
three are filled in by hand. Provide `--apply-verification` which reads the
completed worklist back and overrides extracted values, recording
`method="manual"`.

Also emit `data/argument_matrix.csv`: one row per (venue, round,
claim_type) with the decision outcome joined in, so argument types can be
tabulated against outcomes. This is the highest-value output of Stage 2b.

### 6.5 Stage 3 — Independent verification (optional)

OpenAlex (`https://api.openalex.org`, polite pool via `mailto=`). Compute a
5-year h-index per venue from works fetched by source ID and compare against
the ICORE figure. The purpose is to detect where ICORE's venue resolution
differs from yours (co-located workshops folded in, series splits), NOT to
replace ICORE's numbers. Output `data/metric_discrepancies.csv` sorted by
relative difference.

Implement the h-index computation yourself and unit-test it.

### 6.6 Stage 4 — Join

Join on the canonical key from `aliases.yaml`. Output
`data/analysis.parquet` plus `unmatched.csv`, and print a coverage summary.

## 7. Venue identity (`aliases.yaml`)

Detail pages expose a `DBLP Source` URL, so the DBLP key is given rather
than inferred. Use it as the canonical key wherever present. `aliases.yaml`
then only needs to handle:

- Venues with no DBLP link on their page
- OpenAlex source IDs (one venue may map to several; union before computing)
- Deliberate exclusions (co-located workshops, satellite events)

```yaml
- key: DSN
  core_id: 000
  dblp_key: "conf/dsn"
  openalex_source_ids: ["S...", "S..."]
  exclude_notes: "Excludes DSN-W workshops."
```

Provide `--suggest-aliases` writing to `aliases.suggested.yaml` for human
review; it must never write `aliases.yaml` directly. Validate on load:
duplicate keys, unknown fields, and 404ing OpenAlex IDs are hard errors.

## 8. Interactive app (`app.py`, Streamlit)

- Sidebar: FoR code, round, metric source (ICORE / OpenAlex / reported-in-PDF).
- Venue table with an include/exclude checkbox per row, all included by
  default. Selection persists in session state, saveable to `presets/*.json`.
- Plots (Plotly), all respecting the current selection:
  1. Metric vs rank tier, scatter, coloured by rank, hover shows full title.
     Excluded venues hidden, not greyed.
  2. Metric distribution per rank tier (box + strip), for positioning a
     focal venue against its cohort.
  3. Round-over-round metric change across the two in-scope rounds.
  4. Argument matrix heatmap: claim_type x outcome, cell = count, from
     `argument_matrix.csv`. Clicking a cell lists the venues and links to
     the local PDFs.
- Focal-venue selector: highlights one conference across all plots and
  reports its percentile within its rank tier.
- Exports: filtered table as CSV, figures as PNG and standalone HTML.
- Every figure carries a caption with data source, round, metric window and
  retrieval date. These may end up in a formal submission.

## 9. Testing

- Unit tests: h-index computation, alias validation, rank ordering
  (A* > A > B > Australasian B > C), provenance invariant enforcement,
  extraction schema validation.
- Network layer tested against recorded fixtures only. No test touches the
  network.
- One end-to-end test on a 5-venue fixture, including one PDF fixture with a
  known expected extraction.

## 10. Acceptance criteria

1. `python run_all.py --for-code 4606` on a cold cache produces
   `data/analysis.parquet`, populated `pdfs/` and `extracted/`, a coverage
   summary, and a non-crashing `unmatched.csv`.
2. Warm-cache rerun: zero network requests, under 5 seconds.
3. No extracted value exists without page-level provenance.
4. `streamlit run src/app.py` renders all four plots; toggling a venue
   updates all of them.
5. `pytest` passes with no network access.

## 11. Build order

One stage at a time. Do not scaffold everything up front.

Stop after Stage 1b and report two things before writing any more code:
(a) whether the CSV export endpoint exists, and (b) what the `(h-index)`
and `(citation)` links actually return. Both determine the shape of
everything downstream.
