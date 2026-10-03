"""The 17 required scenarios. Each returns a finished ``Run`` with its checks."""

import math
from decimal import Decimal

from mpex import PositionState
from mpex.batch import BatchOrderStatus
from mpex.journal import EventType

from .scenarios import (CASH, CLEARING, FUTURE, HOME, HUB, LOCAL_BOOK, MARGIN_POLICY, MARKET_OP,
                        OPERATORS, SHARES, Run, avail, held, tiebreak_order)
from .world import SEC_H

D = Decimal


# --------------------------------------------------------------------- helpers

def _only_trade(r, batch_id):
    tids = r.ex.state.batches[batch_id].trade_ids
    return r.ex.state.trades[tids[0]] if tids else None


def _leg(r, trade_id, leg):
    return (r.ex.state.trade_legs[trade_id][leg] or {}).get("transfer_id")


def _alloc(r, batch_id):
    ev = [e for e in r.ex.journal.of_type(EventType.BATCH_EXECUTED) if e.data["batch_id"] == batch_id][0]
    return {a["order_id"]: a for a in ev.data["allocations"]}, ev.data


def _released(r, oid):
    ev = [e for e in r.ex.journal.of_type(EventType.BATCH_LEG_SETTLED) if e.data["order_id"] == oid]
    return (D(ev[0].data["released"]), ev[0].time_h) if ev else (None, None)


def _cross_trade(r, sell_limit=45, buy_limit=50, qty=100):
    r.open_batch(0.5)
    r.submit(1.0, "E-Alice", "sell", qty, sell_limit, "sell")
    r.submit(1.0, "N-Eve", "buy", qty, buy_limit, "buy")


def _set_primary(r, transfer=None, **kw):
    r.extra["primary"] = dict(transfer=transfer, **kw)


# ========================================================================= tests

def local_trade():
    r = Run("local-trade", "Local trade")
    ex, book = r.ex, LOCAL_BOOK["Earth"]
    out = {}

    def sell(t):
        ex.send_message(t, "E-Alice", OPERATORS["Earth"], "local", "client", "order", {"sell": 100, "limit": 40})
        r.w.at(t + SEC_H, "Alice order at Earth Exchange", lambda tt: out.update(
            sell=ex.submit_order(tt, "E-Alice", book, "sell", 100, 40,
                                 note="Earth Exchange locks 100 shares; rests on the Earth book")))

    def buy(t):
        ex.send_message(t, "E-Bob", OPERATORS["Earth"], "local", "client", "order", {"buy": 100, "limit": 42})
        r.w.at(t + SEC_H, "Bob order at Earth Exchange", lambda tt: out.update(
            buy=ex.submit_order(tt, "E-Bob", book, "buy", 100, 42,
                                note="crosses the resting sell; delivery versus payment on the Earth ledger"),
            buy_h=tt))
    r.w.at(1.0, "Alice sends", sell)
    r.w.at(1.5, "Bob sends", buy)
    r.w.run(until=5)

    trades = list(ex.state.trades.values())
    r.check("one continuous trade executed", 1, len(trades))
    t = trades[0]
    r.check("price is the resting order's limit", D(40), t.price)
    r.check("E-Bob shares at Earth", D(500), held(ex, "E-Bob", SHARES))
    r.check("E-Bob cash at Earth", D(45000 - 4000), held(ex, "E-Bob", CASH))
    r.check("E-Alice cash at Earth", D(74000), held(ex, "E-Alice", CASH))
    r.check("E-Bob's excess reservation released (paid 40 of limit 42)", D(0),
            ex.state.ledger.encumbered("E-Bob", CASH, "Earth"))
    r.check("no transfers between settlements", 0, len(ex.state.transfers))
    r.check("no backbone launches after hour 0", 0, sum(1 for x in r.w.launch_log if x["te"] >= 0))
    r.check("complete at execution (both parties at Earth)", t.executed_h, t.settled_h)
    _set_primary(r, kind="local", trade=t.id, accounts=["E-Alice", "E-Bob"],
                 steps=[("ORDER SUBMITTED", 1.0), ("SHARES RESERVED", 1.0 + SEC_H),
                        ("MATCHED (continuous)", t.executed_h), ("FINAL (DvP)", t.settled_h)])
    return r


