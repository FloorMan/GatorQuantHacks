"""Run the scenarios and turn each finished run into evidence JSON for the dashboard.

Everything here is read from the run itself (journal, ledger, launch log); nothing
is hard-coded. A scenario passes only if every check passes and no invariant was
violated after any step.

    python3 -m evidence.runner                # run all, print a summary
    python3 -m evidence.runner cross-planet   # one scenario, print its JSON
"""

import json
import sys
import time
import traceback
from collections import Counter
from decimal import Decimal

from mpex.journal import EventType

from .cases import CASES
from .scenarios import CASH, FUTURE, HOME, SHARES, balances, MARGIN_POLICY

BY_ID = {cid: (title, fn) for cid, title, fn in CASES}


# ------------------------------------------------------------------ timeline

def _amount(asset, amount):
    return f"${Decimal(amount):,.2f}" if asset == CASH else f"{Decimal(amount):,.0f} shares"


def describe(ex, e):
    """(label, detail, category) for one journal event."""
    d, T = e.data, EventType
    st = ex.state
    if e.type is T.PACKET_LAUNCHED:
        p = st.packets[d["packet_id"]]
        msg = st.messages.get(p.message_id or "")
        what = msg.kind if msg and p.kind.value == "data" else p.kind.value.replace("_", " ")
        lost = " · LOST" if d["lost"] else ""
        label = ("PACKET SENT" if d["attempt"] == 1 else f"RETRY #{d['attempt']}") + lost
        cat = "network" if p.kind.value == "data" else "transport"
        return label, f"{what}: {d['from_node']} → {d['to_node']}, lands h {d['arrival_h']:.4f}", cat
    if e.type is T.MESSAGE_SENT:
        return ("MESSAGE SENT", f"{d['kind']}: {d['sender']} → {d['recipient']} ({d['service']})",
                "network" if d["service"] != "local" else "local")
    if e.type is T.MESSAGE_STATUS_CHANGED:
        m = st.messages[d["message_id"]]
        return (("RECEIVED" if d["status"] == "delivered" else d["status"].upper()),
                f"{m.kind} at {m.recipient}", "network")
    if e.type is T.TRANSPORT_PACKET_CREATED:
        return d["kind"].replace("_", " ").upper(), f"from {d['from_node']}", "transport"
    if e.type in (T.SESSION_OPENED, T.SESSION_STATE_CHANGED):
        return e.type.value.replace("_", " ").upper(), d.get("state", ""), "transport"
    if e.type is T.BATCH_ORDER_RESERVED:
        locked = (_amount(CASH, Decimal(d["quantity"]) * Decimal(d["limit_price"])) if d["side"] == "buy"
                  else _amount(SHARES, d["quantity"]))
        return ("ORDER STAMPED · RESERVED",
                f"{d['account']} {d['side'].upper()} {d['quantity']} @ {d['limit_price']}; {locked} locked at {d['home']}",
                "finance")
    if e.type is T.BATCH_OPENED:
        return "BATCH OPENED", f"{d['batch_id']} at {d['market']}, closes h {d['closes_h']:.3f}", "finance"
    if e.type is T.BATCH_ORDER_ACCEPTED:
        return "IN BATCH", f"{d['order_id']} joins {d['batch_id']}", "finance"
    if e.type is T.BATCH_ORDER_ROLLED:
        return "ROLLED TO NEXT BATCH", f"{d['order_id']} missed {d['batch_id']}; limit unchanged", "finance"
    if e.type is T.BATCH_ORDER_CANCELLED:
        return "CANCELLED AT MARKET", d["order_id"], "finance"
    if e.type is T.BATCH_CANCEL_REJECTED:
        return "CANCEL REJECTED", d["reason"], "finance"
    if e.type is T.BATCH_EXECUTED:
        n = len(d["trades"])
        return "BATCH EXECUTED", f"{d['batch_id']} @ {d['price']} · {n} trade(s)", "finance"
    if e.type is T.BATCH_LEG_SETTLED:
        parts = [f"{_amount(l['asset'], l['amount'])} → {l['to_owner']} "
                 f"({'transfer ' + l['transfer_id'] if l['transfer_id'] else 'local'})" for l in d["legs"]]
        if Decimal(d["released"]) > 0:
            parts.append(f"released {d['released']}")
        return "RESULT APPLIED AT HOME", f"{d['order_id']}: " + ("; ".join(parts) or "nothing to deliver"), "finance"
    if e.type is T.TRANSFER_INITIATED:
        return "TRANSFER INITIATED", (f"{d['transfer_id']}: {_amount(d['asset'], d['amount'])} "
                                      f"{d['from_settlement']} → {d['to_settlement']} (in transit)"), "finance"
    if e.type is T.TRANSFER_COMPLETED:
        t = st.transfers[d["transfer_id"]]
        return "VALIDATED · FINAL", f"{t.id}: usable by {t.to_owner} at {t.to_settlement}", "finance"
    if e.type is T.TRANSFER_DUPLICATE_IGNORED:
        return "DUPLICATE IGNORED", f"{d['transfer_id']} ({d['stage']}): same transaction id, no effect", "finance"
    if e.type is T.TRANSFER_CONFIRMED:
        return "SOURCE RECONCILED", f"{d['transfer_id']} confirmed at the source", "finance"
    if e.type is T.ORDER_ACCEPTED:
        return "ORDER ACCEPTED", f"{d['account']} {d['side'].upper()} {d['quantity']} @ {d['limit_price']} on {d['symbol']}", "finance"
    if e.type is T.TRADE_EXECUTED:
        return "TRADE · DvP", f"{d['quantity']} @ {d['price']} on {d['symbol']}", "finance"
    if e.type is T.ORDER_CANCELLED:
        return "ORDER CANCELLED", d["order_id"], "finance"
    if e.type is T.GUARANTEE_CONTRIBUTED:
        return "GUARANTEE CONTRIBUTION", f"{d['account']} → {d['clearing']}: ${Decimal(d['amount']):,.2f}", "finance"
    if e.type is T.POSITION_OPENED:
        return "FUTURE OPENED", (f"{d['symbol']} {d['quantity']} @ {d['entry_price']}: long {d['long']} "
                                 f"(margin {d['long_margin']}), short {d['short']} (margin {d['short_margin']})"), "finance"
    if e.type is T.MARGIN_POSTED:
        return "MARGIN POSTED", f"{d['party']} +{d['amount']}", "finance"
    if e.type is T.POSITION_MARKED:
        return "MARKED", f"{d['position_id']} at {st.observations[d['observation_id']].price}", "finance"
    if e.type is T.POSITION_SETTLED:
        return "SETTLED AT MATURITY", d["position_id"], "finance"
    if e.type is T.POSITION_DEFAULTED:
        return "FUNDED DEFAULT", f"{d['defaulter']} on {d['position_id']}", "finance"
    if e.type is T.OBSERVATION_RELEASED:
        return "MOI OBSERVATION", f"{d['price']} released at Mars", "finance"
    if e.type is T.NOTE:
        return "NOTE", e.note or "", "note"
    return e.type.value.upper(), json.dumps(d)[:120], "other"


