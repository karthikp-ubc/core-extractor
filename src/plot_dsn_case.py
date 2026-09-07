#!/usr/bin/env python3
"""Charts for the DSN A* case document — reads combined_export.csv +
combined_export_CORE2023.csv (both from export_combined.py), writes two
PNGs to data/. Not a pipeline stage; a one-off analysis script, but kept
here (not inline in the report) so the figures are regenerable if the
underlying data changes.

Color use follows the project's dataviz convention: DSN (the subject) gets
its own hue throughout; the three confirmed A* peers share one hue as a
cohort; EuroSys (the closest same-tier A peer) gets a third — three
meaningful colors, not five arbitrary ones, so "which cohort is this bar"
reads at a glance.
"""
import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import load_config  # noqa: E402

# Reference palette (dataviz skill), light-mode steps.
BLUE = "#2a78d6"    # DSN — the subject
AQUA = "#1baf7a"    # confirmed A* peer cohort (SOSP, OSDI, ASPLOS)
ORANGE = "#eb6834"  # EuroSys — closest same-tier (A) peer
INK = "#0b0b0b"
SECONDARY_INK = "#52514e"
MUTED = "#898781"
GRIDLINE = "#e1e0d9"
SURFACE = "#fcfcfb"

A_STAR_PEERS = ["SOSP", "OSDI", "ASPLOS"]
A_PEER = "EuroSys"
SUBJECT = "DSN"


def color_for(acronym: str) -> str:
    if acronym == SUBJECT:
        return BLUE
    if acronym == A_PEER:
        return ORANGE
    return AQUA


