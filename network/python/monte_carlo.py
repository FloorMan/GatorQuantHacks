"""Monte Carlo study of duplicate packets and retry latency.

A packet follows the fastest open backbone route selected at departure. Each hop
sends one copy when its distance is below ``--threshold`` and two simultaneous
copies otherwise. Copies have independent loss outcomes and the hop advances
when the first copy arrives. If every copy is lost, the sender waits for a
round-trip timeout before retrying the hop.

Examples (from the repository root)::

  python network/python/monte_carlo.py --from Earth --to Mars
  python network/python/monte_carlo.py --from Earth --to Mars --samples 50000 \
      --threshold-max 8 --threshold-step 0.25

The threshold is inclusive: a link at exactly the threshold sends two copies.
"""
import argparse
import math
import random
from dataclasses import dataclass

from network_graph import RELAYS, SEC, SETTLEMENTS, System


@dataclass
class PacketResult:
    delivered: bool
    elapsed_days: float
    launches: int
    copies: int


def send_packet(system, path, departure, threshold, rng, max_attempts=20):
    """Simulate one packet on a fixed route and return latency and launch counts."""
    ready = departure
    launches = 0
    copies_sent = 0

    for sender, receiver in zip(path, path[1:]):
        for _ in range(max_attempts):
            hop = system.launch(sender, receiver, ready + SEC)
            copies = 2 if hop["distance_au"] >= threshold else 1
            launches += copies
            copies_sent += copies
            # A blocked hop cannot deliver, while an open hop uses independent loss.
            success = hop["status"] == "open" and any(
                rng.random() >= hop["loss"] for _ in range(copies)
            )
            if success:
                ready = hop["ta"] + (SEC if receiver in RELAYS else 0)
                break

            # No acknowledgment is modeled by the base network model. Use a
            # round-trip light-time timeout before attempting this hop again.
            ready = hop["ta"] + (hop["ta"] - hop["te"]) + SEC
        else:
            return PacketResult(False, ready - departure, launches, copies_sent)

    return PacketResult(True, ready - departure, launches, copies_sent)


def choose_route(system, src, dst, departure, weight="light_min"):
    """Return the fastest currently open route, or None if all routes are shut."""
    routes = system.routes(src, dst, departure, weight)
    return routes[0]["path"] if routes and routes[0]["open"] else None


def run_simulation(system, src, dst, samples, threshold, horizon_days, seed,
                   max_attempts=20):
    """Run paired random trials and return aggregate metrics for one threshold."""
    master = random.Random(seed)
    delivered_times = []
    total_launches = 0
    total_copies = 0
    unavailable = 0

    for _ in range(samples):
        departure = master.random() * horizon_days
        path = choose_route(system, src, dst, departure)
        if path is None:
            unavailable += 1
            continue
        result = send_packet(system, path, departure, threshold, master, max_attempts)
        total_launches += result.launches
        total_copies += result.copies
        if result.delivered:
            delivered_times.append(result.elapsed_days * 24 * 60)

    attempted = samples - unavailable
    delivered = len(delivered_times)
    return {
        "threshold_au": threshold,
        "samples": samples,
        "attempted": attempted,
        "delivered": delivered,
        "delivery_rate": delivered / attempted if attempted else math.nan,
        "mean_min": sum(delivered_times) / delivered if delivered else math.nan,
        "p95_min": percentile(delivered_times, 0.95),
        "mean_launches": total_launches / attempted if attempted else math.nan,
        "mean_copies": total_copies / attempted if attempted else math.nan,
        "unavailable": unavailable,
    }


def percentile(values, fraction):
    if not values:
        return math.nan
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def thresholds(start, stop, step):
    count = int(math.floor((stop - start) / step + 1e-9))
    return [start + i * step for i in range(count + 1)]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="src", choices=SETTLEMENTS, default="Earth")
    parser.add_argument("--to", dest="dst", choices=SETTLEMENTS, default="Mars")
    parser.add_argument("--samples", type=int, default=10000,
                        help="Monte Carlo packets per threshold (default 10000)")
    parser.add_argument("--horizon-hours", type=float, default=24 * 365.25,
                        help="uniform departure-time horizon (default one Julian year)")
    parser.add_argument("--threshold-min", type=float, default=0.0,
                        help="first duplicate threshold in AU (default 0)")
    parser.add_argument("--threshold-max", type=float, default=10.0,
                        help="last duplicate threshold in AU (default 10)")
    parser.add_argument("--threshold-step", type=float, default=0.5,
                        help="threshold increment in AU (default 0.5)")
    parser.add_argument("--seed", type=int, default=20261003)
    parser.add_argument("--max-attempts", type=int, default=20,
                        help="retry limit per hop (default 20)")
    args = parser.parse_args()

    if args.src == args.dst:
        parser.error("--from and --to must be different settlements")
    if args.samples < 1 or args.max_attempts < 1:
        parser.error("--samples and --max-attempts must be positive")
    if args.threshold_min < 0 or args.threshold_max < args.threshold_min or args.threshold_step <= 0:
        parser.error("threshold range must be nonnegative and have a positive step")

    system = System()
    horizon_days = args.horizon_hours / 24
    print(f"{args.src} -> {args.dst}, {args.samples:,} samples, "
          f"departures uniform over 0-{args.horizon_hours:g} h, seed {args.seed}")
    print("Two copies are sent for distance >= threshold; failed hops wait one RTT before retrying.")
    print(f"{'threshold AU':>13} {'mean min':>12} {'p95 min':>12} {'delivery':>10} "
          f"{'launches/packet':>16} {'copies/packet':>15} {'unavailable':>12}")

    for threshold in thresholds(args.threshold_min, args.threshold_max, args.threshold_step):
        result = run_simulation(system, args.src, args.dst, args.samples, threshold,
                                horizon_days, args.seed, args.max_attempts)
        print(f"{result['threshold_au']:13.3f} {result['mean_min']:12.3f} "
              f"{result['p95_min']:12.3f} {result['delivery_rate'] * 100:9.3f}% "
              f"{result['mean_launches']:16.3f} {result['mean_copies']:15.3f} "
              f"{result['unavailable']:12d}")


if __name__ == "__main__":
    main()
