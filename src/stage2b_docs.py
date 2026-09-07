#!/usr/bin/env python3
"""Stage 2b — submission/decision documents, batch driver (SPEC.md §6.4).

Does not download anything itself (portal.core.edu.au robots.txt — see
stage1_core.py). You place PDFs yourself at
`pdfs/{acronym}/{round}_{link_type}_{n}.pdf` for every `data`/`decision`
row in links.csv that's in an in-scope round (config.yaml's
in_scope_rounds). Missing/unreadable files go to unmatched.csv.

Wraps extract_icore.py's regex extractor (verified against a real 2026-round
document) for the deterministic pass. Two things extract_icore.py does NOT
do, added here because they need an LLM call and shouldn't live in a
regex-only prototype:

1. Legacy-document fallback (SPEC.md §6.4 rule 2): only the 2026-round form
   layout is confirmed. For any other in-scope round, whatever the regex
   pass leaves as None is handed to Claude with the same field list, and
   ANY returned field whose quoted snippet is not found verbatim in the
   document's text is discarded — the guard against invented values.
2. Argument coding (SPEC.md §6.4 rule 1): classifies the three free-text
   fields into the claim_type taxonomy, same verbatim-quote guard.

Both LLM calls disable extended thinking explicitly (2026-09-07 finding,
same as stage2_metrics.py: an account/workspace default here, not
something these calls opted into — on longer prompts it consumed the
entire max_tokens budget, truncating the JSON mid-response and failing
silently, since json.JSONDecodeError was being swallowed with no field
filled and no warning printed). Every LLM-derived field is marked
method="llm" with the model's own confidence, never upgraded to "high".
"""
import argparse
import csv as csv_mod
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from pydantic import TypeAdapter, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import load_config  # noqa: E402
import extract_icore as ei  # noqa: E402

DOCUMENTS_COLUMNS = ["core_id", "acronym", "round", "link_type", "doc_index",
                      "doc_url", "local_path", "sha256", "bytes",
                      "content_type", "fetched_at", "needs_ocr"]

# Model wraps JSON in ```json fences on a meaningful fraction of real
# responses despite being told not to — same fix as stage2_metrics.py.
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)
UNMATCHED_COLUMNS = ["core_id", "round", "link_type", "url", "reason"]
WORKLIST_COLUMNS = ["doc_path", "page", "field", "extracted_value", "snippet",
                     "verified_value", "verified_by", "notes"]
ARGUMENT_MATRIX_COLUMNS = ["core_id", "acronym", "round", "claim_type", "outcome"]

CLAIM_TYPES = [
    "flagship_identity", "no_alternative_venue", "citation_strength",
    "peer_inconsistency", "community_size", "selectivity", "longevity",
]

FREE_TEXT_FIELDS = ["relationship_to_similar_conferences",
                     "flagship_area_claim", "other_relevant_info"]

# Fields eligible for the legacy LLM fallback — everything in the schema
# except identity fields, provenance, and the LLM-only argument-coding
# fields (those go through code_arguments(), not this generic fill).
_SCHEMA_FIELD_NAMES = [
    name for name in ei.DocumentExtraction.model_fields
    if name not in ei._IDENTITY_FIELDS
    and name not in ei._NOT_YET_POPULATED
    and name != "provenance"
]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def sniff_content_type(path: Path) -> str:
    head = path.read_bytes()[:16]
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"<") or b"<html" in head.lower():
        return "text/html"
    return "application/octet-stream"


def paged_text(pages: list[str]) -> str:
    return "\n".join(f"--- PAGE {i} ---\n{p}" for i, p in enumerate(pages, 1))


def _matches_schema_type(field: str, value) -> bool:
    """True if `value` validates against DocumentExtraction's declared
    type for `field` (e.g. rejects a dict where a list[int] is expected)."""
    annotation = ei.DocumentExtraction.model_fields[field].annotation
    try:
        TypeAdapter(annotation).validate_python(value)
        return True
    except Exception:  # noqa: BLE001 — any validation failure means reject
        return False


