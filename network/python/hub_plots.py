"""Figure: why the clearing house sits at Earth rather than Mars or Ceres.

Four panels, all from the network model (brief's launch rules for delay; daily
instant-time Sun test for blockage spells):

  A  mean one-way delay to the hub over 200 years (30-day steps)
  B  mean one-way delay from each settlement to each hub
  C  every period in which the hub loses a relay to the Sun, by length,
     against the 7-day session expiry and 30-day packet lifetime
  D  share of the 200 years the hub has only one relay

Usage (from the repo root):
  python3 network/python/hub_plots.py                 # writes notes/hub-comparison.png
  python3 network/python/hub_plots.py --out file.png
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import network_graph as n

HUBS = ["Earth", "Mars", "Ceres"]
# Categorical slots 1-3 of the validated reference palette (light mode), fixed order.
COLOR = {"Earth": "#2a78d6", "Mars": "#eb6834", "Ceres": "#1baf7a"}
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
YEARS = 200


def delays(s, hub, times_h):
    others = [x for x in n.SETTLEMENTS if x != hub]
    D = np.full((len(times_h), len(others)), np.nan)
    for i, h in enumerate(times_h):
        for k, o in enumerate(others):
            r = s.routes(o, hub, h / 24)[0]
            if r["open"]:
                D[i, k] = r["light_min"]
    return others, D


def spells(s, hub, days):
    blocked = {r: np.array([n.segment_origin_distance(s.position(hub, d), s.position(r, d)) < s.sun_exclusion
                            for d in range(days)]) for r in n.RELAYS}
    any_b = blocked["Relay A"] | blocked["Relay B"]
    both = int((blocked["Relay A"] & blocked["Relay B"]).sum())
    runs, cur = [], 0
    for b in any_b:
        if b:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    return runs, float(any_b.mean()), both


def style(ax, title, sub):
    ax.set_title(title, loc="left", fontsize=11.5, fontweight="bold", color=INK, pad=22)
    ax.text(0, 1.02, sub, transform=ax.transAxes, fontsize=8.5, color=MUTED, va="bottom")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#b8b7b1")
    ax.tick_params(colors=MUTED, labelsize=8.5)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "notes" / "hub-comparison.png"))
    args = ap.parse_args()
    s = n.System()
    times = np.arange(0, YEARS * 365.25 * 24, 30 * 24.0)
    days = int(YEARS * 365.25)

    data = {}
    for hub in HUBS:
        others, D = delays(s, hub, times)
        runs, frac, both = spells(s, hub, days)
        data[hub] = {"others": others, "D": D, "runs": runs, "frac": frac, "both": both}
        print(f"{hub}: mean {np.nanmean(D):.1f} min, longest spell {max(runs)} d, "
              f"one relay {frac * 100:.1f}%, both blocked {both} d")

    fig, axes = plt.subplots(2, 2, figsize=(14, 10), facecolor="#fcfcfb")
    fig.subplots_adjust(hspace=0.55, wspace=0.28, top=0.86, bottom=0.08, left=0.07, right=0.97)
    fig.suptitle("Clearing house: Earth vs Mars vs Ceres", x=0.07, ha="left", fontsize=16,
                 fontweight="bold", color=INK)
    fig.text(0.07, 0.915, "Delay is a near tie. Blockage length decides it: only Earth's longest outage of a "
             "relay stays under the 30-day packet lifetime.", fontsize=11, color=MUTED)

    # A: delay over time (one axis: minutes)
    ax = axes[0, 0]
    years = times / 24 / 365.25
    for hub in reversed(HUBS):  # Earth drawn last so it stays visible
        series = np.nanmean(data[hub]["D"], axis=1)
        k = 12  # ~1-year rolling mean so the lines are readable
        smooth = np.convolve(series, np.ones(k) / k, mode="valid")
        ax.plot(years[k - 1:], smooth, color=COLOR[hub], lw=2, label=hub, zorder=3 if hub == "Earth" else 2)
        ax.text(years[-1] + 2, smooth[-1], f"{hub} {np.nanmean(data[hub]['D']):.1f}", color=INK,
                fontsize=8.5, va="center")
    ax.set_xlim(0, YEARS + 28)
    ax.set_xlabel("Years from epoch", color=MUTED, fontsize=9)
    ax.set_ylabel("Minutes, one way", color=MUTED, fontsize=9)
    h, l = ax.get_legend_handles_labels()
    ax.legend(h[::-1], l[::-1], frameon=False, fontsize=8.5, loc="upper left", ncol=3)
    style(ax, "A  Mean delay to the hub over 200 years",
          "Average best-route one-way delay from the other 8 settlements (1-year rolling mean; label = 200-year mean)")

    # B: per-settlement delay (grouped bars, one axis: minutes)
    ax = axes[0, 1]
    settlements = [x for x in n.SETTLEMENTS if x not in HUBS] + HUBS
    x = np.arange(len(settlements))
    wdt = 0.26
    for i, hub in enumerate(HUBS):
        vals = []
        for st in settlements:
            if st == hub:
                vals.append(0)
            else:
                vals.append(np.nanmean(data[hub]["D"][:, data[hub]["others"].index(st)]))
        bars = ax.bar(x + (i - 1) * wdt, vals, wdt - 0.03, color=COLOR[hub], label=hub)
        for b, v in zip(bars, vals):
            b.set_capstyle("round")
    ax.set_xticks(x, settlements, fontsize=8.5)
    ax.set_ylabel("Minutes, one way (200-year mean)", color=MUTED, fontsize=9)
    ax.legend(frameon=False, fontsize=8.5, loc="upper left", ncol=3)
    nep = {h: np.nanmean(data[h]["D"][:, data[h]["others"].index("Neptune")]) for h in HUBS}
    ax.set_ylim(0, max(nep.values()) * 1.18)
    ax.text(settlements.index("Neptune") + 0.45, max(nep.values()) * 0.8,
            f"Neptune ≈ {np.mean(list(nep.values())) / 60:.1f} h\nto every hub", fontsize=8.5, color=INK, va="center")
    style(ax, "B  Delay from each settlement",
          "Distant settlements dominate for every hub; a hub's own bar is 0 (local access)")

    # C: blockage spells by length (dot strip, log days) with the brief's timers
    ax = axes[1, 0]
    rng = np.random.default_rng(0)
    for i, hub in enumerate(HUBS):
        runs = np.array(data[hub]["runs"])
        y = i + rng.uniform(-0.18, 0.18, len(runs))
        ax.scatter(runs, y, s=18, color=COLOR[hub], alpha=0.75, edgecolors="#fcfcfb", linewidths=0.6)
        over = int((runs > 30).sum())
        ax.text(max(runs) * 1.15, i, f"longest {max(runs)} d\n{over} of {len(runs)} periods over 30 d",
                va="center", fontsize=8.5, color=INK)
    for d, lab, ha, dx in ((7, "7-day session expiry", "right", 0.96), (30, "30-day packet lifetime", "left", 1.04)):
        ax.axvline(d, color=INK, lw=1, ls=(0, (4, 3)))
        ax.text(d * dx, 2.55, lab, fontsize=8, color=INK, va="bottom", ha=ha)
    ax.set_xscale("log")
    ax.set_xlim(0.8, 2500)
    ax.set_ylim(-0.6, 2.9)
    ax.set_yticks(range(len(HUBS)), HUBS, fontsize=9.5)
    ax.set_xlabel("Days the hub has only one relay (log scale)", color=MUTED, fontsize=9)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    style(ax, "C  Every period the Sun cuts the hub off from a relay",
          "One dot per period over 200 years. Past 30 days, queued packets expire on the blocked relay")

    # D: share of time on one relay (bars, one axis: %)
    ax = axes[1, 1]
    vals = [data[h]["frac"] * 100 for h in HUBS]
    bars = ax.bar(HUBS, vals, 0.55, color=[COLOR[h] for h in HUBS])
    for b, v, h in zip(bars, vals, HUBS):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.15, f"{v:.1f}%", ha="center", fontsize=9.5, color=INK)
        ax.text(b.get_x() + b.get_width() / 2, -0.9, f"both relays lost: {data[h]['both']} d",
                ha="center", fontsize=8, color=MUTED)
    ax.set_ylim(-1.4, max(vals) * 1.25)
    ax.axhline(0, color="#b8b7b1", lw=0.8)
    ax.set_ylabel("% of 200 years with one relay blocked", color=MUTED, fontsize=9)
    style(ax, "D  How often the hub runs on one relay",
          "Ceres is cut off least often, but when it is, it lasts months (panel C)")

    fig.text(0.07, 0.015, "Source: network/python/hub_plots.py on Data/orbital_elements.json. Delay uses the brief's launch "
             "rules (moving receiver, Sun exclusion, maintenance); blockage uses a daily instant-time Sun test.",
             fontsize=8, color=MUTED)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=150, facecolor=fig.get_facecolor())
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
