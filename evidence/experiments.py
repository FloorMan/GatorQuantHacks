"""Design-choice experiments: re-run the real engine with each alternative value.

Every number comes from mpex scenarios on the network model (the same driver as
the 17 tests), run with one rule or parameter swapped at a time.

    python3 -m evidence.experiments            # print JSON
    python3 -m evidence.experiments --md PATH  # also write the design-choices notes
"""

import argparse
import json
from decimal import Decimal

import mpex.batch as batch_mod
from mpex import CommunicationRiskMarginPolicy
from mpex.batch import BatchOrder, BatchOrderStatus, run_auction
from mpex.trading import Side

from . import cases, scenarios
from .scenarios import OPERATORS, Run, risk_windows
from .world import SEC_H

D = Decimal


# ------------------------------------------------------------------ priority rule

def priority_rule():
    """Equal-price bids: a remote bid stamped 1 minute earlier than Earth's."""
    rows = []
    original = batch_mod._priority_key
    rules = {
        "source time (chosen)": original,
        "arrival time": lambda o: (-o.limit_price if o.side is Side.BUY else o.limit_price, o.arrived_h),
    }
    for remote, acct in (("Mars", "M-Carla"), ("Ceres", "C-Finn"), ("Neptune", "N-Eve")):
        seller = "M-Diego" if remote != "Mars" else "C-Finn"
        row = {"remote": remote}
        for name, key in rules.items():
            batch_mod._priority_key = key
            try:
                r = Run("exp", "exp")
                r.open_batch(9.5)
                r.submit(10.0, acct, "buy", 10, 100, "remote")
                r.submit(10.0 + 1 / 60, "E-Bob", "buy", 10, 100, "earth")
                r.submit(10.0, seller, "sell", 10, 100, "sell")
                r.w.run(until=90)
                bo = r.ex.state.batch_orders
                rem, ear = bo[r.orders["remote"]], bo[r.orders["earth"]]
                row["arrival_gap_h"] = rem.arrived_h - ear.arrived_h
                row[name] = acct if rem.filled > 0 else "E-Bob"
            finally:
                batch_mod._priority_key = original
        rows.append(row)
    return rows


# ------------------------------------------------------------------ price rule

def price_rule():
    """Who keeps the surplus when a $45 sell meets a $50 buy (100 shares)."""
    o = [BatchOrder("S", "E-Alice", "ARES", Side.SELL, D(100), D(45), "Earth", 1.0, "e1"),
         BatchOrder("B", "N-Eve", "ARES", Side.BUY, D(100), D(50), "Neptune", 1.0, "e2")]
    engine = run_auction("BAT-X", o)["price"]
    out = []
    for name, p in (("seller's limit", D(45)), ("midpoint (chosen)", engine), ("buyer's limit", D(50))):
        out.append({"rule": name, "price": str(p), "buyer_saves": str((D(50) - p) * 100),
                    "seller_gains": str((p - D(45)) * 100)})
    return out


# ------------------------------------------------------------------ batch grace

def batch_grace():
    """Grace for k hop retries vs an order sent at batch open that loses L launches."""
    rows = []
    saved = scenarios.BATCH_HOP_RETRIES
    try:
        for k in (0, 1, 2, 3):
            scenarios.BATCH_HOP_RETRIES = k
            dur = Run("exp", "exp").batch_timing(0.5)["duration_h"]
            row = {"retries_covered": k, "batch_length_h": dur, "survives": {}}
            for lost in (0, 1, 2, 3):
                r = Run("exp", "exp")
                r.w.drop = (lambda L: lambda i: "forced" if i["meta"].get("kind") == "batch_order"
                            and i["from"] == "Neptune" and i["attempt"] <= L else None)(lost)
                r.open_batch(0.5)
                r.submit(0.5, "N-Eve", "buy", 10, 100, "eve")
                r.submit(1.0, "E-Alice", "sell", 10, 95, "alice")
                r.w.run(until=0.5 + dur + 1)
                eve = r.ex.state.batch_orders[r.orders["eve"]]
                row["survives"][lost] = eve.status is not BatchOrderStatus.ROLLED and eve.arrived_h is not None \
                    and eve.arrived_h <= r.ex.state.batches[r.batch_ids[0]].closes_h
            # No-loss Neptune order sent 30 min after the batch opens: does it make it?
            late = Run("exp", "exp")
            late.open_batch(0.5)
            late.submit(1.0, "N-Eve", "buy", 10, 100, "eve")
            late.w.run(until=0.5 + dur + 1)
            o = late.ex.state.batch_orders[late.orders["eve"]]
            row["no_loss_sent_30min_late_in_batch"] = o.status is not BatchOrderStatus.ROLLED and \
                o.arrived_h is not None and o.arrived_h <= late.ex.state.batches[late.batch_ids[0]].closes_h
            # Cross-planet trade sent at batch open: order stamp -> both legs usable.
            c = Run("exp", "exp")
            c.open_batch(0.5)
            c.submit(0.5, "E-Alice", "sell", 100, 45, "sell")
            c.submit(0.5, "N-Eve", "buy", 100, 50, "buy")
            c.w.run(until=200)
            t = next(iter(c.ex.state.trades.values()), None)
            row["cross_trade_complete_h"] = (t.settled_h - 0.5) if t and t.settled_h else None
            rows.append(row)
    finally:
        scenarios.BATCH_HOP_RETRIES = saved
    return rows


# ------------------------------------------------------------------ margin

def _with_policy(policy, fn):
    saved = scenarios.MARGIN_POLICY
    scenarios.MARGIN_POLICY = policy
    cases.MARGIN_POLICY = policy
    try:
        return fn()
    finally:
        scenarios.MARGIN_POLICY = saved
        cases.MARGIN_POLICY = saved