def llm_fill_missing(pages, missing_fields, model):
    """Legacy-document fallback. Returns {field: {value, page, snippet,
    confidence}} for fields the model could support with a verbatim quote;
    anything else is silently omitted (never invented)."""
    if not missing_fields:
        return {}
    import anthropic
    client = anthropic.Anthropic()

    field_list = "\n".join(f"- {f}" for f in missing_fields)
    prompt = f"""\
You are extracting structured fields from an ICORE conference-ranking \
submission/decision document. The document's text follows, split into \
pages. Only extract a field if the document states it explicitly — never \
infer or estimate.

Fields to look for (use exactly these names):
{field_list}

For each field you can support, return an entry with the EXACT verbatim \
sentence or line the value came from (copy-paste exact, do not paraphrase) \
so it can be checked against the source. If a field isn't stated, omit it \
entirely — do not include it with a null or guessed value.

submissions_by_year, acceptance_rates_pct, and chair_hindex_by_year are \
each a plain ordered list of numbers, most recent year first — e.g. \
submissions_by_year: [176, 166, 175], NOT an object keyed by year like \
{{"2022": 176, "2021": 166}}. area_leaders is a list of {{"name": ..., \
"gs_hindex": <int>}} objects, not a list of plain name strings.

Return a JSON array, each entry:
{{"field": "<name>", "value": <string, number, or object matching the \
field's expected type>, "page": <int>, "snippet": "<verbatim quote, \
<=200 chars>", "confidence": "medium" | "low"}}

Respond with ONLY the JSON array, no other text.

DOCUMENT TEXT:
{paged_text(pages)}
"""
    resp = client.messages.create(
        model=model, max_tokens=4000, thinking={"type": "disabled"},
        messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    text = _FENCE_RE.sub("", text).strip()
    try:
        entries = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"warning: legacy-fallback JSON parse failed for a document "
              f"({exc}); no fields filled from this call", file=sys.stderr)
        return {}

    accepted = {}
    for entry in entries:
        field = entry.get("field")
        page = entry.get("page")
        snippet = (entry.get("snippet") or "").strip()
        if field not in missing_fields or not snippet or not isinstance(page, int):
            continue
        if not (1 <= page <= len(pages)):
            continue
        if snippet not in pages[page - 1]:
            continue  # guard: quote must be verbatim in the claimed page
        value = entry.get("value")
        if not _matches_schema_type(field, value):
            # Found in practice: the model sometimes returns e.g. a dict
            # for a list[int] field, or a sentence for an int field. One
            # such field would otherwise fail DocumentExtraction's pydantic
            # validation and discard the ENTIRE document — including every
            # other correctly-typed field this same call filled. Drop just
            # this field instead.
            print(f"warning: legacy-fallback returned {field!r}={value!r}, "
                  f"which doesn't match its schema type — dropped, not "
                  f"guessed at", file=sys.stderr)
            continue
        accepted[field] = {
            "value": value,
            "page": page, "snippet": snippet[:200],
            "confidence": entry.get("confidence") if entry.get("confidence") in
            ("medium", "low") else "low",
        }
    return accepted


def code_arguments(field_name, text, page, model):
    """Argument coding for one free-text field. Returns a list of Argument
    dicts whose snippet is a verbatim substring of `text`."""
    if not text:
        return []
    import anthropic
    client = anthropic.Anthropic()
    taxonomy = ", ".join(CLAIM_TYPES)
    prompt = f"""\
Classify the following text (the "{field_name}" section of an ICORE \
conference ranking submission) into zero or more distinct claims, each \
using one of these claim types: {taxonomy}.

For each distinct claim, quote the EXACT verbatim sentence or clause it \
comes from — copy-paste exact, do not paraphrase the quote itself (the \
summary can paraphrase, the quote cannot).

Return a JSON array, each entry:
{{"claim_type": "<one of the types above>", "summary": "<one sentence, \
your words>", "snippet": "<verbatim quote from the text below>"}}
If there are no clear claims, return an empty array.
Respond with ONLY the JSON array, no other text.

TEXT:
{text}
"""
    resp = client.messages.create(
        model=model, max_tokens=2000, thinking={"type": "disabled"},
        messages=[{"role": "user", "content": prompt}])
    raw = "".join(b.text for b in resp.content if b.type == "text").strip()
    raw = _FENCE_RE.sub("", raw).strip()
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"warning: argument-coding JSON parse failed for "
              f"{field_name!r} ({exc}); no arguments coded from this call",
              file=sys.stderr)
        return []

    accepted = []
    for entry in entries:
        snippet = (entry.get("snippet") or "").strip()
        claim_type = entry.get("claim_type")
        if claim_type not in CLAIM_TYPES or not snippet or snippet not in text:
            continue
        accepted.append({
            "claim_type": claim_type,
            "summary": entry.get("summary", "").strip(),
            "page": page, "snippet": snippet[:200],
        })
    return accepted