def cross_planet():
    r = Run("cross-planet", "Cross-planet trade")
    _cross_trade(r)
    r.w.run(until=80)
    ex = r.ex
    bid = r.batch_ids[0]
    t = _only_trade(r, bid)
    r.check("batch executed one trade", True, t is not None)
    r.check("uniform price = midpoint of the tied range 45..50", D("47.50"), t.price)
    shares_x, cash_x = _leg(r, t.id, "shares"), _leg(r, t.id, "cash")
    xs, xc = ex.state.transfers[shares_x], ex.state.transfers[cash_x]
    r.check("N-Eve shares at Neptune", D(600), held(ex, "N-Eve", SHARES))
    r.check("N-Eve cash at Neptune", D(55000) - D(4750), held(ex, "N-Eve", CASH))
    r.check("E-Alice cash at Earth", D(70000) + D(4750), held(ex, "E-Alice", CASH))
    r.check("E-Alice shares at Earth", D(500), held(ex, "E-Alice", SHARES))
    rel, rel_h = _released(r, r.orders["buy"])
    r.check("N-Eve's unused $250 released when the result reached Neptune", D(250), rel)
    ship = [m for m in r.w.messages.values() if m["kind"] == "transfer" and m["recipient"] == "Neptune Exchange"][0]
    r.check("shares usable at Neptune the moment the transfer arrived", ship["delivered_h"], xs.completed_h)
    r.check("trade complete when the later leg is usable", max(xs.completed_h, xc.completed_h), t.settled_h)
    r.check("both sources reconciled", True, xs.source_confirmed_h is not None and xc.source_confirmed_h is not None)
    r.check("shares travelled the pinned route", ["Earth", "Relay A", "Neptune"], ship["route"])
    _set_primary(r, transfer=shares_x, second=cash_x, trade=t.id, order=r.orders["sell"],
                 accounts=["E-Alice", "N-Eve"])
    return r


def equal_price():
    r = Run("equal-price", "Equal-price auction")
    r.open_batch(9.5)
    r.submit(10.0, "N-Eve", "buy", 10, 100, "neptune")
    r.submit(10.0 + 1 / 60, "E-Bob", "buy", 10, 100, "earth")
    r.submit(10.0, "M-Carla", "sell", 10, 100, "mars")
    r.w.run(until=80)
    ex, bo = r.ex, r.ex.state.batch_orders
    e, n = bo[r.orders["earth"]], bo[r.orders["neptune"]]
    alloc, data = _alloc(r, r.batch_ids[0])
    r.check("Earth's bid reached the market first", True, e.arrived_h < n.arrived_h)
    r.check("Neptune's bid has the earlier source timestamp", True, n.source_h < e.source_h)
    r.check("clearing price", "100.00", data["price"])
    r.check("N-Eve (earlier source time) gets all 10", "10", alloc[n.id]["quantity"])
    r.check("E-Bob (arrived first, later source time) gets 0", "0", alloc[e.id]["quantity"])
    rel, _ = _released(r, e.id)
    r.check("E-Bob's $1,000 reservation released", D(1000), rel)
    r.extra["auction_focus"] = "Arrival order Earth then Neptune; priority Neptune then Earth (source time)."
    _set_primary(r, transfer=_leg(r, _only_trade(r, r.batch_ids[0]).id, "shares"),
                 accounts=["N-Eve", "E-Bob", "M-Carla"])
    return r


def exact_tie():
    r = Run("exact-tie", "Exact tie / pro-rata")
    r.open_batch(9.5)
    r.submit(10.0, "E-Bob", "buy", 10, 100, "bob")
    r.submit(10.0, "M-Diego", "buy", 10, 100, "diego")
    r.submit(10.0, "N-Eve", "sell", 15, 100, "eve")
    r.w.run(until=80)
    bo = r.ex.state.batch_orders
    bob, diego = bo[r.orders["bob"]], bo[r.orders["diego"]]
    alloc, data = _alloc(r, r.batch_ids[0])
    r.check("identical limit and source time", (bob.limit_price, bob.source_h),
            (diego.limit_price, diego.source_h))
    got = {bob.id: D(alloc[bob.id]["quantity"]), diego.id: D(alloc[diego.id]["quantity"])}
    r.check("15 shares allocated in total", D(15), sum(got.values()))
    r.check("pro rata 7.5 each, rounded down to whole shares", [D(7), D(8)], sorted(got.values()))
    extra = tiebreak_order(r.batch_ids[0], [bob.id, diego.id])[0]
    r.check("spare share goes to the first order by sha256(batch:order)", D(8), got[extra])
    r.extra["tiebreak"] = {o: __import__("hashlib").sha256(f"{r.batch_ids[0]}:{o}".encode()).hexdigest()[:16]
                           for o in (bob.id, diego.id)}
    _set_primary(r, accounts=["E-Bob", "M-Diego", "N-Eve"])
    return r


