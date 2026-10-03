"""The 17 market scenarios behind the Tests & Evidence dashboard.

Each scenario builds a fresh exchange from the opening balance sheet (a reset),
runs it on the network model through ``World``, and records checks. Expected
values in checks are derived from the rules (limits, priority, margin formula),
not copied from a previous run.

Design rules used here (design paper, with the review fixes):
* The global batch market and the clearing house sit at Earth.
* Priority timestamp = the home operator's receipt stamp (client send + 1 s).
* Batch length = 1.1 x the slowest eligible one-way delay + a grace window for every
  hop retry the transport allows (4 launches per hop = initial + 3 retries, each
  waiting R_h of the longest hop on any eligible route), from the geometry when the
  batch opens.
* The guarantee fund starts empty and is funded by account contributions after hour 0.
* A cross-settlement trade completes only when both legs (shares and cash) are
  usable at their receivers' home settlements.
"""

import hashlib
import os
from decimal import Decimal

from mpex import (CommunicationRiskMarginPolicy, Equity, Exchange, ExchangeError, Future,
                  OpeningAccount, OpeningBalanceSheet, PositionState)
from mpex.batch import BatchOrderStatus
from mpex.positions import Party, Position

from .world import SEC_H, World, ng

D = Decimal

ACCOUNTS = (
    ("E-Alice", "Earth", 70000, 600),
    ("E-Bob", "Earth", 45000, 400),
    ("M-Carla", "Mars", 60000, 550),
    ("M-Diego", "Mars", 45000, 450),
    ("N-Eve", "Neptune", 55000, 500),
    ("C-Finn", "Ceres", 35000, 500),
)
HOME = {a[0]: a[1] for a in ACCOUNTS}
OPERATORS = {"Earth": "Earth Exchange", "Mars": "Mars Exchange",
             "Neptune": "Neptune Exchange", "Ceres": "Ceres Exchange"}
# Hub settlement for the global batch market and the clearing house (MPEX_HUB, default Earth).
HUB = os.environ.get("MPEX_HUB", "Earth")
if HUB not in OPERATORS:
    raise ValueError(f"MPEX_HUB must be one of {sorted(OPERATORS)}, got {HUB!r}")
MARKET, MARKET_OP, CLEARING = HUB, OPERATORS[HUB], f"{HUB} Clearing"
SYMBOL, LOCAL_BOOK = "ARES", {"Earth": "ARES.EARTH", "Mars": "ARES.MARS",
                              "Neptune": "ARES.NEPTUNE"}
FUTURE, SOURCE = "MOI-F300", "MOI Publisher"
BATCH_HOP_RETRIES = 3  # grace covers all retries of a hop: 4 launches = initial + 3
SHARES, CASH = "SHR:AresHabitat", "NEO"


def balance_sheet():
    return OpeningBalanceSheet(tuple(OpeningAccount(n, s, D(c), {"AresHabitat": q})
                                     for n, s, c, q in ACCOUNTS))


def risk_windows(t_h=0.0):
    """Margin risk window per account: call out + funded reply back + one hop retry."""
    net, out = ng.System(), []
    for name, home, *_ in ACCOUNTS:
        if home == HUB:
            out.append((name, 2 * SEC_H))  # local access each way
            continue
        there = net.routes(HUB, home, t_h / 24)[0]
        back = net.routes(home, HUB, t_h / 24)[0]
        longest = max(h["ta"] - h["te"] for h in there["hops"] + back["hops"]) * 24
        window = there["light_min"] / 60 + back["light_min"] / 60 + 2 * longest + 1 + 2 * SEC_H
        out.append((name, round(window, 4)))
    return tuple(out)


MARGIN_POLICY = CommunicationRiskMarginPolicy(move_per_observation=D("0.05"),
                                              risk_window_h=risk_windows())


