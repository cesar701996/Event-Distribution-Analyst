"""
Step 8 - Charts.

For each flagged name: the last 12 earnings reactions (signed bars) with the
current implied event move, our forecast and the raw straddle drawn as +/- lines.
Plus one summary chart: forecast vs implied range for every name with an event.

Colors: up = blue #2a78d6, down = red #e34948 (validated colorblind-safe pair on the
light surface); reference lines use text-ink grays so they never read as a series.
Outputs: charts/<TK>_earnings_moves.png, charts/summary_forecast_vs_implied.png
"""
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from config import DATA, CHARTS, AS_OF

UP, DOWN = "#2a78d6", "#e34948"
INK, INK2, MUTED, GRID, SURF = "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0", "#fcfcfb"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": MUTED,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                     "figure.facecolor": SURF, "axes.facecolor": SURF})


def style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(MUTED)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


def moves_chart(tk, g, f, im):
    fig, ax = plt.subplots(figsize=(10, 5.6), dpi=200)
    x = range(len(g))
    colors = [UP if m > 0 else DOWN for m in g.move_pct]
    ax.bar(x, g.move_pct, width=0.62, color=colors, edgecolor=SURF, linewidth=1.5, zorder=3)
    ax.axhline(0, color=MUTED, linewidth=1, zorder=4)
    xmax = len(g) - 0.4
    lines = [(im.implied_event_move_pct, INK, (0, (6, 3)), 1.6, f"Implied event move ±{im.implied_event_move_pct:.1f}%"),
             (f.forecast_move_pct, INK2, (0, (1.5, 2)), 1.8, f"Our forecast ±{f.forecast_move_pct:.1f}%"),
             (im.straddle_pct, MUTED, "-", 0.9, f"Raw straddle ±{im.straddle_pct:.1f}% (incl. {int(im.N_post) - 1} ordinary days)")]
    for val, c, ls, lw, lab in lines:
        for sgn in (1, -1):
            ax.plot([-0.5, xmax], [sgn * val, sgn * val], color=c, linestyle=ls, linewidth=lw, zorder=5)
    # direct labels at the right edge; nudge apart if they collide
    lim = max(g.abs_move_pct.max(), im.straddle_pct) + 3
    step = 0.055 * lim                       # minimum label spacing, scaled to the axis
    ys = sorted([(v, c, lab) for v, c, _, _, lab in lines], key=lambda t: t[0])
    placed = []
    for v, c, lab in ys:
        y = v
        while any(abs(y - p) < step for p in placed):
            y += step
        placed.append(y)
        ax.text(xmax + 0.1, y, lab, va="center", ha="left", fontsize=8.5, color=c if c != MUTED else INK2)
    # label only the extremes
    for idx in (g.move_pct.idxmax(), g.move_pct.idxmin()):
        pos = list(g.index).index(idx)
        val = g.loc[idx, "move_pct"]
        ax.text(pos, val + (0.8 if val > 0 else -0.8), f"{val:+.1f}%", ha="center",
                va="bottom" if val > 0 else "top", fontsize=8.5, color=INK)
    ax.set_xticks(list(x))
    ax.set_xticklabels([pd.Timestamp(d).strftime("%b '%y") for d in g.event_date], fontsize=8.5)
    ax.set_ylim(-lim, lim)
    ax.set_xlim(-0.6, len(g) + 3.2)
    ax.set_ylabel("1-day earnings reaction (%)")
    style(ax)
    ax.legend(handles=[Patch(color=UP, label="Up move"), Patch(color=DOWN, label="Down move")],
              loc="lower right", frameon=False, fontsize=8.5, ncol=2)
    fig.text(0.02, 0.965, f"{tk}: last 12 earnings moves vs what options imply for {pd.Timestamp(im.event_date):%b %-d}",
             ha="left", fontsize=13, color=INK, fontweight="bold")
    fig.text(0.02, 0.945, f"Close-to-close reaction to each report. Options: {pd.Timestamp(im.post_expiry):%b %-d} expiry, "
             f"{AS_OF:%b %-d} close marks. Mean |move| {f.mean_abs_12:.1f}%, last 4 {f.mean_abs_last4:.1f}%.\n"
             f"25-delta skew (put IV minus call IV): market {f.market_rr25:.1f} vols, our view {f.model_rr25_ours:.1f} vols.",
             ha="left", va="top", fontsize=9, color=INK2, linespacing=1.5)
    fig.text(0.02, 0.015, "Source: yfinance prices and option chains, SEC EDGAR 8-K dates. Event move strips the ordinary "
             "days out of the straddle using the ATM term structure.", fontsize=7.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.03, 1, 0.87))
    out = CHARTS / f"{tk}_earnings_moves.png"
    fig.savefig(out, facecolor=SURF)
    plt.close(fig)
    return out