def partial_fill():
    r = Run("partial-fill", "Partial fill")
    r.open_batch(0.5)
    r.submit(1.0, "N-Eve", "buy", 100, 50, "buy")
    r.submit(1.0, "E-Alice", "sell", 60, 45, "sell")
    r.w.run(until=80)
    alloc, data = _alloc(r, r.batch_ids[0])
    buy = r.ex.state.batch_orders[r.orders["buy"]]
    r.check("N-Eve filled 60 of 100", "60", alloc[buy.id]["quantity"])
    price = D(data["price"])
    rel, rel_h = _released(r, buy.id)
    r.check("released = 40 unfilled x 50 + 60 x (50 - price)", D(40) * 50 + 60 * (50 - price), rel)
    r.check("N-Eve shares at Neptune", D(560), held(r.ex, "N-Eve", SHARES))
    r.check("N-Eve cash fully available after settlement", held(r.ex, "N-Eve", CASH), avail(r.ex, "N-Eve", CASH))
    r.check("E-Alice sold 60", D(540), held(r.ex, "E-Alice", SHARES))
    _set_primary(r, transfer=_leg(r, _only_trade(r, r.batch_ids[0]).id, "cash"),
                 accounts=["N-Eve", "E-Alice"])
    return r


def late_order():
    r = Run("late-order", "Late order")
    d1 = r.batch_timing(0.5)["duration_h"]
    c1 = 0.5 + d1
    r.open_batch(0.5)
    r.submit(1.0, "E-Bob", "buy", 5, 99, "b1-buy")
    r.submit(1.0, "E-Alice", "sell", 5, 99, "b1-sell")
    r.submit(c1 - 1.0, "N-Eve", "buy", 10, 100, "late-A")         # cannot arrive before c1
    o2 = c1 + 4.0
    c2 = o2 + r.batch_timing(o2)["duration_h"]
    r.open_batch(o2)
    r.submit(o2 + 0.1, "M-Carla", "sell", 10, 98, "b2-sell")
    r.submit(c2 - 1.0, "N-Eve", "buy", 10, 100, "late-B")         # misses batch 2
    o3 = c2 + 4.0
    c3 = o3 + r.batch_timing(o3)["duration_h"]
    r.open_batch(o3)
    r.submit(o3 + 0.1, "E-Bob", "buy", 10, 120, "b3-buy")
    r.submit(o3 + 0.1, "M-Diego", "sell", 10, 105, "b3-sell")
    r.w.run(until=c3 + 30)
    bo = r.ex.state.batch_orders
    A, B = bo[r.orders["late-A"]], bo[r.orders["late-B"]]
    b1, b2, b3 = r.batch_ids
    r.check("order A arrived after batch 1 closed", True, A.arrived_h > c1)
    r.check("A rolled from batch 1", [b1], A.missed_batches)
    r.check("A kept its original limit", D(100), A.limit_price)
    p2 = r.ex.state.batches[b2].price
    r.check("A executed in batch 2 at or below its limit", True, A.filled == 10 and p2 <= A.limit_price)
    r.check("batch 2 price = midpoint of 98..100", D("99.00"), p2)
    p3 = r.ex.state.batches[b3].price
    r.check("B rolled from batch 2", [b2], B.missed_batches)
    r.check("batch 3 clears above B's limit", True, p3 > B.limit_price)
    r.check("B not executed (price protection)", D(0), B.filled)
    rel, _ = _released(r, B.id)
    r.check("B's $1,000 lock released", D(1000), rel)
    r.extra["batches_focus"] = [b1, b2, b3]
    _set_primary(r, accounts=["N-Eve", "M-Carla", "E-Bob"])
    return r


def limit_protection():
    r = Run("limit-protection", "Limit-price protection")
    r.open_batch(0.5)
    r.submit(1.0, "E-Bob", "buy", 50, 50, "bob")
    r.submit(1.0, "N-Eve", "buy", 30, 48, "eve")
    r.submit(1.0, "M-Diego", "buy", 20, 40, "diego")
    r.submit(1.0, "E-Alice", "sell", 40, 45, "alice")
    r.submit(1.0, "C-Finn", "sell", 30, 47, "finn")
    r.w.run(until=80)
    alloc, data = _alloc(r, r.batch_ids[0])
    p = D(data["price"])
    bo = r.ex.state.batch_orders
    # By hand: volume is 70 at 47 and 48 with imbalance 10 at both; midpoint 47.50 still trades 70.
    r.check("clearing price", D("47.50"), p)
    for key in ("bob", "eve", "diego", "alice", "finn"):
        o = bo[r.orders[key]]
        if D(alloc[o.id]["quantity"]) > 0:
            ok = p <= o.limit_price if o.side.value == "buy" else p >= o.limit_price
            r.check(f"{o.account} {o.side.value} filled within limit {o.limit_price}", True, ok)
    d = bo[r.orders["diego"]]
    r.check("M-Diego (limit 40 < 47.50) not executed", "0", alloc[d.id]["quantity"])
    r.check("M-Diego's reason is price protection", True, "price protection" in alloc[d.id]["reason"])
    r.check("N-Eve rationed: 20 of 30 after the better-priced E-Bob", "20", alloc[r.orders["eve"]]["quantity"])
    _set_primary(r, accounts=["E-Bob", "N-Eve", "M-Diego", "E-Alice", "C-Finn"])
    return r


