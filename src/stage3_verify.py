#!/usr/bin/env python3
"""Stage 3 — independent verification via OpenAlex (SPEC.md §6.5, optional).

Unlike every other stage, this one does fetch over the network: OpenAlex's
public API is a different host with an explicit, documented polite pool
(`mailto=`), not the portal.core.edu.au host this project deliberately
avoids crawling (see stage1_core.py's docstring). It still respects the
project-wide rate limit and caches every response to disk.

Purpose per SPEC: detect where ICORE's own venue resolution differs from
ours (folded-in co-located workshops, series splits) — NOT to replace
ICORE's numbers. Requires aliases.yaml entries with openalex_source_ids for
any venue you want checked (SPEC §7: DBLP key alone doesn't give you an
OpenAlex source id).
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import load_config  # noqa: E402
from aliases import load_aliases, check_openalex_ids_live  # noqa: E402

DISCREPANCIES_COLUMNS = ["core_id", "venue_key", "round", "icore_value",
                          "openalex_value", "relative_diff", "note"]


def h_index(citation_counts: list[int]) -> int:
    """Standard h-index: largest h such that h works have >= h citations
    each. Pure function, no I/O — unit-tested independently of the network
    layer per SPEC.md §9."""
    counts = sorted(citation_counts, reverse=True)
    h = 0
    for i, c in enumerate(counts, start=1):
        if c >= i:
            h = i
        else:
            break
    return h


class RateLimiter:
    def __init__(self, seconds_per_request: float):
        self.seconds = seconds_per_request
        self._last = 0.0

    def wait(self):
        elapsed = time.monotonic() - self._last
        if elapsed < self.seconds:
            time.sleep(self.seconds - elapsed)
        self._last = time.monotonic()


def fetch_json(url, params, cache_dir: Path, limiter: RateLimiter, session):
    cache_key = str(abs(hash((url, tuple(sorted(params.items()))))))
    cache_path = cache_dir / f"{cache_key}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())

    for attempt in range(5):
        limiter.wait()
        resp = session.get(url, params=params, timeout=30)
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
        data = resp.json()
        cache_path.write_text(json.dumps(data))
        return data
    raise RuntimeError(f"exhausted retries fetching {url} params={params}")


def source_exists(source_id: str, base_url: str, session) -> bool:
    resp = session.get(f"{base_url}/sources/{source_id}", timeout=30)
    return resp.status_code == 200


def fetch_citation_counts(source_id, base_url, window_start_year, session,
                            cache_dir, limiter):
    """cited_by_count for every work published by this source since
    window_start_year, paginated via OpenAlex's cursor paging."""
    counts = []
    cursor = "*"
    while cursor:
        params = {
            "filter": f"primary_location.source.id:{source_id},"
                      f"from_publication_date:{window_start_year}-01-01",
            "per-page": 200, "cursor": cursor,
            "select": "cited_by_count",
        }
        data = fetch_json(f"{base_url}/works", params, cache_dir, limiter, session)
        counts.extend(w["cited_by_count"] for w in data.get("results", []))
        cursor = (data.get("meta") or {}).get("next_cursor")
        if not data.get("results"):
            break
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="OpenAlex cross-check of ICORE-reported metrics "
                     "(SPEC.md §6.5). Requires aliases.yaml entries with "
                     "openalex_source_ids.")
    parser.add_argument("--metrics-parquet", type=Path, default=None)
    parser.add_argument("--aliases", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--window-years", type=int, default=5,
                         help="h-index computed over this many trailing "
                              "years (default 5, per SPEC.md §6.5).")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config) if args.config else load_config()
    if cfg.contact_email_is_placeholder:
        print("error: config.yaml's contact_email is still the placeholder "
              "\"you@example.com\" — set your real address before hitting "
              "OpenAlex's polite pool (see config.yaml's header comment).",
              file=sys.stderr)
        return 1

    metrics_path = args.metrics_parquet or (cfg.resolve("data_dir") / "icore_metrics.parquet")
    aliases_path = args.aliases or None
    out_dir = args.out_dir or cfg.resolve("data_dir")
    cache_dir = cfg.resolve("cache_dir") / "openalex"

    aliases = load_aliases(aliases_path) if aliases_path else load_aliases()
    checked_aliases = [a for a in aliases if a.openalex_source_ids]
    if not checked_aliases:
        print("no aliases.yaml entries with openalex_source_ids — nothing "
              "to verify. This stage is optional; skipping.", file=sys.stderr)
        pd.DataFrame(columns=DISCREPANCIES_COLUMNS).to_csv(
            out_dir / "metric_discrepancies.csv", index=False)
        return 0

    session = requests.Session()
    session.headers["User-Agent"] = (
        f"core-extractor/1.0 (mailto:{cfg.contact_email})")
    limiter = RateLimiter(cfg.rate_limit_seconds)
    base_url = cfg.openalex.base_url

    id_errors = check_openalex_ids_live(
        checked_aliases, lambda sid: source_exists(sid, base_url, session))
    if id_errors:
        print("error: aliases.yaml has 404ing OpenAlex source id(s) — hard "
              "error per SPEC.md §7:", file=sys.stderr)
        for e in id_errors:
            print(f"  {e}", file=sys.stderr)
        return 1

    metrics = pd.read_parquet(metrics_path) if metrics_path.exists() else \
        pd.DataFrame(columns=["core_id", "round", "metric_name", "value"])
    window_start_year = datetime.now(timezone.utc).year - args.window_years

    rows = []
    for alias in checked_aliases:
        all_counts = []
        for source_id in alias.openalex_source_ids:
            all_counts.extend(fetch_citation_counts(
                source_id, base_url, window_start_year, session,
                cache_dir, limiter))
        our_h = h_index(all_counts)

        icore_rows = metrics[
            (metrics["core_id"].astype(str) == str(alias.core_id))
            & (metrics["metric_name"] == "author_strength_raw_value")
        ]
        if icore_rows.empty:
            rows.append({
                "core_id": alias.core_id, "venue_key": alias.key,
                "round": None, "icore_value": None,
                "openalex_value": our_h, "relative_diff": None,
                "note": "no ICORE author_strength_raw_value on record for "
                        "this venue (Stage 2 coverage gap, or chart didn't "
                        "print a raw value) — nothing to compare against",
            })
            continue
        for _, r in icore_rows.iterrows():
            icore_val = r["value"]
            rel_diff = (abs(our_h - icore_val) / icore_val) if icore_val else None
            rows.append({
                "core_id": alias.core_id, "venue_key": alias.key,
                "round": r["round"], "icore_value": icore_val,
                "openalex_value": our_h, "relative_diff": rel_diff,
                "note": None,
            })

    out_df = pd.DataFrame(rows, columns=DISCREPANCIES_COLUMNS)
    out_df = out_df.sort_values("relative_diff", ascending=False, na_position="last")
    out_path = out_dir / "metric_discrepancies.csv"
    out_df.to_csv(out_path, index=False)
    print(f"wrote {len(out_df)} row(s) to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
