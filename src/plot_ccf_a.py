#!/usr/bin/env python3
"""How do CCF-A conferences fare in CORE/ICORE? — reads
combined_export_ICORE2026.csv (export_combined.py), ccf_A_status.csv and
chart_for_code.csv (list_ccf.py), icore_metrics.parquet (stage2_metrics.py);
writes two PNGs and one CSV to data/. Not a pipeline stage — a one-off
analysis script in the same pattern as plot_dsn_case.py.

Cross-field comparison. ICORE's centile charts are per-FoR: a venue's
"% of papers in the top-25% band" is measured against its own field, and
the same chart shows that field's A*-tier and A-tier averages. Raw venue
percentages are therefore NOT comparable across fields (a 4613 theory venue
and a 4603 vision venue sit on different scales), so every venue is plotted
as its gap to its own field's A* baseline:

    gap = <metric>_p25_venue_pct - <metric>_p25_A_star_pct   (pct points)

with the field's A-tier baseline (A_pct - A_star_pct) as a reference tick.
The 25% band is used because it is the band ICORE's own Data submissions
cite ("top25" fields) and, unlike the 50% band, it isn't saturated near 100.

Each venue's chart is the one for the FoR in chart_for_code.csv (4606 for
the original cohort, CORE's primary FoR for the rest). Any ICORE2026 metric
row read from a different FoR's chart (a Stage 2 fallback) is excluded, not
silently mixed in.

Colors follow the dataviz reference palette's first three categorical
slots — the only three cleared for a dot plot (all-pairs) — one per CORE
rank group; every dot is also named on the y-axis, so identity is never
color-alone, and the CSV is the table view.
"""
import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import load_config  # noqa: E402

ROUND = "ICORE2026"
BAND = "p25"
METRICS = [("citation", "Citations"), ("author_strength", "Author strength")]

BLUE = "#2a78d6"    # CORE A*
ORANGE = "#eb6834"  # CORE A
AQUA = "#1baf7a"    # below A / not on the letter scale
INK = "#0b0b0b"
SECONDARY_INK = "#52514e"
MUTED = "#898781"
GRIDLINE = "#e1e0d9"
SURFACE = "#fcfcfb"

GROUPS = [("A*", BLUE), ("A", ORANGE), ("Below A / other", AQUA)]
GROUP_COLOR = dict(GROUPS)


def rank_group(rank):
    if rank in ("A*", "A"):
        return rank
    return "Below A / other"


def short_category(cat):
    return {
        "Computer Architecture / Parallel and Distributed Computing / Storage System":
            "Architecture / parallel / storage",
        "Software Engineering / System Software / Programming Languages":
            "Software eng. / systems / PL",
        "Database / Data Mining / Content Retrieval": "Databases / data mining / IR",
        "Human-Computer Interaction and Pervasive Computing": "HCI / pervasive",
        "Cross-disciplinary / Comprehensive / Emerging": "Cross-disciplinary",
        "Network and Information Security": "Security",
        "Computer Graphics and Multimedia": "Graphics / multimedia",
    }.get(cat, cat)