def cancel_before():
    r = Run("cancel-before", "Cancellation before execution")
    r.open_batch(0.5)
    r.submit(1.0, "N-Eve", "buy", 10, 100, "eve")
    r.submit(1.0, "E-Alice", "sell", 10, 95, "alice")
    r.cancel(2.0, "eve")
    r.w.run(until=80)
    eve = r.ex.state.batch_orders[r.orders["eve"]]
    res = r.extra["cancel_results"][eve.id]
    close = r.ex.state.batches[r.batch_ids[0]].closes_h
    r.check("cancel reached the market before the batch closed", True, res["t"] < close)
    r.check("cancel accepted", "cancelled", res["result"])
    r.check("no trade executed", 0, len(r.ex.state.trades))
    rel, rel_h = _released(r, eve.id)
    r.check("N-Eve's full $1,000 lock released", D(1000), rel)
    r.check("release only after the confirmation reached Neptune", True, rel_h > res["t"])
    r.check("N-Eve cash back to fully available", D(55000), avail(r.ex, "N-Eve", CASH))
    _set_primary(r, accounts=["N-Eve", "E-Alice"])
    return r


def cancel_after():
    r = Run("cancel-after", "Cancellation after execution")
    close = 0.5 + r.batch_timing(0.5)["duration_h"]
    r.open_batch(0.5)
    r.submit(1.0, "N-Eve", "buy", 10, 100, "eve")
    r.submit(1.0, "E-Alice", "sell", 10, 95, "alice")
    r.cancel(close - 0.5, "eve")
    r.w.run(until=80)
    eve = r.ex.state.batch_orders[r.orders["eve"]]
    res = r.extra["cancel_results"][eve.id]
    r.check("cancel arrived after execution", True, res["t"] > close)
    r.check("cancel rejected: trade is binding", "rejected", res["result"])
    t = _only_trade(r, r.batch_ids[0])
    r.check("trade settled", "settled", t.status.value)
    r.check("N-Eve holds the bought shares at Neptune", D(510), held(r.ex, "N-Eve", SHARES))
    _set_primary(r, transfer=_leg(r, t.id, "shares"), accounts=["N-Eve", "E-Alice"])
    return r


def retry_duplicate():
    r = Run("retry-duplicate", "Packet retry / duplicate protection")
    r.w.drop = lambda i: ("forced loss (test): first launch of the share transfer on Relay A -> Neptune"
                          if i["meta"].get("kind") == "transfer" and i["from"] == "Relay A"
                          and i["to"] == "Neptune" and i["attempt"] == 1 else None)
    r.extra["reconcile_after_rt"] = True
    _cross_trade(r)
    r.w.run(until=120)
    t = _only_trade(r, r.batch_ids[0])
    xs = _leg(r, t.id, "shares")
    lost = [x for x in r.w.launch_log if x["lost"]]
    r.check("exactly one launch lost (forced)", 1, len(lost))
    retried = [x for x in r.w.launch_log if x["packet"] == lost[0]["packet"] and x["attempt"] == 2]
    r.check("hop retried after R_h and delivered", True, bool(retried) and not retried[0]["lost"])
    completions = [e for e in r.ex.journal.of_type(EventType.TRANSFER_COMPLETED) if e.data["transfer_id"] == xs]
    r.check("transfer completed exactly once", 1, len(completions))
    results = [d["result"] for d in r.extra["deliveries"] if d["transfer"] == xs]
    r.check("status query with the same transaction id hit the existing record", ["completed", "duplicate"], results)
    r.check("N-Eve shares credited once", D(600), held(r.ex, "N-Eve", SHARES))

    # Grace window: an order sent when the batch opens, whose slowest hop loses
    # its first 3 launches (4 launches = initial + 3 retries), still makes the batch.
    g = Run("retry-grace", "grace")
    g.w.drop = lambda i: ("forced loss (test): order launches 1-3 on Neptune -> Relay A"
                          if i["meta"].get("kind") == "batch_order" and i["from"] == "Neptune"
                          and i["attempt"] <= 3 else None)
    g.open_batch(0.5)
    g.submit(0.5, "N-Eve", "buy", 10, 100, "eve")
    g.submit(1.0, "E-Alice", "sell", 10, 95, "alice")
    g.w.run(until=80)
    eve, b = g.ex.state.batch_orders[g.orders["eve"]], g.ex.state.batches[g.batch_ids[0]]
    used = max(l["attempt"] for l in g.w.launch_log
               if l["kind"] == "batch_order" and l["from"] == "Neptune" and not l["lost"])
    r.check("worst-case order used all 4 launches on its slowest hop", 4, used)
    r.check("it still arrived before the batch closed (grace covers 3 retries)", True,
            eve.arrived_h <= b.closes_h)
    r.check("and executed in that batch", D(10), eve.filled)
    r.extra["grace"] = {"arrived_h": eve.arrived_h, "closes_h": b.closes_h, "launches": used,
                        "duration_h": b.timing["duration_h"]}
    r.notes.append(f"Grace check: an order sent at batch open needed all 4 launches, arrived "
                   f"h {eve.arrived_h:.2f}, batch closed h {b.closes_h:.2f}. Orders sent later "
                   "than the batch opening get less grace and may roll to the next batch.")
    _set_primary(r, transfer=xs, accounts=["E-Alice", "N-Eve"])
    return r