class Run:
    """One scenario run from a full reset of the opening balance sheet."""

    def __init__(self, test_id, title, futures=False):
        self.id, self.title = test_id, title
        self.ex = ex = Exchange(MARGIN_POLICY)
        self.w = World(ex)
        self.checks, self.blocked, self.notes = [], [], []
        self.extra = {}
        self.batch_id, self.batch_ids, self.rolled = None, [], []
        self.orders = {}
        ex.open(balance_sheet(), at_h=-48)
        for s, op in OPERATORS.items():
            ex.charter_institution(-48, op, s, ["operator"])
        ex.charter_institution(-48, CLEARING, HUB, ["clearing"], operator=MARKET_OP)
        ex.register_price_source(-48, SOURCE, "Mars")
        ex.list_instrument(-48, Equity(symbol=SYMBOL, venue=MARKET, base_asset=SHARES))
        for s, sym in LOCAL_BOOK.items():
            ex.list_instrument(-48, Equity(symbol=sym, venue=s, base_asset=SHARES))
        ex.list_instrument(-48, Future(symbol=FUTURE, venue=HUB, underlying="MOI",
                                       multiplier=D(100), maturity_h=300, price_source=SOURCE))
        # Pre-hour-0 sessions (allowed from hour -168): every operator pair, so a
        # settlement leg goes straight from the seller's home to the buyer's, plus
        # each remote operator to the clearing house when futures are in play.
        ops = list(OPERATORS.values())
        pairs = [(a, b) for i, a in enumerate(ops) for b in ops[i + 1:]]
        if futures:
            pairs += [(op, CLEARING) for s, op in OPERATORS.items() if s != HUB]
        for a, b in pairs:
            self.w.at(-48, f"session {a}-{b}", lambda t, a=a, b=b: self.w.open_session(t, a, b))
        self.w.run(until=-0.001)
        self.before = balances(ex)

    # ------------------------------------------------------------ checking
    def check(self, name, expected, actual, ok=None):
        ok = (expected == actual) if ok is None else bool(ok)
        self.checks.append({"name": name, "expected": _s(expected), "actual": _s(actual),
                            "pass": ok})
        return ok

    def attempt(self, t, label, fn):
        """Run a command that the rules must reject; record the rejection as evidence."""
        try:
            fn()
        except ExchangeError as exc:
            self.blocked.append({"t": t, "attempt": label, "rejected": True, "error": str(exc)})
            self.ex.annotate(t, None, f"REJECTED: {label} ({exc})")
            return True
        self.blocked.append({"t": t, "attempt": label, "rejected": False, "error": None})
        return False

    # -------------------------------------------------------- batch market
    def batch_timing(self, t):
        rows = []
        for home in sorted({h for h in HOME.values() if h != MARKET}):
            route = self.w.route_from(OPERATORS[home], MARKET_OP)
            ev = self.w.net.evaluate_route(route, t / 24)
            longest = max(h["ta"] - h["te"] for h in ev["hops"]) * 24
            rows.append({"from": home, "route": route, "one_way_h": ev["light_min"] / 60,
                         "retry_allowance_h": 2 * longest + 1})
        slow = max(rows, key=lambda r: r["one_way_h"])
        retry = BATCH_HOP_RETRIES * max(r["retry_allowance_h"] for r in rows)
        return {"rule": f"close = open + 1.1 x slowest one-way + {BATCH_HOP_RETRIES} hop retries x R_h",
                "slowest_from": slow["from"], "slowest_one_way_h": slow["one_way_h"],
                "hop_retries": BATCH_HOP_RETRIES, "retry_allowance_h": retry,
                "duration_h": 1.1 * slow["one_way_h"] + retry, "routes": rows}

    def open_batch(self, t):
        def go(tt):
            timing = self.batch_timing(tt)
            bid = self.ex.open_batch(tt, SYMBOL, MARKET, tt + timing["duration_h"], timing,
                                     note=f"closes in {timing['duration_h']:.2f} h")
            self.batch_id = bid
            self.batch_ids.append(bid)
            for oid in list(self.rolled):
                if self.ex.state.batch_orders[oid].status in (BatchOrderStatus.ROLLED,
                                                              BatchOrderStatus.RESERVED):
                    self.ex.accept_batch_order(tt, oid, bid, note="rolled order joins with its original limit")
                    self.rolled.remove(oid)
            self.w.at(tt + timing["duration_h"], f"{bid} closes", self._execute, bid)
        self.w.at(t, "open batch", go)

    def submit(self, t, account, side, qty, limit, key):
        """Client sends an order to its home operator (1 s local access)."""
        home = HOME[account]
        op = OPERATORS[home]
        self.orders[key] = None

        def client(tt):
            self.ex.send_message(tt, account, op, "local", "client", "order",
                                 {"side": side, "qty": qty, "limit": limit})
            self.w.at(tt + SEC_H, f"{key} reaches {op}", at_operator)

        def at_operator(tt):
            oid = self.ex.reserve_batch_order(
                tt, account, SYMBOL, side, qty, limit,
                note=f"{op} stamps the order (priority time h {tt:.4f}) and locks collateral at {home}")
            self.orders[key] = oid
            self.w.send(tt, op, MARKET_OP, "batch_order", {"order": oid},
                        lambda ta: self._at_market(ta, oid), references=[oid])
        self.w.at(t, f"{key} client sends", client)

    def _at_market(self, ta, oid):
        o = self.ex.state.batch_orders[oid]
        if o.status is BatchOrderStatus.CANCELLED:
            self.ex.annotate(ta, MARKET_OP, f"{oid} arrived after its cancel; ignored")
            return
        b = self.ex.state.batches.get(self.batch_id or "")
        if b is None:
            self.ex.annotate(ta, MARKET_OP, f"{oid} arrived with no batch open; waits for the next one")
            self.rolled.append(oid)
            return
        if self.ex.accept_batch_order(ta, oid, self.batch_id) == "rolled":
            self.rolled.append(oid)

    def cancel(self, t, key):
        """Client asks its operator to cancel; the request must reach the market."""
        def client(tt):
            oid = self.orders[key]
            o = self.ex.state.batch_orders[oid]
            op = OPERATORS[o.home]
            self.ex.send_message(tt, o.account, op, "local", "client", "cancel", {"order": oid})
            self.w.at(tt + SEC_H, f"cancel {key} to market",
                      lambda t2: self.w.send(t2, op, MARKET_OP, "cancel", {"order": oid},
                                             lambda ta: self._cancel_at_market(ta, oid),
                                             references=[oid]))
        self.w.at(t, f"cancel {key}", client)

    def _cancel_at_market(self, ta, oid):
        res = self.ex.cancel_batch_order(ta, oid)
        self.extra.setdefault("cancel_results", {})[oid] = {"t": ta, "result": res}
        if res == "cancelled":
            o = self.ex.state.batch_orders[oid]
            self.w.send(ta, MARKET_OP, OPERATORS[o.home], "cancel_confirmed", {"order": oid},
                        lambda t2: self.ex.settle_batch_leg(t2, oid, note="cancel confirmed: lock released"),
                        references=[oid])

    def _execute(self, t, bid):
        self.ex.execute_batch(t, bid)
        for oid in self.ex.state.batches[bid].order_ids:
            o = self.ex.state.batch_orders[oid]
            self.w.send(t, MARKET_OP, OPERATORS[o.home], "batch_result", {"batch": bid, "order": oid},
                        lambda ta, oid=oid: self._apply_result(ta, oid), references=[oid])

    def _apply_result(self, ta, oid):
        data = self.ex.settle_batch_leg(ta, oid, note="batch result applied at home")
        src = OPERATORS[self.ex.state.batch_orders[oid].home]
        for leg in data["legs"]:
            if leg["transfer_id"]:
                self.ship(ta, leg["transfer_id"], src, OPERATORS[leg["to_settlement"]])

    # ------------------------------------------------------------ transfers
    def ship(self, t, xfr, src_op, dst_op, kind="transfer"):
        """Send a transfer instruction; the destination validates and replies with status.

        With ``reconcile_after_rt``, a source that has no status reply one round
        trip + 1 h after sending queries the destination with the same
        transaction id. That is reconciliation of the existing transfer, never a
        new one.
        """
        mid = self.w.send(t, src_op, dst_op, kind, {"transfer": xfr},
                          lambda ta: self._transfer_arrived(ta, xfr, src_op, dst_op),
                          references=[xfr])
        if self.extra.get("reconcile_after_rt"):
            route = self.w.route_from(src_op, dst_op)
            rt = (self.w.route_timing_h(route, t)
                  + self.w.route_timing_h(list(reversed(route)), t))
            self.w.at(t + rt + 1.0, f"reconcile {xfr}", self._query, xfr, src_op, dst_op)
        return mid

    def _query(self, tq, xfr, src_op, dst_op):
        if self.ex.state.transfers[xfr].source_confirmed_h is not None:
            return
        self.ex.annotate(tq, src_op, f"no status for {xfr} after one round trip + 1 h: status query "
                                     "(same transaction id, never a new transfer)")
        self.w.send(tq, src_op, dst_op, "status_query", {"transfer": xfr},
                    lambda ta: self._transfer_arrived(ta, xfr, src_op, dst_op), references=[xfr])

    def _transfer_arrived(self, ta, xfr, src_op, dst_op):
        res = self.ex.deliver_transfer(ta, xfr, note="destination validates the locked commitment")
        self.extra.setdefault("deliveries", []).append({"transfer": xfr, "t": ta, "result": res})
        on = self.extra.get("on_final", {}).get(xfr)
        if on and res == "completed":
            on(ta)
        self.w.send(ta, dst_op, src_op, "transfer_status", {"transfer": xfr, "result": res},
                    lambda tb: self.ex.confirm_transfer(tb, xfr, note="source reconciles to the destination record"),
                    references=[xfr])

    # -------------------------------------------------------------- futures
    def fund_guarantee(self, t, amounts):
        """Contributions after hour 0. Cash held away from the hub travels there first."""
        def contribute(tt, acct, amt):
            self.ex.contribute_guarantee(tt, acct, CLEARING, amt,
                                         note="pre-funded guarantee: real cash, encumbered in advance")
            self.extra["fund_ready_h"] = max(self.extra.get("fund_ready_h", 0.0), tt)
        for acct, amt in amounts:
            if HOME[acct] == HUB:
                self.w.at(t, f"fund guarantee {acct}", contribute, acct, amt)
            else:
                self.move_margin_to_clearing(t, acct, amt,
                                             on_arrive=lambda ta, a=acct, m=amt: contribute(ta, a, m),
                                             kind="guarantee_transfer")

    def move_margin_to_clearing(self, t, account, amount, on_arrive=None, kind="margin_transfer"):
        home = HOME[account]
        if home == HUB:  # already at the clearing house: nothing to move
            if on_arrive:
                self.w.at(t, f"{account} cash already at {HUB}", on_arrive)
            return

        def go(tt):
            xfr = self.ex.initiate_transfer(tt, account, CASH, amount, home, HUB,
                                            note=f"{OPERATORS[home]} debits {home}; cash travels to {CLEARING}")
            self.extra.setdefault("margin_transfers" if kind == "margin_transfer" else "fund_transfers", []).append(xfr)

            def arrived(ta):
                self.ex.deliver_transfer(ta, xfr, note=f"cash validated at {HUB}")
                if on_arrive:
                    on_arrive(ta)
                self.w.send(ta, CLEARING, OPERATORS[home], "transfer_status",
                            {"transfer": xfr}, lambda tb: self.ex.confirm_transfer(tb, xfr),
                            references=[xfr])
            self.w.send(tt, OPERATORS[home], CLEARING, "margin_transfer", {"transfer": xfr},
                        arrived, references=[xfr])
        self.w.at(t, f"{account} ships margin", go)

    def initial_margin(self, account, qty, price):
        probe = Position("probe", FUTURE, HUB, account, "x", D(qty), D(price), 0.0)
        return MARGIN_POLICY.required(self.ex.state.instruments[FUTURE], probe, Party.LONG, D(price))

    def price_feed(self, path, start_h=12.0, interval=12.0):
        """MOI observations released at Mars; Mars Exchange forwards each mark to the clearing house."""
        for k, price in enumerate(path):
            t = start_h + k * interval

            def release(tt, price=price):
                obs = self.ex.release_observation(tt, SOURCE, "MOI", price)
                self.w.at(tt + SEC_H, "MOI to Mars Exchange",
                          lambda t2: self.w.send(t2, OPERATORS["Mars"], CLEARING if HUB != "Mars" else OPERATORS["Mars"], "mark",
                                                 {"observation": obs, "price": str(price)},
                                                 lambda ta: self._on_mark(ta, obs),
                                                 references=[obs]))
            self.w.at(t, f"MOI release {price}", release)

    def _on_mark(self, ta, obs_id):
        pid = self.extra.get("position")
        if not pid:
            return
        ex, pos = self.ex, self.ex.state.positions[pid]
        if pos.state is not PositionState.OPEN:
            return
        obs = ex.state.observations[obs_id]
        inst = ex.state.instruments[FUTURE]
        if obs.released_h >= inst.maturity_h:
            ex.settle_position(ta, pid, [obs_id], note="maturity: first scheduled MOI at or after h 300")
            self._series(ta, obs, pos, "settled")
            self._pay_winner_home(ta, pid)
            return
        ex.mark_position(ta, pid, obs_id)
        defaulter = self.extra.get("default_pending")
        if defaulter and pos.party_of(defaulter).value in pos.margin_calls:
            ex.declare_default(ta, pid, defaulter,
                               note="margin deadline passed without a validated top-up; "
                                    "closed at the next authoritative MOI")
            self._series(ta, obs, pos, "default")
            self._pay_winner_home(ta, pid)
            return
        self._series(ta, obs, pos, "marked")
        for party, amount in pos.margin_calls.items():
            acct = pos.long if party == "long" else pos.short
            calls = self.extra.setdefault("calls", [])
            if any(c["account"] == acct and c["open"] for c in calls):
                continue
            window = MARGIN_POLICY.window(acct)
            call = {"account": acct, "amount": amount, "issued_h": ta, "deadline_h": ta + window,
                    "open": True, "met_h": None, "price": str(obs.price)}
            calls.append(call)
            ex.annotate(ta, CLEARING, f"MARGIN CALL {acct} {amount} (deadline h {ta + window:.2f})")
            self.w.send(ta, CLEARING, OPERATORS[HOME[acct]], "margin_call",
                        {"position": pid, "amount": amount},
                        lambda tc, call=call: self._call_received(tc, call), references=[pid])
            self.w.at(ta + window, f"margin deadline {acct}", self._deadline, call)

    def _call_received(self, tc, call):
        if not self.extra.get("respond_to_calls", True):
            self.ex.annotate(tc, call["account"], "margin call received; participant does not respond")
            return
        pid = self.extra["position"]
        acct, amount = call["account"], D(call["amount"])
        if HOME[acct] == HUB:
            self.ex.post_margin(tc + 2 * SEC_H, pid, acct, amount)
            call["open"], call["met_h"] = False, tc + 2 * SEC_H
            return

        def landed(ta):
            self.ex.post_margin(ta, pid, acct, amount, note=f"top-up validated at {CLEARING}")
            call["met_h"], call["open"] = ta, False
        self.move_margin_to_clearing(tc + SEC_H, acct, amount, on_arrive=landed)

    def _deadline(self, t, call):
        pos = self.ex.state.positions[self.extra["position"]]
        if call["met_h"] is not None and call["met_h"] <= t:
            return  # top-up validated in time
        if pos.state is PositionState.OPEN and pos.party_of(call["account"]).value in pos.margin_calls:
            self.extra["default_pending"] = call["account"]
            self.ex.annotate(t, CLEARING, f"margin deadline passed for {call['account']}: "
                                          "default at the next MOI observation")

    def _series(self, ta, obs, pos, event):
        inst = self.ex.state.instruments[FUTURE]
        led = self.ex.state.ledger
        row = {"t": ta, "observed_h": obs.released_h, "price": str(obs.price), "event": event}
        for party in ("long", "short"):
            acct = pos.long if party == "long" else pos.short
            p = Party(party)
            posted = sum((led.encumbrances[i].original for i in pos.margin[party]), D(0))
            pnl = inst.long_payoff(pos.quantity, pos.entry_price, obs.price)
            pnl = pnl if party == "long" else -pnl
            row[party] = {"account": acct, "pnl": str(pnl), "posted": str(posted),
                          "maintenance": str(MARGIN_POLICY.maintenance(inst, pos, p, obs.price)),
                          "initial": str(MARGIN_POLICY.required(inst, pos, p, obs.price)),
                          "call": pos.margin_calls.get(party)}
        self.extra.setdefault("series", []).append(row)

    def _pay_winner_home(self, ta, pid):
        pos = self.ex.state.positions[pid]
        if not pos.winner or HOME[pos.winner] == HUB or not pos.paid:
            return
        home = HOME[pos.winner]
        xfr = self.ex.initiate_transfer(ta, pos.winner, CASH, pos.paid, HUB, home, reference=pid,
                                        note="payout travels to the winner's home to become spendable")
        self.ship(ta, xfr, CLEARING, OPERATORS[home])