def timeline(ex):
    rows = []
    for e in ex.journal:
        if e.time_h < 0:
            continue
        label, detail, cat = describe(ex, e)
        rows.append({"t": e.time_h, "actor": e.actor, "label": label, "detail": detail,
                     "category": cat, "note": e.note if e.type is not EventType.NOTE else None})
    return rows


# ------------------------------------------------------------------ network

def message_view(r, mid):
    m = r.w.messages[mid]
    ex = r.ex
    pkts = [p.id for p in ex.state.packets.values() if p.message_id == mid]
    launches = [l for l in r.w.launch_log if l["packet"] in pkts]
    first_ok = {}
    for l in launches:
        if not l["lost"]:
            first_ok.setdefault((l["from"], l["to"]), l)
    return {
        "id": mid, "kind": m["kind"], "sender": m["sender"], "recipient": m["recipient"],
        "route": m["route"], "sent_h": m["sent_h"], "delivered_h": m["delivered_h"],
        "endpoint_attempts": m["attempts"],
        "distance_au": sum(l["distance_au"] for l in first_ok.values()),
        "launches": launches,
        "retries": sum(1 for l in launches if l["attempt"] > 1) + (m["attempts"] - 1),
        "losses": sum(1 for l in launches if l["lost"]),
    }


def network_summary(r):
    ex, log = r.ex, [l for l in r.w.launch_log if l["te"] >= 0]
    pk = Counter(p.kind.value for p in ex.state.packets.values() if p.created_h >= 0)
    quota = sum(1 for p in ex.state.packets.values() if p.created_h >= 0 and p.counts_against_quota
                and (p.message_id is None or ex.state.messages[p.message_id].service.value == "backbone"))
    return {
        "launches": len(log), "lost": sum(l["lost"] for l in log),
        "hop_retries": sum(1 for l in log if l["attempt"] > 1),
        "loss_reasons": dict(Counter(l["loss_reason"] for l in log if l["lost"])),
        "packets_by_kind": dict(pk), "backbone_quota_packets": quota,
        "setup_quota_packets": sum(1 for p in ex.state.packets.values()
                                   if p.created_h < 0 and p.counts_against_quota),
        "messages": [message_view(r, mid) for mid, m in r.w.messages.items() if m["sent_h"] >= 0],
        "incidents": r.w.incidents,
    }