def uncertain_lock():
    r = Run("uncertain-lock", "Uncertain settlement locking")
    probe = {}

    def on_final(ta):
        # The confirmation back to Earth is caught by a 6 h forced-loss incident at Neptune.
        r.w.incidents.append({"kind": "forced_loss", "node": "Neptune", "start_h": ta, "end_h": ta + 6})
        probe["final_h"] = ta
        r.w.at(ta + 1.0, "double-spend attempt", attempt)

    def attempt(t):
        x = r.ex.state.transfers[probe["xfr"]]
        probe["confirmed_at_probe"] = x.source_confirmed_h
        probe["alice_shares"] = held(r.ex, "E-Alice", SHARES)
        r.attempt(t, "E-Alice sells 600 shares at Earth while the 100 sold are unconfirmed",
                  lambda: r.ex.submit_order(t, "E-Alice", LOCAL_BOOK["Earth"], "sell", 600, 40))
        r.attempt(t, "E-Alice re-sends the 100 shares to someone else",
                  lambda: r.ex.initiate_transfer(t, "E-Alice", SHARES, 600, "Earth", "Mars", to_owner="M-Carla"))

    _cross_trade(r)

    def hook(t):
        tr = next(iter(r.ex.state.trades.values()), None)
        if tr and r.ex.state.trade_legs[tr.id]["shares"]:
            probe["xfr"] = r.ex.state.trade_legs[tr.id]["shares"]["transfer_id"]
            r.extra.setdefault("on_final", {})[probe["xfr"]] = on_final
        else:
            r.w.at(t + 0.05, "wait for shares leg", hook)
    r.w.at(0.6, "watch shares leg", hook)
    r.extra["reconcile_after_rt"] = True
    r.w.run(until=120)
    x = r.ex.state.transfers[probe["xfr"]]
    r.check("shares usable at Neptune before the source knew", True, x.completed_h < x.source_confirmed_h)
    r.check("source still unconfirmed one hour after finality", None, probe["confirmed_at_probe"])
    r.check("E-Alice's sold shares are gone from her ledger", D(500), probe["alice_shares"])
    r.check("both double-spend attempts rejected", [True, True], [b["rejected"] for b in r.blocked])
    lost = [l for l in r.w.launch_log if l["lost"]]
    r.check("confirmation launches lost to the incident", True,
            bool(lost) and all("incident" in l["loss_reason"] for l in lost))
    r.check("source reconciled after the incident", True, x.source_confirmed_h > probe["final_h"] + 6)
    _set_primary(r, transfer=probe["xfr"], accounts=["E-Alice", "N-Eve"])
    return r


def finality():
    r = Run("finality", "Settlement finality")
    probe = {}

    def on_final(ta):
        probe["final_h"] = ta
        probe["order"] = r.ex.submit_order(ta, "N-Eve", LOCAL_BOOK["Neptune"], "sell", 600, 60,
                                           note="N-Eve uses the new shares at once (destination finality)")[0]

    def hook(t):
        tr = next(iter(r.ex.state.trades.values()), None)
        if tr and r.ex.state.trade_legs[tr.id]["shares"]:
            probe["xfr"] = r.ex.state.trade_legs[tr.id]["shares"]["transfer_id"]
            r.extra.setdefault("on_final", {})[probe["xfr"]] = on_final
        else:
            r.w.at(t + 0.05, "wait for shares leg", hook)
    _cross_trade(r)
    r.w.at(0.6, "watch shares leg", hook)
    r.w.run(until=80)
    x = r.ex.state.transfers[probe["xfr"]]
    o = r.ex.state.orders[probe["order"]]
    r.check("N-Eve's sell of all 600 shares accepted at the moment of validation", x.completed_h, o.submitted_h)
    r.check("source learned of finality later", True, x.source_confirmed_h > x.completed_h)
    r.check("600 shares locked by the new order at Neptune", D(600),
            r.ex.state.ledger.encumbered("N-Eve", SHARES, "Neptune"))
    r.check("E-Alice cannot reuse the sold shares", D(500), avail(r.ex, "E-Alice", SHARES))
    _set_primary(r, transfer=probe["xfr"], accounts=["E-Alice", "N-Eve"])
    return r