def build_table(data_dir):
    status = pd.read_csv(data_dir / "ccf_A_status.csv", dtype=str)
    chart_for = (pd.read_csv(data_dir / "chart_for_code.csv", dtype=str)
                 .set_index("core_id")["for_code"])
    exp = pd.read_csv(data_dir / f"combined_export_{ROUND}.csv",
                      na_values=["-"], dtype={"core_id": str})

    # Which FoR each ICORE2026 metric was actually read from (its source
    # image's filename carries _FOR<code>_), per venue and metric family.
    m = pd.read_parquet(data_dir / "icore_metrics.parquet")
    m = m[m["round"] == ROUND].copy()
    m["core_id"] = m["core_id"].astype(str)
    m["chart_for"] = m["raw_payload_ref"].str.extract(r"_FOR([A-Za-z0-9]+)_centile")[0]
    m["family"] = m["metric_name"].str.extract(r"^(citation|author_strength)_")[0]
    read_from = (m.dropna(subset=["family"])
                 .groupby(["core_id", "family"])["chart_for"]
                 .agg(lambda s: ",".join(sorted(set(s.dropna())))))

    rows = []
    for s in status.itertuples():
        e = exp[exp["core_id"] == s.core_id]
        if e.empty:
            print(f"warning: {s.ccf_abbr} (core_id {s.core_id}) not in "
                  f"{ROUND} export — skipped", file=sys.stderr)
            continue
        e = e.iloc[0]
        want = chart_for.get(s.core_id)
        row = {"ccf_abbr": s.ccf_abbr.split(" （")[0], "core_acronym": e["acronym"],
               "core_id": s.core_id, "ccf_category": short_category(s.ccf_category),
               "core_rank": e["rank"], "rank_group": rank_group(e["rank"]),
               "chart_for": want}
        for fam, _ in METRICS:
            got = read_from.get((s.core_id, fam))
            ok = got == want
            v = e.get(f"{fam}_{BAND}_venue_pct")
            astar = e.get(f"{fam}_{BAND}_A_star_pct")
            a = e.get(f"{fam}_{BAND}_A_pct")
            if not ok:
                print(f"warning: {s.ccf_abbr} {fam}: chart read from FoR "
                      f"{got}, expected {want} — excluded", file=sys.stderr)
                v = astar = a = float("nan")
            row.update({
                f"{fam}_venue_pct": v, f"{fam}_field_A_star_pct": astar,
                f"{fam}_field_A_pct": a,
                f"{fam}_gap_vs_A_star": v - astar,
                f"{fam}_A_tier_gap_vs_A_star": a - astar,
            })
        rows.append(row)
    df = pd.DataFrame(rows)

    # Sanity check: within one FoR every venue's chart should show the same
    # A*/A baselines. A disagreement means a misread chart value.
    for fam, _ in METRICS:
        for col in (f"{fam}_field_A_star_pct", f"{fam}_field_A_pct"):
            spread = df.groupby("chart_for")[col].nunique()
            for code in spread[spread > 1].index:
                vals = df.loc[df["chart_for"] == code, ["ccf_abbr", col]]
                print(f"warning: {col} differs within FoR {code}: "
                      f"{vals.values.tolist()}", file=sys.stderr)
    return df