# ------------------------------------------------------------------ views

def primary_view(r):
    p = r.extra.get("primary") or {}
    ex = r.ex
    out = {"accounts": p.get("accounts", []), "steps": [], "transaction": None}
    if p.get("steps"):
        out["steps"] = [{"label": a, "t": b} for a, b in p["steps"]]
    xid = p.get("transfer")
    if not xid:
        return out
    t = ex.state.transfers[xid]
    mid = next((m for m in r.w.messages if xid in ex.state.messages[m].references
                and r.w.messages[m]["kind"] in ("transfer", "margin_transfer")), None)
    mv = message_view(r, mid) if mid else None
    steps = []
    order = None
    if t.leg_of:
        tr = ex.state.trades[t.leg_of]
        order = ex.state.batch_orders[tr.sell_order if t.asset == SHARES else tr.buy_order]
        steps.append({"label": "RESERVED", "t": order.source_h, "detail": f"{order.id} locks at {order.home}"})
        steps.append({"label": "BATCH EXECUTED", "t": tr.executed_h, "detail": f"{tr.quantity} @ {tr.price}"})
    steps.append({"label": "LOCKED → IN TRANSIT", "t": t.initiated_h, "detail": f"{xid} leaves {t.from_settlement}"})
    if mv:
        ok = [l for l in mv["launches"] if not l["lost"]]
        if mv["launches"]:
            steps.append({"label": "PACKET SENT", "t": mv["launches"][0]["te"],
                          "detail": f"{mv['route'][0]} → {mv['route'][1]}"})
        for l in mv["launches"]:
            if l["lost"]:
                steps.append({"label": f"LOST (launch #{l['attempt']})", "t": l["te"],
                              "detail": f"{l['from']} → {l['to']}: {l['loss_reason']}"})
            elif l["attempt"] > 1:
                steps.append({"label": f"RETRY #{l['attempt']}", "t": l["te"], "detail": f"{l['from']} → {l['to']}"})
        for node in mv["route"][1:-1]:
            arr = next((l["ta"] for l in ok if l["to"] == node), None)
            if arr is not None:
                steps.append({"label": node.upper(), "t": arr, "detail": "relay stores, receipts, forwards first copy"})
        steps.append({"label": mv["route"][-1].upper() + " RECEIVED", "t": mv["delivered_h"], "detail": "data delivered"})
    steps.append({"label": "VALIDATED · FINAL", "t": t.completed_h, "detail": f"usable by {t.to_owner} at {t.to_settlement}"})
    if t.source_confirmed_h is not None:
        steps.append({"label": "SOURCE RECONCILED", "t": t.source_confirmed_h, "detail": f"{t.from_settlement} confirms"})
    out["steps"] = steps
    out["transaction"] = {
        "id": xid, "asset": "cash" if t.asset == CASH else "shares", "amount": str(t.amount),
        "from_owner": t.owner, "to_owner": t.to_owner, "from": t.from_settlement, "to": t.to_settlement,
        "route": mv["route"] if mv else None, "distance_au": mv["distance_au"] if mv else None,
        "send_h": mv["launches"][0]["te"] if mv and mv["launches"] else None,
        "arrival_h": mv["delivered_h"] if mv else None,
        "total_delay_h": (t.completed_h - (order.source_h if order else t.initiated_h)) if t.completed_h else None,
        "retries": mv["retries"] if mv else 0, "losses": mv["losses"] if mv else 0,
        "launches": mv["launches"] if mv else [], "final_state": t.status.value,
        "duplicates_ignored": t.duplicates_ignored, "trade": t.leg_of,
    }
    if p.get("second"):
        t2 = ex.state.transfers[p["second"]]
        out["second_leg"] = {"id": t2.id, "asset": "cash" if t2.asset == CASH else "shares",
                             "amount": str(t2.amount), "from": t2.from_settlement, "to": t2.to_settlement,
                             "completed_h": t2.completed_h, "source_confirmed_h": t2.source_confirmed_h}
    return out