def _futures_run(test_id, title, path=None, respond=True, until=40):
    r = Run(test_id, title, futures=True)
    r.extra["respond_to_calls"] = respond
    r.fund_guarantee(0.1, [("E-Alice", 25000), ("E-Bob", 25000)])
    im_eve, im_alice = r.initial_margin("N-Eve", 10, 100), r.initial_margin("E-Alice", 10, 100)
    r.extra.update(im={"N-Eve": str(im_eve), "E-Alice": str(im_alice)})

    def early(t):
        r.attempt(t, "open the future before N-Eve's margin reaches Earth",
                  lambda: r.ex.open_position(t, FUTURE, "N-Eve", "E-Alice", 10, 100,
                                             long_margin=im_eve, short_margin=im_alice))

    waiting = {"N-Eve", "E-Alice"}

    def landed(ta, who):
        waiting.discard(who)
        r.extra.setdefault("margin_landed", {})[who] = ta
        if not waiting:
            r.extra["position"] = r.ex.open_position(
                ta, FUTURE, "N-Eve", "E-Alice", 10, 100, long_margin=im_eve, short_margin=im_alice,
                note=f"both initial margins encumbered at {CLEARING}")
            r.extra["opened_h"] = ta
    r.w.at(1.0, "early open attempt", early)
    r.move_margin_to_clearing(0.5, "N-Eve", im_eve, on_arrive=lambda ta: landed(ta, "N-Eve"))
    r.move_margin_to_clearing(0.5, "E-Alice", im_alice, on_arrive=lambda ta: landed(ta, "E-Alice"))
    if path:
        r.price_feed(path)
    r.w.run(until=until)
    return r, im_eve, im_alice


def futures_opening():
    r, im_eve, im_alice = _futures_run("futures-opening", "Futures opening")
    pos = r.ex.state.positions[r.extra["position"]]
    n_eve = 1 + math.ceil(MARGIN_POLICY.window("N-Eve") / 12)
    n_alice = 1 + math.ceil(MARGIN_POLICY.window("E-Alice") / 12)
    r.check("N-Eve initial margin = 100,000 notional x 5% x observations in her window",
            D(100000) * D("0.05") * n_eve, im_eve)
    r.check(f"E-Alice initial margin (window {MARGIN_POLICY.window('E-Alice'):.2f} h to {HUB})",
            D(100000) * D("0.05") * n_alice, im_alice)
    r.check("opening before the margins arrived was rejected", True, r.blocked[0]["rejected"])
    r.check(f"position opened when the last margin landed at {HUB}",
            max(r.extra["margin_landed"].values()), pos.opened_h)
    r.check(f"both margins encumbered at {HUB}", (im_eve, im_alice),
            (r.ex.state.ledger.encumbered("N-Eve", CASH, HUB),
             r.ex.state.ledger.encumbered("E-Alice", CASH, HUB)))
    r.check("guarantee fund = $50,000 contributed after hour 0", D(50000),
            r.ex.state.ledger.encumbered(CLEARING, CASH, HUB))
    r.check(f"{CLEARING} started with nothing", False,
            any(p["principal"] == CLEARING for p in r.before["principals"]))
    _set_primary(r, transfer=r.extra["margin_transfers"][0], accounts=["N-Eve", "E-Alice", CLEARING])
    return r


FALLING = [99, 97, 94, 92, 90, 89, 88, 86, 85, 84, 83, 82, 81, 80, 80, 79, 79, 78, 78, 79,
           80, 80, 81, 80, 80]          # h 12 ... h 300, steps within the declared 5%
CRASH = [99, 97, 93, 85, 78, 75, 74, 74, 75, 76, 77, 78, 78, 79, 79, 80, 80, 80, 79, 79,
         80, 80, 80, 80, 80]            # h 36 -> h 60 breaks the 5% assumption