def plot_gap(df, out_path):
    df = df.sort_values("citation_gap_vs_A_star", ascending=True).reset_index(drop=True)
    n = len(df)
    fig, axes = plt.subplots(1, 2, figsize=(11, 0.24 * n + 2.2),
                             sharey=True, facecolor=SURFACE)
    fig.suptitle(f"CCF-A conferences in CORE: share of papers in the top-25% band, "
                 f"relative to their own field's A* average ({ROUND})",
                 fontsize=11, color=INK, fontweight="bold", y=0.995)

    lo = min(df[f"{f}_gap_vs_A_star"].min() for f, _ in METRICS)
    hi = max(df[f"{f}_gap_vs_A_star"].max() for f, _ in METRICS)
    lo = min(lo, min(df[f"{f}_A_tier_gap_vs_A_star"].min() for f, _ in METRICS))
    pad = 3
    for ax, (fam, title) in zip(axes, METRICS):
        ax.set_facecolor(SURFACE)
        y = range(n)
        ax.axvline(0, color=SECONDARY_INK, linewidth=1, zorder=1)
        ax.scatter(df[f"{fam}_A_tier_gap_vs_A_star"], y, marker="|", s=90,
                   color=MUTED, linewidths=1.5, zorder=2)
        ax.scatter(df[f"{fam}_gap_vs_A_star"], y,
                   c=df["rank_group"].map(GROUP_COLOR), s=48, zorder=3,
                   edgecolors=SURFACE, linewidths=1.5)
        ax.set_title(title, fontsize=9.5, color=SECONDARY_INK, pad=6)
        ax.set_xlim(lo - pad, hi + pad)
        ax.set_ylim(-0.8, n - 0.2)
        ax.set_xlabel("percentage points vs. field's A* average", fontsize=8,
                      color=MUTED)
        ax.grid(axis="x", color=GRIDLINE, linewidth=0.8, zorder=0)
        ax.set_axisbelow(True)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.spines["bottom"].set_color(MUTED)
        ax.tick_params(axis="x", colors=MUTED, labelsize=7.5)
        ax.tick_params(axis="y", length=0)
    labels = [f"{r.ccf_abbr}  ({r.core_rank})" if r.rank_group == "Below A / other"
              else r.ccf_abbr for r in df.itertuples()]
    axes[0].set_yticks(range(n))
    axes[0].set_yticklabels(labels, fontsize=7.5, color=INK)

    handles = [Line2D([], [], marker="o", linestyle="", markersize=7,
                      markerfacecolor=c, markeredgecolor=SURFACE, label=f"CORE {g}"
                      if g != "Below A / other" else "CORE below A / not letter-ranked")
               for g, c in GROUPS]
    handles.append(Line2D([], [], marker="|", linestyle="", markersize=10,
                          color=MUTED, markeredgewidth=1.5,
                          label="Field's CORE-A average"))
    fig.legend(handles=handles, loc="lower center", ncol=4, frameon=False,
               fontsize=8, bbox_to_anchor=(0.5, 0.0))
    fig.text(0.5, -0.025,
             f"Rows sorted by the citation gap. 0 = the venue's field A* average "
             f"(each venue measured within its chart FoR: 4606 for the original "
             f"cohort, CORE's primary FoR otherwise). Source: "
             f"data/ccf_a_comparison.csv, from Stage 2 chart reads "
             f"(*_{BAND}_venue_pct − *_{BAND}_A_star_pct).",
             ha="center", fontsize=7, color=MUTED, wrap=True)
    fig.tight_layout(rect=[0, 0.035, 1, 0.985])
    fig.savefig(out_path, dpi=200, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    print(f"wrote {out_path}", file=sys.stderr)


def plot_crosswalk(df, out_path):
    groups = [g for g, _ in GROUPS]
    ct = (pd.crosstab(df["ccf_category"], df["rank_group"])
          .reindex(columns=groups, fill_value=0))
    ct = ct.loc[ct.sum(axis=1).sort_values().index]
    fig, ax = plt.subplots(figsize=(7, 0.42 * len(ct) + 1.6), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    left = pd.Series(0, index=ct.index)
    for g, color in GROUPS:
        vals = ct[g]
        ax.barh(range(len(ct)), vals, left=left, color=color, height=0.62,
                edgecolor=SURFACE, linewidth=2, label=None)
        left = left + vals
    for i, total in enumerate(ct.sum(axis=1)):
        ax.annotate(str(total), (total, i), xytext=(5, 0), textcoords="offset points",
                    va="center", fontsize=8.5, color=INK)
    below = df[df["rank_group"] == "Below A / other"]
    for i, cat in enumerate(ct.index):
        names = below[below["ccf_category"] == cat]
        if len(names):
            txt = ", ".join(f"{r.ccf_abbr} ({r.core_rank})" for r in names.itertuples())
            ax.annotate(txt, (ct.loc[cat].sum(), i), xytext=(22, 0),
                        textcoords="offset points", va="center", fontsize=7.5,
                        color=SECONDARY_INK, annotation_clip=False)
    ax.set_yticks(range(len(ct)))
    ax.set_yticklabels(ct.index, fontsize=8.5, color=INK)
    ax.set_xlim(0, ct.sum(axis=1).max() + 0.5)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_xlabel("CCF-A conferences", fontsize=8, color=MUTED)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color(MUTED)
    ax.tick_params(axis="x", colors=MUTED, labelsize=7.5)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color=GRIDLINE, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    n_astar = int((df["rank_group"] == "A*").sum())
    ax.set_title(f"{n_astar} of {len(df)} CCF-A conferences are CORE A* ({ROUND}), "
                 f"by CCF category", fontsize=11, color=INK, fontweight="bold",
                 loc="left", pad=10)
    fig.legend(handles=[Patch(facecolor=c, label=f"CORE {g}" if g != "Below A / other"
                              else "CORE below A / not letter-ranked")
                        for g, c in GROUPS],
               loc="lower center", ncol=3, frameon=False, fontsize=8,
               bbox_to_anchor=(0.5, -0.02))
    fig.tight_layout(rect=[0, 0.05, 1, 1])
    fig.savefig(out_path, dpi=200, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    print(f"wrote {out_path}", file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, default=None, help="Default: <data_dir>")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    cfg = load_config(args.config) if args.config else load_config()
    data_dir = cfg.resolve("data_dir")
    out_dir = args.out_dir or data_dir

    df = build_table(data_dir)
    csv_path = out_dir / "ccf_a_comparison.csv"
    df.sort_values("citation_gap_vs_A_star", ascending=False).to_csv(
        csv_path, index=False, float_format="%.0f")
    print(f"wrote {csv_path} ({len(df)} venues)", file=sys.stderr)
    plot_gap(df, out_dir / "ccf_a_core_gap.png")
    plot_crosswalk(df, out_dir / "ccf_a_rank_crosswalk.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