def _futures_metrics(path, respond):
    r, im_eve, im_alice = cases._futures_run("exp", "exp", path, respond=respond, until=320)
    pos = r.ex.state.positions[r.extra["position"]]
    calls = r.extra.get("calls", [])
    buffers = [D(row["long"]["posted"]) + D(row["long"]["pnl"]) for row in r.extra["series"]
               if row["event"] == "marked"]
    quota = sum(1 for p in r.ex.state.packets.values() if p.created_h >= 0 and p.counts_against_quota
                and p.message_id and r.ex.state.messages[p.message_id].service.value == "backbone")
    return {"im_neptune": str(im_eve), "im_earth": str(im_alice), "calls": len(calls),
            "calls_met": sum(1 for c in calls if c["met_h"] is not None and c["met_h"] <= c["deadline_h"]),
            "state": pos.state.value, "paid": str(pos.paid), "fund_used": str(pos.guarantee_used),
            "shortfall": str(pos.shortfall), "min_buffer": str(min(buffers)) if buffers else None,
            "backbone_packets": quota,
            "peak_cash_locked": str(r.ex.snapshot()["metrics"]["peak_encumbered"].get("NEO"))}


def margin_move():
    rows = []
    for move in ("0.03", "0.05", "0.08"):
        pol = CommunicationRiskMarginPolicy(move_per_observation=D(move), risk_window_h=risk_windows())
        rows.append({"move": move,
                     "falling": _with_policy(pol, lambda: _futures_metrics(cases.FALLING, True)),
                     "crash": _with_policy(pol, lambda: _futures_metrics(cases.CRASH, False))})
    return rows


def maintenance_fraction():
    rows = []
    for frac in ("0.5", "0.75", "1.0"):
        pol = CommunicationRiskMarginPolicy(move_per_observation=D("0.05"), maintenance_fraction=D(frac),
                                            risk_window_h=risk_windows())
        rows.append({"fraction": frac,
                     "falling": _with_policy(pol, lambda: _futures_metrics(cases.FALLING, True)),
                     "crash": _with_policy(pol, lambda: _futures_metrics(cases.CRASH, False))})
    return rows


def flat_margin():
    """Location-blind rule: every window 0 h (same 2-observation, 10% margin and a 0 h
    deadline for everyone), on both paths. On the falling path N-Eve pays every call."""
    pol = CommunicationRiskMarginPolicy(move_per_observation=D("0.10"),
                                        risk_window_h=tuple((a, 0.0) for a, _ in risk_windows()))

    def paying():
        r, im, _ = cases._futures_run("exp", "exp", cases.FALLING, respond=True, until=320)
        pos = r.ex.state.positions[r.extra["position"]]
        c = r.extra["calls"][0] if r.extra.get("calls") else None
        return {"im_neptune": str(im), "state": pos.state.value, "final_price": str(pos.final_price),
                "first_call_h": c and c["issued_h"], "deadline_h": c and c["deadline_h"],
                "top_up_landed_h": c and c["met_h"], "paid": str(pos.paid)}
    return {"falling_paying": _with_policy(pol, paying),
            "crash": _with_policy(pol, lambda: _futures_metrics(cases.CRASH, False)),
            "ours_falling": _futures_metrics(cases.FALLING, True)}


def hop_loss():
    """Per-launch loss on each hop of every remote settlement's route to Earth at h 0.5."""
    r = Run("exp", "exp")
    out = []
    for s in ("Mars", "Ceres", "Neptune"):
        route = r.w.route_from(OPERATORS[s], OPERATORS["Earth"])
        hops = r.w.net.evaluate_route(route, 0.5 / 24)["hops"]
        worst = max(hops, key=lambda h: h["loss"])
        p = worst["loss"]
        out.append({"from": s, "route": route, "worst_hop": f"{worst['from']} -> {worst['to']}",
                    "p_launch_lost": p,
                    "p_needs_more_than": {k: p ** (k + 1) for k in (0, 1, 2, 3)}})
    return out


# ------------------------------------------------------------------ settlement path

def settlement_path():
    """Mars seller -> Neptune buyer: direct operator session vs relaying the leg through Earth."""
    r = Run("exp", "exp")
    w = r.w
    out = []
    for src, dst in (("Mars", "Neptune"), ("Ceres", "Neptune"), ("Mars", "Ceres")):
        direct = w.route_timing_h(w.route_from(OPERATORS[src], OPERATORS[dst]), 0.0)
        via = (w.route_timing_h(w.route_from(OPERATORS[src], OPERATORS["Earth"]), 0.0)
               + w.route_timing_h(w.route_from(OPERATORS["Earth"], OPERATORS[dst]), 0.0) + SEC_H)
        out.append({"leg": f"{src} -> {dst}", "direct_h": direct, "via_earth_h": via,
                    "direct_quota_packets": 1, "via_earth_quota_packets": 2})
    return out


def run_all():
    return {"priority": priority_rule(), "price": price_rule(), "grace": batch_grace(),
            "margin_move": margin_move(), "maintenance": maintenance_fraction(),
            "flat_margin": flat_margin(), "settlement_path": settlement_path(),
            "hop_loss": hop_loss(),
            "windows": dict(risk_windows())}


def _default(o):
    return str(o)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", help="write the design-choices notes to this path")
    args = ap.parse_args()
    res = run_all()
    if args.md:
        from .design_notes import render
        with open(args.md, "w") as f:
            f.write(render(res))
        print(f"wrote {args.md}")
    print(json.dumps(res, default=_default, indent=1))