def margin_call():
    r, im_eve, _ = _futures_run("margin-call", "Margin call", FALLING, respond=True, until=320)
    pos = r.ex.state.positions[r.extra["position"]]
    calls = r.extra.get("calls", [])
    r.check("price path moves at least 20%", True, min(FALLING) <= 80)
    r.check("at least one margin call issued", True, len(calls) >= 1)
    first_breach = next(row for row in r.extra["series"]
                        if D(row["long"]["posted"]) < D(row["long"]["maintenance"]))
    r.check("first call issued at the first mark below maintenance", first_breach["t"], calls[0]["issued_h"])
    r.check("every call met before its deadline", True,
            all(c["met_h"] is not None and c["met_h"] <= c["deadline_h"] for c in calls))
    r.check("no default", "settled", pos.state.value)
    r.check("final payment = 10 x 100 x (80 - 100) from N-Eve's margin", D(20000), pos.paid)
    r.check("guarantee fund untouched", D(0), pos.guarantee_used)
    w = pos.winner
    if HOME[w] == HUB:
        r.check(f"winner {w} can spend at {HUB} when the position closed", pos.discharged_h, pos.spendable_h)
    else:
        r.check(f"winner {w} can spend only after the payout reaches {HOME[w]}", True,
                pos.spendable_h is not None and pos.spendable_h > pos.discharged_h)
    _set_primary(r, transfer=r.extra["margin_transfers"][0], accounts=["N-Eve", "E-Alice", CLEARING])
    return r


def funded_default():
    r, im_eve, _ = _futures_run("funded-default", "Funded default", CRASH, respond=False, until=320)
    pos = r.ex.state.positions[r.extra["position"]]
    call = r.extra["calls"][0]
    owed = D(10) * 100 * (100 - pos.final_price)
    r.check("margin call issued and not met", True, call["met_h"] is None)
    r.check("default declared after the deadline", True, pos.discharged_h > call["deadline_h"])
    r.check("state", "funded_default", pos.state.value)
    r.check("winner paid in full", owed, pos.paid)
    r.check("paid from margin first, then the guarantee fund", owed - im_eve, pos.guarantee_used)
    r.check("nothing unbacked", D(0), pos.shortfall)
    w = pos.winner
    if HOME[w] == HUB:
        r.check(f"winner {w} can spend at {HUB} when the position closed", pos.discharged_h, pos.spendable_h)
    else:
        r.check(f"winner {w} can spend only after the payout reaches {HOME[w]}", True,
                pos.spendable_h is not None and pos.spendable_h > pos.discharged_h)
    r.check("guarantee fund reduced by the draw", D(50000) - pos.guarantee_used,
            r.ex.state.ledger.encumbered(CLEARING, CASH, HUB))
    r.notes.append(f"N-Eve owes {CLEARING} ${pos.guarantee_used} for the fund draw (recorded claim).")
    _set_primary(r, accounts=["N-Eve", "E-Alice", CLEARING])
    return r


def disconnection():
    r = Run("disconnection", "Disconnection")
    r.w.incidents.append({"kind": "gateway_isolation", "node": "Mars", "start_h": 1.0, "end_h": 73.0})
    ex, book, probe = r.ex, LOCAL_BOOK["Mars"], {}

    def local(t):
        ex.submit_order(t, "M-Carla", book, "sell", 50, 40, note="local market keeps running")
        ex.submit_order(t + SEC_H, "M-Diego", book, "buy", 50, 41)
    r.w.at(2.0, "local Mars trade", local)
    r.submit(3.0, "M-Carla", "sell", 20, 45, "carla")

    def at72(t):
        o = ex.state.batch_orders[r.orders["carla"]]
        probe.update(status=o.status.value, lock=ex.state.ledger.encumbrances[o.encumbrance_id].amount)
    r.w.at(72.0, "probe during isolation", at72)
    r.open_batch(75.0)
    r.submit(76.0, "E-Bob", "buy", 20, 46, "bob")
    r.w.run(until=160)
    local_trade = [t for t in ex.state.trades.values() if t.symbol == book]
    r.check("local Mars trade executed during the isolation", True,
            bool(local_trade) and 1.0 <= local_trade[0].executed_h < 73.0)
    lost = [l for l in r.w.launch_log if l["lost"]]
    r.check("launches during the window failed (incident)", True,
            bool(lost) and all(1.0 <= l["te"] < 73.0 and "isolation" in l["loss_reason"] for l in lost))
    r.check("M-Carla's 20 shares stayed locked at Mars throughout", ("reserved", D(20)),
            (probe["status"], probe["lock"]))
    carla = ex.state.batch_orders[r.orders["carla"]]
    r.check("order reached the market only after the isolation", True, carla.arrived_h >= 73.0)
    cross = [t for t in ex.state.trades.values() if t.symbol == "ARES"]
    r.check("cross-planet trade completed after recovery", "settled", cross[0].status.value if cross else None)
    r.extra["outage"] = {"node": "Mars", "start_h": 1.0, "end_h": 73.0}
    _set_primary(r, transfer=_leg(r, cross[0].id, "shares") if cross else None,
                 accounts=["M-Carla", "M-Diego", "E-Bob"])
    return r