def summary_chart(fc, imp):
    fig, ax = plt.subplots(figsize=(10, 4.4), dpi=200)
    order = list(fc.sort_values("event_date").ticker)[::-1]
    for i, tk in enumerate(order):
        f, im = fc.set_index("ticker").loc[tk], imp.set_index("ticker").loc[tk]
        ax.plot([im.implied_event_move_low_pct, im.implied_event_move_high_pct], [i, i], color=GRID,
                linewidth=9, solid_capstyle="round", zorder=2)
        ax.scatter(im.implied_event_move_pct, i, s=70, color=INK, zorder=4, edgecolor=SURF, linewidth=1.5)
        ax.scatter(f.forecast_move_pct, i, s=80, marker="D", color=UP, zorder=5, edgecolor=SURF, linewidth=1.5)
        ax.scatter(f.mean_abs_12, i, s=60, facecolor=SURF, edgecolor=INK2, linewidth=1.4, zorder=3)
        flags = [n for n, on in (("vol", f.vol_flag), ("skew", f.skew_flag), ("direction", f.dir_flag)) if on]
        tag = ("trade idea: " + " + ".join(flags)) if flags else "no trade"
        ax.text(1.01, i, f"size gap {f.gap_rel_pct:+.0f}%\n{tag}", va="center", fontsize=8.5, color=INK2,
                transform=ax.get_yaxis_transform())
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels([f"{t}  {pd.Timestamp(fc.set_index('ticker').loc[t].event_date):%b %-d}" for t in order], fontsize=9.5)
    ax.set_xlim(4, 18.5)
    ax.set_xlabel("Expected absolute 1-day earnings move (% of spot)")
    style(ax)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.grid(axis="y", visible=False)
    handles = [plt.Line2D([], [], color=GRID, linewidth=8, label="Implied range (3 term-structure methods)"),
               plt.Line2D([], [], marker="o", linestyle="", color=INK, markersize=7, label="Implied event move (central)"),
               plt.Line2D([], [], marker="D", linestyle="", color=UP, markersize=7, label="Our forecast"),
               plt.Line2D([], [], marker="o", linestyle="", markerfacecolor=SURF, markeredgecolor=INK2, markersize=7,
                          label="12-quarter mean |move|")]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2, frameon=False, fontsize=8)
    fig.text(0.02, 0.94, "Forecast vs implied earnings move, events Nov 20 - Dec 20 2026", ha="left",
             fontsize=13, color=INK, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 0.86, 0.92))
    out = CHARTS / "summary_forecast_vs_implied.png"
    fig.savefig(out, facecolor=SURF)
    plt.close(fig)
    return out


def main():
    fc = pd.read_csv(DATA / "forecast.csv")
    imp = pd.read_csv(DATA / "implied.csv")
    mv = pd.read_csv(DATA / "earnings_moves.csv")
    for _, f in fc[fc.trade_idea].iterrows():
        g = mv[mv.ticker == f.ticker].reset_index(drop=True)
        print(moves_chart(f.ticker, g, f, imp.set_index("ticker").loc[f.ticker]))
    print(summary_chart(fc, imp))


if __name__ == "__main__":
    main()
