"""Compare candidate clearing-hub settlements (see notes/clearing-hub-location.md).

1. Delay: best open backbone route from every other settlement to the hub, sampled
   over the scenario window and over 200 Julian years.
2. Reliability: over 200 years at daily steps, how often the hub has one or both
   relay links Sun-blocked, and the longest spell. This scan tests the Sun at one
   instant (both ends at the same t), so its spell boundaries are approximate.

Usage:
  python3 network/python/hub_compare.py                     # Earth, Mars, Ceres
  python3 network/python/hub_compare.py --hubs Earth Jupiter
"""
import argparse

import numpy as np

import network_graph as n


def delay_table(s, hubs, times_h, label):
    print(f"\n== Delay: {label} ({len(times_h)} samples) ==")
    print(f"{'hub':<8}{'mean 1-way':>11}{'mean RT':>10}{'worst 1-way':>12}{'no route':>10}   worst-served (mean 1-way)")
    for hub in hubs:
        others = [x for x in n.SETTLEMENTS if x != hub]
        D = np.full((len(times_h), len(others)), np.nan)
        R = D.copy()
        for i, h in enumerate(times_h):
            for k, o in enumerate(others):
                r = s.routes(o, hub, h / 24)[0]
                if r["open"]:
                    D[i, k] = r["light_min"]
                    back = s.routes(hub, o, h / 24)[0]
                    if back["open"]:
                        R[i, k] = r["light_min"] + back["light_min"]
        per = np.nanmean(D, axis=0)
        w = int(np.nanargmax(per))
        print(f"{hub:<8}{np.nanmean(D):>9.1f} m{np.nanmean(R) / 60:>8.2f} h{np.nanmax(D) / 60:>10.2f} h"
              f"{np.isnan(D).mean() * 100:>9.2f}%   {others[w]} {per[w] / 60:.2f} h")


def blockage_table(s, hubs, years=200):
    days = int(years * 365.25)
    print(f"\n== Reliability: {years} years, daily steps, Sun test at one instant ==")
    print(f"{'hub':<8}{'one relay blocked':>20}{'both blocked':>14}{'longest spell':>15}")
    for hub in hubs:
        blk = {r: [n.segment_origin_distance(s.position(hub, d), s.position(r, d)) < s.sun_exclusion
                   for d in range(days)] for r in n.RELAYS}
        a, b = blk["Relay A"], blk["Relay B"]
        one = sum(x != y for x, y in zip(a, b))
        both = sum(x and y for x, y in zip(a, b))
        longest = cur = 0
        for x, y in zip(a, b):
            cur = cur + 1 if (x or y) else 0
            longest = max(longest, cur)
        print(f"{hub:<8}{one:>9d} d ({one / days * 100:4.1f}%){both:>12d} d{longest:>13d} d")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hubs", nargs="+", default=["Earth", "Mars", "Ceres"], choices=n.SETTLEMENTS)
    args = ap.parse_args()
    s = n.System()
    delay_table(s, args.hubs, np.arange(0, 721, 4.0), "hours 0-720, 4 h steps")
    delay_table(s, args.hubs, np.arange(0, 200 * 365.25 * 24, 30 * 24.0), "200 years, 30-day steps")
    blockage_table(s, args.hubs)


if __name__ == "__main__":
    main()