# ======================================================================= helpers

def _s(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, float):
        return round(v, 6)
    if isinstance(v, (list, tuple)):
        return [_s(x) for x in v]
    if isinstance(v, dict):
        return {k: _s(x) for k, x in v.items()}
    return v


def balances(ex):
    """Per-principal view and system totals, for before/after panels."""
    led, st = ex.state.ledger, ex.state
    rows = {}
    for (owner, asset, settlement), bal in sorted(led.holdings().items()):
        r = rows.setdefault(owner, {"principal": owner,
                                    "home": st.principals[owner].settlement, "holdings": []})
        margin = sum((e.amount for e in led.active_encumbrances()
                      if e.key == (owner, asset, settlement) and e.purpose.value == "margin"), D(0))
        guarantee = sum((e.amount for e in led.active_encumbrances()
                         if e.key == (owner, asset, settlement) and e.purpose.value == "guarantee"), D(0))
        rows[owner]["holdings"].append({
            "asset": "cash" if asset == CASH else "shares", "settlement": settlement,
            "balance": str(bal), "available": str(led.available(owner, asset, settlement)),
            "locked": str(led.encumbered(owner, asset, settlement) - margin - guarantee),
            "collateral": str(margin + guarantee)})
    totals = {}
    for asset, label in ((CASH, "cash"), (SHARES, "shares")):
        on_ledger, transit = led.total_supply(asset), st.in_transit(asset)
        opening = st.opening_supply.get(asset, D(0))
        totals[label] = {"on_ledgers": str(on_ledger), "in_transit": str(transit),
                         "total": str(on_ledger + transit), "opening": str(opening),
                         "locked": str(led.encumbered_total(asset)),
                         "conserved": on_ledger + transit == opening}
    return {"principals": list(rows.values()), "totals": totals}


def tiebreak_order(batch_id, order_ids):
    return sorted(order_ids, key=lambda o: hashlib.sha256(f"{batch_id}:{o}".encode()).hexdigest())


def held(ex, account, asset, settlement=None):
    return ex.state.ledger.balance(account, asset, settlement or HOME[account])


def avail(ex, account, asset, settlement=None):
    return ex.state.ledger.available(account, asset, settlement or HOME[account])