def auction_view(r):
    ex = r.ex
    out = []
    for bid in r.batch_ids:
        b = ex.state.batches[bid]
        ev = next((e for e in ex.journal.of_type(EventType.BATCH_EXECUTED) if e.data["batch_id"] == bid), None)
        alloc = {a["order_id"]: a for a in ev.data["allocations"]} if ev else {}
        ids = list(b.order_ids) + [o.id for o in ex.state.batch_orders.values()
                                   if bid in o.missed_batches or (o.batch_id == bid and o.id not in b.order_ids)]
        orders = []
        for oid in dict.fromkeys(ids):
            o = ex.state.batch_orders[oid]
            a = alloc.get(oid)
            orders.append({"id": oid, "account": o.account, "home": o.home, "side": o.side.value,
                           "quantity": str(o.quantity), "limit": str(o.limit_price),
                           "source_h": o.source_h, "arrived_h": o.arrived_h,
                           "allocated": a["quantity"] if a else "0", "rank": a["rank"] if a else None,
                           "reason": a["reason"] if a else ("missed this batch (rolled)" if bid in o.missed_batches
                                                             else o.status.value),
                           "in_batch": oid in b.order_ids, "status": o.status.value})
        out.append({"id": bid, "market": b.market, "opened_h": b.opened_h, "closes_h": b.closes_h,
                    "executed_h": b.executed_h, "price": None if b.price is None else str(b.price),
                    "timing": b.timing, "orders": orders,
                    "table": ev.data["table"] if ev else [], "trades": ev.data["trades"] if ev else []})
    return out


def futures_view(r):
    pid = r.extra.get("position")
    if not pid:
        return None
    ex = r.ex
    pos = ex.state.positions[pid]
    inst = ex.state.instruments[FUTURE]
    return {
        "symbol": FUTURE, "multiplier": str(inst.multiplier), "maturity_h": inst.maturity_h,
        "quantity": str(pos.quantity), "contract_price": str(pos.entry_price),
        "long": pos.long, "short": pos.short, "opened_h": pos.opened_h,
        "initial_margin": r.extra.get("im"),
        "risk_window_h": {a: MARGIN_POLICY.window(a) for a in (pos.long, pos.short)},
        "rule": {"move_per_observation": str(MARGIN_POLICY.move_per_observation),
                 "observation_interval_h": MARGIN_POLICY.observation_interval_h,
                 "maintenance_fraction": str(MARGIN_POLICY.maintenance_fraction)},
        "series": r.extra.get("series", []), "calls": r.extra.get("calls", []),
        "state": pos.state.value, "final_price": None if pos.final_price is None else str(pos.final_price),
        "paid": str(pos.paid), "guarantee_used": str(pos.guarantee_used), "shortfall": str(pos.shortfall),
        "winner": pos.winner, "defaulter": pos.defaulter,
        "discharged_h": pos.discharged_h, "backed_claim_h": pos.backed_claim_h, "spendable_h": pos.spendable_h,
        "guarantee_fund_now": str(ex.state.ledger.encumbered("Earth Clearing", CASH, "Earth")),
    }


def integrity(r):
    ex = r.ex
    c = Counter()
    for e in ex.journal:
        if e.type is EventType.TRANSFER_COMPLETED:
            c[("transfer", e.data["transfer_id"])] += 1
        elif e.type is EventType.BATCH_EXECUTED:
            c[("batch", e.data["batch_id"])] += 1
        elif e.type in (EventType.POSITION_SETTLED, EventType.POSITION_DEFAULTED):
            c[("position", e.data["position_id"])] += 1
        elif e.type is EventType.BATCH_LEG_SETTLED:
            c[("leg", e.data["order_id"])] += 1
    dup = [f"{k[0]} {k[1]} x{n}" for k, n in c.items() if n > 1]
    ds = [p for v in r.w.violations for p in v["problems"] if "over-encumbered" in p or "negative" in p]
    return {"duplicate_settlements": dup,
            "duplicates_ignored": sum(t.duplicates_ignored for t in ex.state.transfers.values()),
            "double_spend_violations": ds,
            "double_spend_attempts": r.blocked,
            "double_spend_allowed": [b for b in r.blocked if not b["rejected"]]}