def plot_peer_comparison(analysis_csv: Path, out_path: Path):
    df = pd.read_csv(analysis_csv, na_values=["-"])
    df = df[df["round"] == "ICORE2026"]
    venues = [SUBJECT] + A_STAR_PEERS + [A_PEER]
    sub = df[df["acronym"].isin(venues)].set_index("acronym").loc[venues]

    metrics = [
        ("citation_p25_venue_pct", "Citation — 25th pctl. band"),
        ("citation_p50_venue_pct", "Citation — 50th pctl. band"),
        ("author_strength_p25_venue_pct", "Author strength — 25th pctl. band"),
        ("author_strength_p50_venue_pct", "Author strength — 50th pctl. band"),
    ]

    fig, axes = plt.subplots(1, 4, figsize=(14, 4.2), facecolor=SURFACE)
    fig.suptitle(
        "DSN vs. confirmed A* peers and its closest same-tier (A) peer — "
        "ICORE2026 chart-derived percentiles",
        fontsize=11, color=INK, y=1.04, fontweight="bold")

    for ax, (col, title) in zip(axes, metrics):
        ax.set_facecolor(SURFACE)
        values = pd.to_numeric(sub[col], errors="coerce")
        colors = [color_for(v) for v in sub.index]
        bars = ax.bar(range(len(sub)), values, color=colors, width=0.62,
                       edgecolor=SURFACE, linewidth=1.5)
        for bar, val in zip(bars, values):
            if pd.notna(val):
                ax.annotate(f"{val:.0f}", (bar.get_x() + bar.get_width() / 2, val),
                            xytext=(0, 3), textcoords="offset points",
                            ha="center", fontsize=8.5, color=INK)
        ax.set_xticks(range(len(sub)))
        ax.set_xticklabels(sub.index, fontsize=8.5, color=SECONDARY_INK, rotation=20)
        ax.set_title(title, fontsize=9, color=SECONDARY_INK, pad=8)
        ax.set_ylim(0, 100)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.spines["bottom"].set_color(MUTED)
        ax.tick_params(axis="y", colors=MUTED, labelsize=7.5)
        ax.grid(axis="y", color=GRIDLINE, linewidth=0.8, zorder=0)
        ax.set_axisbelow(True)

    from matplotlib.patches import Patch
    legend_handles = [
        Patch(facecolor=BLUE, label="DSN (subject)"),
        Patch(facecolor=AQUA, label="Confirmed A* peers (SOSP, OSDI, ASPLOS)"),
        Patch(facecolor=ORANGE, label="EuroSys (closest same-tier A peer)"),
    ]
    fig.legend(handles=legend_handles, loc="lower center", ncol=3,
               frameon=False, fontsize=8.5, bbox_to_anchor=(0.5, -0.06))
    fig.text(0.5, -0.14,
              "Source: data/combined_export.csv (round=ICORE2026), columns "
              "citation_p{25,50}_venue_pct / author_strength_p{25,50}_venue_pct. "
              "Values are ICORE-computed percentile-band positions read from "
              "each venue's own centile-graph chart image (Stage 2, vision "
              "extraction) — see icore_metrics_labels.csv / cache/images/ for "
              "the source image per value.",
              ha="center", fontsize=7, color=MUTED, wrap=True)

    fig.tight_layout(rect=[0, 0.02, 1, 0.96])
    fig.savefig(out_path, dpi=200, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    print(f"wrote {out_path}", file=sys.stderr)


def plot_dsn_trend(analysis_csv: Path, core2023_csv: Path, out_path: Path):
    # combined_export*.csv renders missing values as the literal string "-"
    # (see export_combined.py) — na_values restores them to real NaN so the
    # numeric columns used below don't come back as dtype=object.
    i26 = pd.read_csv(analysis_csv, na_values=["-"])
    i26 = i26[(i26["acronym"] == SUBJECT) & (i26["round"] == "ICORE2026")].iloc[0]
    c23 = pd.read_csv(core2023_csv, na_values=["-"])
    c23 = c23[c23["acronym"] == SUBJECT].iloc[0]

    rounds = ["CORE2023", "ICORE2026"]
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.6), facecolor=SURFACE)
    fig.suptitle("DSN's own trajectory, CORE2023 → ICORE2026",
                  fontsize=11, color=INK, y=1.06, fontweight="bold")

    panels = [
        ("GS h5-index (self-reported)",
         [c23["gs_h5_index"], i26["gs_h5_index"]], "{:.0f}"),
        ("Citation, 50th pctl. band (venue)",
         [c23["citation_p50_venue_pct"], i26["citation_p50_venue_pct"]], "{:.0f}"),
        ("PC established researchers (%)",
         [100 * c23["pc_established_count"] / c23["pc_size"],
          100 * i26["pc_established_count"] / i26["pc_size"]], "{:.0f}%"),
    ]

    for ax, (title, values, fmt) in zip(axes, panels):
        ax.set_facecolor(SURFACE)
        ax.plot(rounds, values, color=BLUE, linewidth=2, marker="o",
                markersize=8, markerfacecolor=BLUE, markeredgecolor=SURFACE,
                markeredgewidth=1.5, zorder=3)
        for x, v in zip(rounds, values):
            ax.annotate(fmt.format(v), (x, v), xytext=(0, 10),
                        textcoords="offset points", ha="center",
                        fontsize=9.5, color=INK, fontweight="bold")
        ax.set_title(title, fontsize=9, color=SECONDARY_INK, pad=14)
        ax.spines[["top", "right"]].set_visible(False)
        ax.spines[["bottom", "left"]].set_color(MUTED)
        ax.tick_params(colors=MUTED, labelsize=8.5)
        ax.grid(axis="y", color=GRIDLINE, linewidth=0.8, zorder=0)
        ax.set_axisbelow(True)
        pad = (max(values) - min(values)) * 0.4 or max(values) * 0.15
        ax.set_ylim(min(values) - pad, max(values) + pad)

    fig.text(0.5, -0.06,
              "Source: data/combined_export_CORE2023.csv and "
              "data/combined_export.csv (round=ICORE2026), acronym=DSN. "
              "gs_h5_index/pc_established_count/pc_size are self-reported "
              "in DSN's own Data submission PDF (extracted/787_*.json); "
              "citation_p50_venue_pct is Stage-2 chart-derived.",
              ha="center", fontsize=7, color=MUTED, wrap=True)

    fig.tight_layout(rect=[0, 0.04, 1, 0.94])
    fig.savefig(out_path, dpi=200, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    print(f"wrote {out_path}", file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-csv", type=Path, default=None,
                         help="Default: <data_dir>/combined_export.csv")
    parser.add_argument("--core2023-csv", type=Path, default=None,
                         help="Default: <data_dir>/combined_export_CORE2023.csv")
    parser.add_argument("--out-dir", type=Path, default=None,
                         help="Default: <data_dir>")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config) if args.config else load_config()
    data_dir = cfg.resolve("data_dir")
    analysis_csv = args.analysis_csv or (data_dir / "combined_export.csv")
    core2023_csv = args.core2023_csv or (data_dir / "combined_export_CORE2023.csv")
    out_dir = args.out_dir or data_dir

    plot_peer_comparison(analysis_csv, out_dir / "dsn_case_peer_comparison.png")
    plot_dsn_trend(analysis_csv, core2023_csv, out_dir / "dsn_case_trend.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
