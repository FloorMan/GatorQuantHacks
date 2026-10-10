"""What-if search for relay starting angles (an extension: the brief fixes them at 45 and 135 deg).

Both relays stay on the brief's circle of radius sqrt(8) AU with its period; only
their starting angles change. For a pair of angles (a, b) the objective is

    J(a, b) = mean over time t and the 9 settlements p of  min(d(p, A)^2, d(p, B)^2)

i.e. each settlement uses its nearer relay, and planets and relays both move.
Geometry only: it ignores solar blockage and maintenance.

The search evaluates every pair on a coarse grid, then refines around the best pair.

Usage:
  python3 network/python/relay_placement.py                  # 200 Julian years, 10-day steps
  python3 network/python/relay_placement.py --hours 720      # first 720 h (a scenario window), 1 h steps
"""
import argparse
import math

import numpy as np

from network_graph import RELAY_PERIOD, RELAY_RADIUS, SETTLEMENTS, System

BRIEF_ANGLES = (45.0, 135.0)


def planet_positions(times_days):
    s = System()
    return np.array([[s.position(p, t)[:3] for p in SETTLEMENTS] for t in times_days])  # (T, 9, 3)


def dist2_table(P, times_days, angles_deg):
    """d^2 from every settlement to a relay starting at each angle: shape (angles, T, 9)."""
    r = RELAY_RADIUS
    th = np.radians(angles_deg)[:, None] + 2 * np.pi * np.asarray(times_days)[None, :] / RELAY_PERIOD
    c, s = np.cos(th)[:, :, None], np.sin(th)[:, :, None]
    p2 = (P ** 2).sum(axis=2)[None, :, :]
    return p2 + r * r - 2 * r * (P[None, :, :, 0] * c + P[None, :, :, 1] * s)


def objective(da, db):
    return float(np.minimum(da, db).mean())


def search(P, times, step):
    angles = np.arange(0, 360, step)
    D = dist2_table(P, times, angles).astype(np.float32)
    best = (math.inf, None, None)
    for i in range(len(angles)):
        # J for (angles[i], every angles[j] with j > i) in one vectorized pass
        vals = np.minimum(D[i][None], D[i + 1:]).mean(axis=(1, 2))
        if len(vals):
            j = int(vals.argmin())
            if vals[j] < best[0]:
                best = (float(vals[j]), angles[i], angles[i + 1 + j])
    return best


def refine(P, times, a, b, span, step):
    grid = np.arange(-span, span + step / 2, step)
    Da = dist2_table(P, times, (a + grid) % 360)
    Db = dist2_table(P, times, (b + grid) % 360)
    best = (math.inf, a, b)
    for i, ga in enumerate(grid):
        vals = np.minimum(Da[i][None], Db).mean(axis=(1, 2))
        j = int(vals.argmin())
        if vals[j] < best[0]:
            best = (float(vals[j]), (a + ga) % 360, (b + grid[j]) % 360)
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", type=float, default=200.0, help="horizon in Julian years (default 200)")
    ap.add_argument("--hours", type=float, help="use a short horizon in hours instead (1 h steps)")
    ap.add_argument("--step-days", type=float, default=10.0, help="time step for --years (default 10)")
    ap.add_argument("--grid", type=float, default=2.0, help="coarse angle grid in degrees (default 2)")
    args = ap.parse_args()

    if args.hours:
        times = np.arange(0, args.hours + 1e-9, 1.0) / 24
        label = f"hours 0-{args.hours:g}"
    else:
        times = np.arange(0, args.years * 365.25 + 1e-9, args.step_days)
        label = f"{args.years:g} Julian years, {args.step_days:g}-day steps"
    P = planet_positions(times)
    print(f"Horizon: {label} ({len(times)} samples x 9 settlements)")

    j0, a0, b0 = search(P, times, args.grid)
    j, a, b = refine(P, times, a0, b0, args.grid, 0.25)
    jb = objective(*dist2_table(P, times, list(BRIEF_ANGLES)))

    sep = abs(a - b) % 360
    sep = min(sep, 360 - sep)
    print(f"\nBrief placement   A {BRIEF_ANGLES[0]:6.2f} deg  B {BRIEF_ANGLES[1]:6.2f} deg   "
          f"mean min d^2 = {jb:9.4f} AU^2")
    print(f"Best found        A {a:6.2f} deg  B {b:6.2f} deg   mean min d^2 = {j:9.4f} AU^2   "
          f"(separation {sep:.2f} deg)")
    print(f"Improvement       {100 * (jb - j) / jb:.2f}% lower than the brief's placement")

    # Per-settlement breakdown: mean distance to the nearer relay.
    Db = np.sqrt(np.minimum(*dist2_table(P, times, list(BRIEF_ANGLES))))
    Do = np.sqrt(np.minimum(*dist2_table(P, times, [a, b])))
    print(f"\n{'settlement':<10}{'brief mean d':>14}{'best mean d':>13}   (AU, to the nearer relay)")
    for k, name in enumerate(SETTLEMENTS):
        print(f"{name:<10}{Db[:, k].mean():>14.4f}{Do[:, k].mean():>13.4f}")


if __name__ == "__main__":
    main()
