"""Figure: clearing-house location with delivery probability AND Sun blockage, over 200 years.

Answers "Ceres has the best packet delivery" (true over one relay period) against the
full 200-year span and the market's timers.

  A  first-try delivery probability to the hub, by year (1-year rolling mean)
  B  the same metric over one relay period (1,737.5 d) vs 200 years
  C  expected one-way time including hop retries (delivery loss turned into delay)
  D  single-relay blockages longer than the 30-day packet lifetime

Delivery: for each other settlement, the best open backbone route's first-try success
Π(1 - loss per hop), averaged over settlements. Expected time: fastest open route's light
time + Σ p/(1-p) × R_h per hop (each lost launch waits R_h = 2 × flight + 60 min).

Usage (from the repo root):  python3 network/python/hub_tradeoff.py
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import network_graph as n
from hub_plots import COLOR, GRID, HUBS, INK, MUTED, spells, style

YEARS = 200
PERIOD_D = 365.2568983 * 8 ** 0.75  # one relay orbit, the teammate's window


def sample(s, hub, t_days):
    p, x = [], []
    for o in n.SETTLEMENTS:
        if o == hub:
            continue
        rs = [r for r in s.routes(o, hub, t_days) if r["open"]]
        if not rs:
            continue
        p.append(max(r["p_first_try"] for r in rs))
        f = rs[0]
        x.append(f["light_min"] + sum(h["loss"] / (1 - h["loss"]) * (2 * (h["ta"] - h["te"]) * 1440 + 60)
                                      for h in f["hops"]))
    return np.mean(p), np.mean(x)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "notes" / "hub-tradeoff.png"))
    args = ap.parse_args()
    s = n.System()
    t = np.arange(0, YEARS * 365.25, 30.0)
    data = {}
    for hub in HUBS:
        P, X = zip(*(sample(s, hub, d) for d in t))
        runs, frac, both = spells(s, hub, int(YEARS * 365.25))
        P, X = np.array(P), np.array(X)
        first = t < PERIOD_D
        data[hub] = dict(P=P, X=X, runs=np.array(runs), frac=frac, both=both,
                         p_period=P[first].mean(), p_200=P.mean(), x_200=X.mean(),
                         x_worst_decade=max(X[(t >= d * 365.25) & (t < (d + 10) * 365.25)].mean()
                                            for d in range(0, YEARS, 10)))
        d = data[hub]
        print(f"{hub}: delivery period {d['p_period']:.4f} / 200y {d['p_200']:.4f}; expected time "
              f"{d['x_200']:.1f} min (worst decade {d['x_worst_decade']:.1f}); spells>30d "
              f"{int((d['runs'] > 30).sum())}/{len(d['runs'])}, longest {d['runs'].max()} d")

    fig, axes = plt.subplots(2, 2, figsize=(14, 10), facecolor="#fcfcfb")
    fig.subplots_adjust(hspace=0.55, wspace=0.3, top=0.85, bottom=0.08, left=0.07, right=0.96)
    fig.suptitle("Clearing house over 200 years: delivery probability and Sun blockage together",
                 x=0.07, ha="left", fontsize=16, fontweight="bold", color=INK)
    fig.text(0.07, 0.905, "Ceres delivers best for its first ~30 years, then trails for ~70. Earth is steady, "
             "never passes the 30-day packet lifetime, and ties Mars on delivery.", fontsize=11, color=MUTED)
    years = t / 365.25

    # A: delivery probability over time
    ax = axes[0, 0]
    k = 12
    for hub in reversed(HUBS):
        sm = np.convolve(data[hub]["P"], np.ones(k) / k, mode="valid") * 100
        ax.plot(years[k - 1:], sm, color=COLOR[hub], lw=2, label=hub, zorder=3 if hub == "Earth" else 2)
        ax.text(YEARS + 2, sm[-1], hub, fontsize=8.5, color=INK, va="center")
    ax.axvspan(0, PERIOD_D / 365.25, color="#d9d8d3", alpha=0.6, lw=0)
    ax.text(PERIOD_D / 365.25 + 1, ax.get_ylim()[0] + 0.15 if False else 76.6, "teammate's window\n(one relay period)",
            fontsize=8, color=MUTED, va="bottom")
    ax.set_xlim(0, YEARS + 18)
    ax.set_xlabel("Years from epoch", color=MUTED, fontsize=9)
    ax.set_ylabel("First-try delivery to hub, %", color=MUTED, fontsize=9)
    h, l = ax.get_legend_handles_labels()
    ax.legend(h[::-1], l[::-1], frameon=False, fontsize=8.5, loc="upper right", ncol=3)
    style(ax, "A  Delivery probability by year",
          "Mean over the 8 other settlements of the best open route's first-try success (1-year rolling)")

    # B: dot plot, period vs 200 years (no zero baseline needed for dots)
    ax = axes[0, 1]
    for i, hub in enumerate(HUBS):
        a, b = data[hub]["p_period"] * 100, data[hub]["p_200"] * 100
        ax.plot([a, b], [i, i], color="#b8b7b1", lw=1.5, zorder=1)
        ax.scatter([a], [i], s=70, facecolors="#fcfcfb", edgecolors=COLOR[hub], linewidths=2, zorder=2)
        ax.scatter([b], [i], s=70, color=COLOR[hub], zorder=2)
        ax.text(a, i + 0.22, f"{a:.2f}", ha="center", fontsize=8.5, color=INK)
        ax.text(b, i - 0.32, f"{b:.2f}", ha="center", fontsize=8.5, color=INK)
    ax.set_yticks(range(len(HUBS)), HUBS, fontsize=9.5)
    ax.set_ylim(-0.7, 2.7)
    lo = min(min(d["p_period"], d["p_200"]) for d in data.values()) * 100
    hi = max(max(d["p_period"], d["p_200"]) for d in data.values()) * 100
    ax.set_xlim(lo - 0.4, hi + 0.4)
    ax.set_xlabel("First-try delivery to hub, %", color=MUTED, fontsize=9)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.scatter([], [], s=60, facecolors="#fcfcfb", edgecolors=MUTED, linewidths=2, label="first 4.8 years")
    ax.scatter([], [], s=60, color=MUTED, label="200 years")
    ax.legend(frameon=False, fontsize=8.5, loc="lower right")
    style(ax, "B  The window decides the ranking",
          "Ceres leads in one relay period; over 200 years Earth and Mars lead (differences < 0.3 points)")

    # C: expected time with retries (dot = 200-year mean, bar end = worst decade)
    ax = axes[1, 0]
    for i, hub in enumerate(HUBS):
        m, w = data[hub]["x_200"], data[hub]["x_worst_decade"]
        ax.plot([m, w], [i, i], color=COLOR[hub], lw=3, alpha=0.35, solid_capstyle="round")
        ax.scatter([m], [i], s=70, color=COLOR[hub], zorder=3)
        ax.text(w + 0.4, i, f"mean {m:.1f} · worst decade {w:.1f} min", va="center", fontsize=8.5, color=INK)
    ax.set_yticks(range(len(HUBS)), HUBS, fontsize=9.5)
    ax.set_ylim(-0.7, 2.7)
    xs = [d["x_200"] for d in data.values()] + [d["x_worst_decade"] for d in data.values()]
    ax.set_xlim(min(xs) - 2, max(xs) + 14)
    ax.set_xlabel("Expected one-way minutes to the hub, including retries", color=MUTED, fontsize=9)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    style(ax, "C  Delivery loss turned into delay",
          "Light time + expected retry waits (R_h per lost launch); lower is better")

    # D: blockages past the 30-day packet lifetime
    ax = axes[1, 1]
    over = [int((data[h]["runs"] > 30).sum()) for h in HUBS]
    bars = ax.bar(HUBS, over, 0.55, color=[COLOR[h] for h in HUBS])
    for b, v, h in zip(bars, over, HUBS):
        r = data[h]["runs"]
        ax.text(b.get_x() + b.get_width() / 2, v + 2, f"{v} of {len(r)}", ha="center", fontsize=9.5, color=INK)
        ax.text(b.get_x() + b.get_width() / 2, -9, f"longest {r.max()} d", ha="center", fontsize=8, color=MUTED)
    ax.set_ylim(-14, max(over) * 1.25 + 5)
    ax.axhline(0, color="#b8b7b1", lw=0.8)
    ax.set_ylabel("Blockages longer than 30 days", color=MUTED, fontsize=9)
    style(ax, "D  Blockages that outlast the packet lifetime",
          "Packets queued on the blocked relay expire; sessions must be re-routed (quota cost)")

    fig.text(0.07, 0.015, "Source: network/python/hub_tradeoff.py on Data/orbital_elements.json (30-day samples for "
             "delivery and time; daily Sun test for blockages).", fontsize=8, color=MUTED)
    fig.savefig(args.out, dpi=150, facecolor=fig.get_facecolor())
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