def is_form_generated_round(round_label: str) -> bool:
    """Only the 2026-round layout is confirmed form-generated (see
    extract_icore.py docstring). Anything else takes the legacy path."""
    return "2026" in round_label


def process_document(pdf_path, core_id, acronym, round_label, link_type,
                      doc_url, doc_index, model, use_llm):
    result = ei.extract(pdf_path, core_id=core_id, round_=round_label,
                         link_type=link_type, doc_url=doc_url)
    if result["needs_ocr"]:
        return result, None, []

    fields = dict(result["fields"])
    provenance = dict(result["provenance"])

    if use_llm and not is_form_generated_round(round_label):
        missing = [f for f in _SCHEMA_FIELD_NAMES if fields.get(f) is None]
        pages = ei.load_pages(pdf_path)
        filled = llm_fill_missing(pages, missing, model)
        for field, info in filled.items():
            fields[field] = info["value"]
            provenance[field] = {
                "page": info["page"], "snippet": info["snippet"],
                "confidence": info["confidence"], "method": "llm",
            }

    arguments = []
    if use_llm:
        for field_name in FREE_TEXT_FIELDS:
            text = fields.get(field_name)
            if not text:
                continue
            page = (provenance.get(field_name) or {}).get("page", 1)
            arguments.extend(code_arguments(field_name, text, page, model))
    fields["arguments"] = arguments

    try:
        doc = ei.DocumentExtraction(**fields, provenance=provenance)
    except ValidationError as exc:
        return result, f"schema validation failed: {exc}", []

    return result, doc, arguments