# ------------------------------------------------------------------ run

def run_case(cid):
    title, fn = BY_ID[cid]
    t0 = time.time()
    try:
        r = fn()
    except Exception as exc:
        return {"id": cid, "title": title, "status": "FAIL", "duration_s": time.time() - t0,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc().splitlines()[-8:],
                "checks": [], "failures": [{"name": "scenario raised an exception", "expected": "completes",
                                            "actual": f"{type(exc).__name__}: {exc}"}]}
    after = balances(r.ex)
    failures = [c for c in r.checks if not c["pass"]]
    failures += [{"name": f"invariant after '{v['step']}' at h {v['t']:.4f}", "expected": "no violation",
                  "actual": "; ".join(v["problems"])} for v in r.w.violations]
    integ = integrity(r)
    failures += [{"name": "duplicate settlement", "expected": "each settles once", "actual": d}
                 for d in integ["duplicate_settlements"]]
    failures += [{"name": "double spend allowed", "expected": "rejected", "actual": b["attempt"]}
                 for b in integ["double_spend_allowed"]]
    for asset, row in after["totals"].items():
        if not row["conserved"]:
            failures.append({"name": f"{asset} conserved", "expected": row["opening"], "actual": row["total"]})
    return {
        "id": cid, "title": r.title, "status": "PASS" if not failures else "FAIL",
        "duration_s": round(time.time() - t0, 3),
        "checks": r.checks, "failures": failures,
        "invariants": {"steps_checked": r.w.steps, "violations": r.w.violations},
        "balances": {"before": r.before, "after": after},
        "conservation": after["totals"],
        "integrity": integ,
        "timeline": timeline(r.ex),
        "network": network_summary(r),
        "primary": primary_view(r),
        "auction": auction_view(r) if r.batch_ids else None,
        "futures": futures_view(r),
        "notes": r.notes, "extra": {k: v for k, v in r.extra.items()
                                    if k in ("cancel_results", "tiebreak", "outage", "auction_focus")},
        "homes": HOME,
    }


def summarize(results):
    def all_ok(pred):
        return all(pred(t) for t in results)
    return {
        "tests_passed": sum(t["status"] == "PASS" for t in results), "tests_total": len(results),
        "financial_invariants": all_ok(lambda t: not t.get("invariants", {}).get("violations") and "error" not in t),
        "cash_conserved": all_ok(lambda t: t.get("conservation", {}).get("cash", {}).get("conserved", False)),
        "shares_conserved": all_ok(lambda t: t.get("conservation", {}).get("shares", {}).get("conserved", False)),
        "double_spending": sum(len(t.get("integrity", {}).get("double_spend_violations", []))
                               + len(t.get("integrity", {}).get("double_spend_allowed", [])) for t in results),
        "double_spend_attempts_blocked": sum(len([b for b in t.get("integrity", {}).get("double_spend_attempts", [])
                                                  if b["rejected"]]) for t in results),
        "duplicate_settlements": sum(len(t.get("integrity", {}).get("duplicate_settlements", [])) for t in results),
        "duplicates_ignored": sum(t.get("integrity", {}).get("duplicates_ignored", 0) for t in results),
        "invariant_checks": sum(t.get("invariants", {}).get("steps_checked", 0) for t in results),
    }


def run_all():
    t0 = time.time()
    results = [run_case(cid) for cid, _, _ in CASES]
    return {"summary": summarize(results), "tests": results, "ran_at_s": round(time.time() - t0, 2)}


def _default(o):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    return str(o)


def dumps(obj):
    return json.dumps(obj, default=_default)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(json.dumps(run_case(sys.argv[1]), default=_default, indent=1)[:6000])
    else:
        out = run_all()
        for t in out["tests"]:
            print(f"{t['status']}  {t['title']}")
            for f in t["failures"]:
                print("      ", f)
        print(json.dumps(out["summary"], indent=1), f"\n{out['ran_at_s']} s")
