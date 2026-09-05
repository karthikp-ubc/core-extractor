#!/usr/bin/env python3
"""Orchestrates the whole local-file pipeline in one command.

SPEC.md §10's acceptance criterion is "a cold cache produces
analysis.parquet from `--for-code`". This project cannot do that literally
autonomously end-to-end: stage1_core.py, stage1b_detail.py, stage2_metrics.py
and stage2b_docs.py all require you to have manually saved the relevant
pages/images/PDFs first (portal.core.edu.au's robots.txt disallows
AI-agent fetching site-wide — see stage1_core.py's docstring). What this
script does is run every stage against whatever local inputs already exist,
skip (loudly, not silently) any stage whose required inputs aren't there
yet, and always finish with stage4_join.py's coverage summary — so a rerun
after you've added more inputs picks up exactly where it left off.

Usage:
    python run_all.py --for-code 4606 --round ICORE2026 \\
        --master-csv inputs/4606-all.csv
"""
import argparse
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"


def run_stage(name, args):
    print(f"\n=== {name} ===", file=sys.stderr)
    result = subprocess.run([sys.executable, str(SRC / name), *args])
    return result.returncode == 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--master-csv", type=Path, default=None,
                         help="Stage 1 input — the Export CSV for one FoR "
                              "(see stage1_core.py). Skipped if not given.")
    parser.add_argument("--round", default=None,
                         help="Required with --master-csv.")
    parser.add_argument("--for-code", default=None)
    parser.add_argument("--for-name", default=None)
    parser.add_argument("--detail-pages", type=Path, nargs="*", default=None,
                         help="Stage 1b input — saved detail-page HTML "
                              "files. Skipped if not given.")
    parser.add_argument("--no-llm", action="store_true",
                         help="Passed through to stage2b_docs.py.")
    parser.add_argument("--skip-stage3", action="store_true",
                         help="Skip the OpenAlex cross-check (it's optional "
                              "per SPEC.md §6.5 and needs network + a "
                              "configured contact_email).")
    parser.add_argument("--apply-verification", action="store_true",
                         help="Run stage2b_docs.py's --apply-verification "
                              "instead of a normal extraction pass.")
    args = parser.parse_args(argv)

    ok = True

    if args.apply_verification:
        ok &= run_stage("stage2b_docs.py", ["--apply-verification"])
        return 0 if ok else 1

    if args.master_csv:
        if not args.round:
            parser.error("--round is required with --master-csv")
        stage1_args = [str(args.master_csv), "--round", args.round]
        if args.for_code:
            stage1_args += ["--for-code", args.for_code]
        if args.for_name:
            stage1_args += ["--for-name", args.for_name]
        ok &= run_stage("stage1_core.py", stage1_args)
    else:
        print("\n=== stage1_core.py === skipped: no --master-csv given",
              file=sys.stderr)

    if args.detail_pages:
        ok &= run_stage("stage1b_detail.py",
                         [str(p) for p in args.detail_pages] + ["--append"])
    else:
        print("\n=== stage1b_detail.py === skipped: no --detail-pages given",
              file=sys.stderr)

    # stage2_metrics.py and stage2b_docs.py read links.csv themselves and
    # log per-link gaps to unmatched.csv rather than needing an explicit
    # file list — always run them; they no-op cleanly if links.csv is empty.
    run_stage("stage2_metrics.py", [])
    stage2b_args = ["--no-llm"] if args.no_llm else []
    run_stage("stage2b_docs.py", stage2b_args)

    if not args.skip_stage3:
        run_stage("stage3_verify.py", [])
    else:
        print("\n=== stage3_verify.py === skipped: --skip-stage3",
              file=sys.stderr)

    ok &= run_stage("stage4_join.py", [])

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