def clearing_outage():
    r = Run("clearing-outage", "Clearing-house outage/recovery", futures=True)
    r.w.incidents.append({"kind": "gateway_isolation", "node": HUB, "start_h": 1.0, "end_h": 73.0})
    local_at = HUB if sum(1 for h in HOME.values() if h == HUB) >= 2 else "Earth"
    pair = [a for a, h in HOME.items() if h == local_at][:2]
    if local_at != HUB:
        r.notes.append(f"{HUB} has only one account, so the same-settlement trading demo runs at {local_at} "
                       "(its local market is not isolated).")
    ex = r.ex
    r.fund_guarantee(0.1, [("E-Alice", 25000), ("E-Bob", 25000)])
    im_eve, im_alice = r.initial_margin("N-Eve", 10, 100), r.initial_margin("E-Alice", 10, 100)
    recovery = {}

    def local(t):
        ex.submit_order(t, pair[0], LOCAL_BOOK[local_at], "sell", 50, 40, note=f"{local_at} local market keeps running")
        ex.submit_order(t + SEC_H, pair[1], LOCAL_BOOK[local_at], "buy", 50, 41)
    r.w.at(10.0, f"local {local_at} trade", local)
    r.w.at(20.0, "open attempt during outage", lambda t: r.attempt(
        t, "open a new future while Earth Clearing is isolated",
        lambda: ex.open_position(t, FUTURE, "N-Eve", "E-Alice", 10, 100,
                                 long_margin=im_eve, short_margin=im_alice)))

    def margin_landed(ta):
        recovery["margin_h"] = ta
        ex.annotate(ta, CLEARING, "RECOVERY 1-4: sessions alive; exchange state summary; dedupe by id; reconcile")
        r.w.send(ta, CLEARING, OPERATORS["Neptune"], "state_summary", {"open": [], "pending": []},
                 lambda tb: r.w.send(tb, OPERATORS["Neptune"], CLEARING, "summary_ack", {},
                                     lambda tc: reopen(tc)))

    def reopen(tc):
        recovery["reopen_h"] = tc
        ex.annotate(tc, CLEARING, "RECOVERY 6-7: cross-settlement services and new futures reopen")
        r.extra["position"] = ex.open_position(tc, FUTURE, "N-Eve", "E-Alice", 10, 100,
                                               long_margin=im_eve, short_margin=im_alice)
    r.move_margin_to_clearing(0.2, "E-Alice", im_alice)  # no-op when E-Alice is at the hub
    r.move_margin_to_clearing(0.5, "N-Eve", im_eve, on_arrive=margin_landed)
    r.w.run(until=200)
    local_trade = [t for t in ex.state.trades.values() if t.symbol == LOCAL_BOOK[local_at]]
    r.check(f"{local_at} local trade executed during the outage", True,
            bool(local_trade) and 1.0 <= local_trade[0].executed_h < 73.0)
    r.check("new future rejected during the outage", True, r.blocked[0]["rejected"])
    r.check("margin reached Earth only after the outage ended", True, recovery.get("margin_h", 0) >= 73.0)
    pos = ex.state.positions.get(r.extra.get("position") or "")
    r.check("future opened after recovery steps", True, pos is not None and pos.opened_h >= recovery["margin_h"])
    lost = [l for l in r.w.launch_log if l["lost"]]
    r.check("all losses fall inside the isolation window", True,
            bool(lost) and all(1.0 <= l["te"] < 73.0 for l in lost))
    r.extra["outage"] = {"node": HUB, "start_h": 1.0, "end_h": 73.0,
                         "service_restored_h": recovery.get("reopen_h"),
                         "lost_service_h": (recovery.get("reopen_h") or 0) - 1.0}
    _set_primary(r, transfer=r.extra["margin_transfers"][0], accounts=["N-Eve", "E-Alice", CLEARING])
    return r


CASES = [
    ("local-trade", "Local trade", local_trade),
    ("cross-planet", "Cross-planet trade", cross_planet),
    ("equal-price", "Equal-price auction", equal_price),
    ("exact-tie", "Exact tie / pro-rata", exact_tie),
    ("partial-fill", "Partial fill", partial_fill),
    ("late-order", "Late order", late_order),
    ("limit-protection", "Limit-price protection", limit_protection),
    ("cancel-before", "Cancellation before execution", cancel_before),
    ("cancel-after", "Cancellation after execution", cancel_after),
    ("retry-duplicate", "Packet retry / duplicate protection", retry_duplicate),
    ("uncertain-lock", "Uncertain settlement locking", uncertain_lock),
    ("finality", "Settlement finality", finality),
    ("futures-opening", "Futures opening", futures_opening),
    ("margin-call", "Margin call", margin_call),
    ("funded-default", "Funded default", funded_default),
    ("disconnection", "Disconnection", disconnection),
    ("clearing-outage", "Clearing-house outage/recovery", clearing_outage),
]