def build_worklist(all_docs_payloads, doc_paths, rng_seed=0):
    import random
    rng = random.Random(rng_seed)
    rows, high_conf_pool = [], []
    for payload, doc_path in zip(all_docs_payloads, doc_paths):
        prov = payload.get("provenance", {})
        for field, p in prov.items():
            entry = {
                "doc_path": doc_path, "page": p["page"], "field": field,
                "extracted_value": json.dumps(payload.get(field)),
                "snippet": p["snippet"], "verified_value": "",
                "verified_by": "", "notes": "",
            }
            if p["confidence"] != "high":
                rows.append(entry)
            else:
                high_conf_pool.append(entry)
    sample_n = max(1, len(high_conf_pool) // 10) if high_conf_pool else 0
    rows.extend(rng.sample(high_conf_pool, min(sample_n, len(high_conf_pool))))
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Batch-extract ICORE Data/Decision PDFs (local files "
                     "only — see module docstring).")
    parser.add_argument("--links-csv", type=Path, default=None)
    parser.add_argument("--rankings-parquet", type=Path, default=None,
                         help="For acronym lookup by core_id. Default: "
                              "<data_dir>/core_rankings.parquet.")
    parser.add_argument("--pdfs-dir", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--extracted-dir", type=Path, default=None)
    parser.add_argument("--no-llm", action="store_true",
                         help="Skip legacy-document fallback and argument "
                              "coding; regex pass only.")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config) if args.config else load_config()
    links_csv = args.links_csv or (cfg.resolve("data_dir") / "links.csv")
    rankings_path = args.rankings_parquet or \
        (cfg.resolve("data_dir") / "core_rankings.parquet")
    pdfs_dir = args.pdfs_dir or cfg.resolve("pdfs_dir")
    out_dir = args.out_dir or cfg.resolve("data_dir")
    extracted_dir = args.extracted_dir or cfg.resolve("extracted_dir")
    extracted_dir.mkdir(parents=True, exist_ok=True)

    if not links_csv.exists():
        print(f"error: {links_csv} not found — run stage1b_detail.py first",
              file=sys.stderr)
        return 1

    links = pd.read_csv(links_csv)
    acronym_by_core_id = {}
    if rankings_path.exists():
        rankings = pd.read_parquet(rankings_path)
        acronym_by_core_id = dict(zip(rankings["core_id"].astype(str),
                                        rankings["acronym"]))

    in_scope = links[
        links["link_type"].isin(["data", "decision"])
        & links["round"].isin(cfg.in_scope_rounds)
    ]
    print(f"{len(in_scope)} data/decision link(s) in scope "
          f"(rounds: {cfg.in_scope_rounds})", file=sys.stderr)

    doc_rows, unmatched_rows = [], []
    payloads, doc_paths, argument_matrix_rows = [], [], []
    decisions_by_core_round = {}  # for joining outcome into argument_matrix

    for _, row in in_scope.iterrows():
        core_id = str(row["core_id"])
        acronym = acronym_by_core_id.get(core_id, f"core{core_id}")
        round_label = row["round"]
        link_type = row["link_type"]
        doc_index = int(row["doc_index"]) if pd.notna(row["doc_index"]) else 1

        local_path = pdfs_dir / acronym / f"{round_label}_{link_type}_{doc_index}.pdf"
        if not local_path.exists():
            unmatched_rows.append({
                "core_id": core_id, "round": round_label,
                "link_type": link_type, "url": row["url"],
                "reason": f"PDF not found locally at {local_path} — "
                          f"download it yourself and place it there",
            })
            continue

        content_type = sniff_content_type(local_path)
        if content_type != "application/pdf":
            # Confirmed (2026-09): every real case of this is the site
            # linking the SAME url already captured as this venue's own
            # h_index/citation chart into the Data/Decision slot too — not
            # a broken download. No new information behind it; Stage 2
            # already extracted this exact image. Only word it as a
            # possible download failure when the url genuinely isn't one
            # we already have elsewhere.
            duplicate_of = links[
                (links["url"] == row["url"])
                & (links["link_type"].isin(["h_index", "citation"]))
            ]
            if not duplicate_of.empty:
                reason = (f"this {link_type} link points to the exact same "
                          f"url as this venue's own "
                          f"{duplicate_of.iloc[0]['link_type']} chart for "
                          f"{round_label} — already extracted via Stage 2 "
                          f"(icore_metrics.parquet), nothing new to parse "
                          f"here")
            else:
                reason = (f"{local_path} is not a PDF (sniffed "
                          f"{content_type}) — link may have 404'd or "
                          f"returned an HTML error page when saved")
            unmatched_rows.append({
                "core_id": core_id, "round": round_label,
                "link_type": link_type, "url": row["url"], "reason": reason,
            })
            continue

        doc_rows.append({
            "core_id": core_id, "acronym": acronym, "round": round_label,
            "link_type": link_type, "doc_index": doc_index,
            "doc_url": row["url"], "local_path": str(local_path),
            "sha256": sha256_of(local_path), "bytes": local_path.stat().st_size,
            "content_type": content_type,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "needs_ocr": False,
        })

        result, doc_or_err, arguments = process_document(
            local_path, core_id, acronym, round_label, link_type, row["url"],
            doc_index, cfg.anthropic.model, use_llm=not args.no_llm)

        if result["needs_ocr"]:
            doc_rows[-1]["needs_ocr"] = True
            unmatched_rows.append({
                "core_id": core_id, "round": round_label,
                "link_type": link_type, "url": row["url"],
                "reason": "needs_ocr: under ~200 chars of extractable text; "
                          "extraction skipped per SPEC.md §6.4",
            })
            continue
        if isinstance(doc_or_err, str):
            unmatched_rows.append({
                "core_id": core_id, "round": round_label,
                "link_type": link_type, "url": row["url"],
                "reason": doc_or_err,
            })
            continue

        doc = doc_or_err
        payload = doc.model_dump(exclude_none=False)
        if result["extra_fields"]:
            payload["extra_fields"] = result["extra_fields"]

        # link_type must be in the filename: a data doc and a decision doc
        # for the same core_id/round both default to doc_index=1 and would
        # otherwise silently overwrite each other (found: half of every
        # real batch run's per-document JSONs were lost this way).
        out_path = extracted_dir / f"{core_id}_{round_label}_{link_type}_{doc_index}.json"
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        payloads.append(payload)
        doc_paths.append(str(out_path))

        if link_type == "decision":
            decisions_by_core_round[(core_id, round_label)] = \
                payload.get("outcome") or payload.get("outcome_verbatim")
        for arg in arguments:
            argument_matrix_rows.append({
                "core_id": core_id, "acronym": acronym, "round": round_label,
                "claim_type": arg["claim_type"], "outcome": None,  # filled below
            })

    for r in argument_matrix_rows:
        r["outcome"] = decisions_by_core_round.get((r["core_id"], r["round"]))

    out_dir.mkdir(parents=True, exist_ok=True)

    docs_df = pd.DataFrame(doc_rows, columns=DOCUMENTS_COLUMNS)
    docs_df.to_csv(out_dir / "documents.csv", index=False)
    print(f"wrote {len(docs_df)} document row(s) to {out_dir / 'documents.csv'}",
          file=sys.stderr)

    if payloads:
        extracted_df = pd.json_normalize(payloads)
        extracted_df.to_parquet(out_dir / "extracted.parquet", index=False)
        print(f"wrote {len(extracted_df)} extraction row(s) to "
              f"{out_dir / 'extracted.parquet'}", file=sys.stderr)

    unmatched_path = out_dir / "unmatched.csv"
    unmatched_df = pd.DataFrame(unmatched_rows, columns=UNMATCHED_COLUMNS)
    if unmatched_path.exists():
        existing = pd.read_csv(unmatched_path)
        # This stage only ever contributes data/decision rows — drop ALL of
        # its own prior entries first (see stage2_metrics.py's identical
        # fix) so a rerun reflects current reality, not stale history.
        existing = existing[~existing["link_type"].isin(["data", "decision"])]
        unmatched_df = pd.concat([existing, unmatched_df], ignore_index=True)
    unmatched_df.to_csv(unmatched_path, index=False)

    worklist_rows = build_worklist(payloads, doc_paths)
    pd.DataFrame(worklist_rows, columns=WORKLIST_COLUMNS).to_csv(
        out_dir / "verify_worklist.csv", index=False)
    print(f"wrote {len(worklist_rows)} row(s) to "
          f"{out_dir / 'verify_worklist.csv'}", file=sys.stderr)

    pd.DataFrame(argument_matrix_rows, columns=ARGUMENT_MATRIX_COLUMNS).to_csv(
        out_dir / "argument_matrix.csv", index=False)
    print(f"wrote {len(argument_matrix_rows)} row(s) to "
          f"{out_dir / 'argument_matrix.csv'}", file=sys.stderr)

    print(f"coverage: {len(doc_rows)}/{len(in_scope)} in-scope documents "
          f"processed ({len(unmatched_rows)} logged to {unmatched_path})",
          file=sys.stderr)
    return 0


def apply_verification(worklist_path: Path, extracted_dir: Path):
    """--apply-verification: read a completed worklist back and override
    extracted values with method="manual"."""
    rows = list(csv_mod.DictReader(worklist_path.open()))
    by_doc = {}
    for r in rows:
        if not r.get("verified_value"):
            continue
        by_doc.setdefault(r["doc_path"], []).append(r)

    updated = 0
    for doc_path, entries in by_doc.items():
        p = Path(doc_path)
        payload = json.loads(p.read_text())
        for entry in entries:
            field = entry["field"]
            try:
                value = json.loads(entry["verified_value"])
            except json.JSONDecodeError:
                value = entry["verified_value"]
            payload[field] = value
            payload.setdefault("provenance", {})[field] = {
                "page": int(entry["page"]), "snippet": entry["snippet"],
                "confidence": "high", "method": "manual",
            }
            updated += 1
        p.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return updated


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--apply-verification" in argv:
        idx = argv.index("--apply-verification")
        worklist_arg = Path(argv[idx + 1]) if len(argv) > idx + 1 else None
        cfg = load_config()
        worklist_path = worklist_arg or (cfg.resolve("data_dir") / "verify_worklist.csv")
        n = apply_verification(worklist_path, cfg.resolve("extracted_dir"))
        print(f"applied {n} verified field(s)", file=sys.stderr)
        sys.exit(0)
    sys.exit(main())
