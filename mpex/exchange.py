"""The central exchange: one place holding the complete state of the system.

``Exchange`` is the omniscient simulation record, not a single physical
institution. Every holding still sits at a settlement, every institution at
a gateway, and nothing becomes usable remotely until a transfer or message
says so. Physical locality is enforced by the data model, not by pretending
the exchange runs in one place.

Design
------
* **Commands** (public methods such as ``submit_order``) validate a request
  against the current state and record one or more events.
* **Events** are appended to the ``Journal``. Each one is applied to
  ``ExchangeState`` by a handler (``_on_<event type>``). If a handler fails,
  the state rolls back and nothing is journaled, so state and journal never
  disagree.
* **Replay**: ``state_at(t)`` rebuilds the exchange as it was at hour ``t``
  from the journal. ``snapshot()`` gives a JSON-safe view for traces.
* **Invariants**: ``check_invariants()`` verifies conservation of every
  asset, encumbrance consistency, and that no obligation is left unbacked.
"""

import copy
import json
from decimal import Decimal
from statistics import mean

from .assets import to_quantity, validate_asset
from .batch import Batch, BatchOrder, BatchOrderStatus, BatchStatus, run_auction
from .balance_sheet import OpeningBalanceSheet
from .comms import (Launch, Message, MessageStatus, Packet, PacketKind, Service,
                    Session, SessionState, TrafficClass, packets_needed, validate_route)
from .constants import (EARLIEST_SESSION_SETUP_H, FIRST_FINANCIAL_ACTION_H,
                        MAX_INSTITUTIONS, NEODOLLAR, validate_node, validate_settlement)
from .errors import (InvalidState, TimeOrderError, ValidationError)
from .instruments import (Equity, Future, Instrument, ObservationRule, Option,
                          instrument_from_data, instrument_to_data)
from .journal import Event, EventType, Journal
from .ledger import EncumbrancePurpose
from .observations import PriceObservation
from .positions import MarginPolicy, Party, Position, PositionState
from .principals import Account, Institution, InstitutionRole, Principal, PriceSource
from .state import ExchangeState
from .trading import Order, OrderBook, OrderStatus, Side, Trade, TradeStatus
from .transfers import Transfer, TransferStatus

ZERO = Decimal(0)


def _d(x) -> Decimal:
    return Decimal(str(x))


class Exchange:
    def __init__(self, margin_policy: MarginPolicy | None = None) -> None:
        self.margin_policy = margin_policy or MarginPolicy()
        self.state = ExchangeState()
        self.journal = Journal()

    # =====================================================================
    # Construction and replay
    # =====================================================================
    @classmethod
    def from_events(cls, events, margin_policy: MarginPolicy | None = None) -> "Exchange":
        ex = cls(margin_policy)
        for event in events:
            ex._apply(event)
            ex.journal.append(event)
        return ex

    def state_at(self, time_h: float) -> "Exchange":
        """A separate Exchange rebuilt from every event at or before ``time_h``."""
        return Exchange.from_events(self.journal.until(time_h), self.margin_policy)

    @property
    def now_h(self) -> float | None:
        return self.state.clock_h

    # =====================================================================
    # Setup
    # =====================================================================
    def open(self, balance_sheet: OpeningBalanceSheet, at_h: float = 0.0) -> Event:
        """Create every opening account with its hour-0 holdings.

        ``at_h`` may be earlier than 0 (down to hour -168) so communication
        sessions can be set up before trading; no financial action is
        allowed before hour 0 either way.
        """
        if len(self.journal):
            raise InvalidState("exchange is already open")
        if not EARLIEST_SESSION_SETUP_H <= at_h <= FIRST_FINANCIAL_ACTION_H:
            raise TimeOrderError("the exchange opens between hour -168 and hour 0")
        balance_sheet.validate()
        return self._record(EventType.EXCHANGE_OPENED, at_h, None,
                            {"accounts": balance_sheet.to_rows()})

    def charter_institution(self, at_h: float, name: str, settlement: str,
                            roles, operator: str | None = None) -> Event:
        self._require_open()
        roles = sorted({InstitutionRole(r).value for r in roles})
        if not roles:
            raise ValidationError("an institution needs at least one role")
        self._require_new_principal(name)
        validate_settlement(settlement)
        n = sum(isinstance(p, Institution) for p in self.state.principals.values())
        if n >= MAX_INSTITUTIONS:
            raise ValidationError(f"at most {MAX_INSTITUTIONS} institutions may be chartered")
        service_roles = {InstitutionRole.CLEARING.value, InstitutionRole.SETTLEMENT.value}
        if service_roles & set(roles) and InstitutionRole.OPERATOR.value not in roles:
            op = self.state.principals.get(operator or "")
            if not (isinstance(op, Institution)
                    and InstitutionRole.OPERATOR.value in op.roles):
                raise ValidationError(
                    "a clearing or settlement service must name its operator institution")
            if op.settlement != settlement:
                raise ValidationError("services sit at their operator's settlement gateway")
        return self._record(EventType.INSTITUTION_CHARTERED, at_h, name,
                            {"name": name, "settlement": settlement, "roles": roles,
                             "operator": operator})

    def register_price_source(self, at_h: float, name: str, settlement: str) -> Event:
        self._require_open()
        self._require_new_principal(name)
        validate_settlement(settlement)
        return self._record(EventType.PRICE_SOURCE_REGISTERED, at_h, name,
                            {"name": name, "settlement": settlement})

    def list_instrument(self, at_h: float, instrument: Instrument) -> Event:
        self._require_open()
        instrument.validate()
        if instrument.symbol in self.state.instruments:
            raise ValidationError(f"{instrument.symbol} is already listed")
        if not any(isinstance(p, Institution) and p.settlement == instrument.venue
                   and p.may_use_backbone for p in self.state.principals.values()):
            raise ValidationError(
                f"no operator or clearing service at {instrument.venue} to hold the ledger")
        source = getattr(instrument, "price_source", None)
        if source is not None and not isinstance(self.state.principals.get(source), PriceSource):
            raise ValidationError(f"unknown price source {source!r}")
        return self._record(EventType.INSTRUMENT_LISTED, at_h, None,
                            instrument_to_data(instrument))

    # =====================================================================
    # Orders and trades (spot equity, delivery versus payment at the venue)
    # =====================================================================
    def submit_order(self, at_h: float, account: str, symbol: str, side, quantity,
                     limit_price, message_id: str | None = None,
                     note: str | None = None) -> tuple[str, list[str]]:
        """Accept a limit order, encumber its collateral, and match it.

        Returns the order id and the ids of any trades it executed.
        """
        self._require_financial(at_h)
        side = Side(side)
        instrument = self._instrument(symbol)
        if not isinstance(instrument, Equity):
            raise ValidationError(f"{symbol}: only equities trade through the order book")
        self._holder(account)
        qty = to_quantity(instrument.base_asset, quantity)
        price = to_quantity(instrument.quote_asset, limit_price)
        if qty <= 0 or price <= 0:
            raise ValidationError("quantity and limit price must be positive")
        asset, amount = self._order_collateral(instrument, side, qty, price)
        available = self.state.ledger.available(account, asset, instrument.venue)
        if available < amount:
            raise ValidationError(
                f"{account} needs {amount} {asset} free at {instrument.venue}, has {available}")

        fills = self.state.books[symbol].match(account, side, qty, price)
        order_id = self._new_id("ORD")
        self._record(EventType.ORDER_ACCEPTED, at_h, account, {
            "order_id": order_id, "encumbrance_id": self._new_id("ENC"),
            "account": account, "symbol": symbol, "side": side.value,
            "quantity": str(qty), "limit_price": str(price), "message_id": message_id,
        }, note)
        trade_ids = []
        for fill in fills:
            trade_id = self._new_id("TRD")
            buy, sell = ((order_id, fill.resting_order_id) if side is Side.BUY
                         else (fill.resting_order_id, order_id))
            self._record(EventType.TRADE_EXECUTED, at_h, instrument.venue, {
                "trade_id": trade_id, "symbol": symbol, "buy_order": buy,
                "sell_order": sell, "quantity": str(fill.quantity), "price": str(fill.price)})
            trade_ids.append(trade_id)
        return order_id, trade_ids

    def cancel_order(self, at_h: float, order_id: str, note: str | None = None) -> Event:
        self._require_financial(at_h)
        order = self._order(order_id)
        if not order.status.resting:
            raise InvalidState(f"{order_id} is {order.status.value}")
        return self._record(EventType.ORDER_CANCELLED, at_h, order.account,
                            {"order_id": order_id}, note)

    # =====================================================================
    # Price-dependent positions (futures, options)
    # =====================================================================
    def open_position(self, at_h: float, symbol: str, long: str, short: str, quantity,
                      entry_price, long_margin=0, short_margin=0,
                      reference_price=None, note: str | None = None) -> str:
        """Record a bilateral contract at the instrument's clearing venue.

        ``entry_price`` is the futures price, or the option premium per unit
        (paid by the long side to the short side at opening). Each side's
        posted margin must meet the margin rule at ``reference_price``
        (default: entry price for futures, latest observation for options).
        """
        self._require_financial(at_h)
        instrument = self._instrument(symbol)
        if not isinstance(instrument, (Future, Option)):
            raise ValidationError(f"{symbol} is not a price-dependent contract")
        if long == short:
            raise ValidationError("long and short must differ")
        self._holder(long)
        self._holder(short)
        if at_h >= instrument.maturity_h:
            raise ValidationError(f"{symbol} matured at hour {instrument.maturity_h}")
        qty, entry = _d(quantity), _d(entry_price)
        lm, sm = to_quantity(NEODOLLAR, long_margin), to_quantity(NEODOLLAR, short_margin)
        if qty <= 0 or entry < 0 or lm < 0 or sm < 0:
            raise ValidationError("quantity must be positive; prices and margins non-negative")
        if reference_price is None:
            if isinstance(instrument, Future):
                reference_price = entry
            else:
                latest = self._latest_observation(instrument, at_h)
                if latest is None:
                    raise ValidationError(f"no observation of {instrument.underlying} yet; "
                                          "pass reference_price")
                reference_price = latest.price
        ref = _d(reference_price)

        probe = Position("probe", symbol, instrument.venue, long, short, qty, entry, at_h)
        for party, posted in ((Party.LONG, lm), (Party.SHORT, sm)):
            need = self.margin_policy.required(instrument, probe, party, ref)
            if posted < need:
                raise ValidationError(
                    f"{probe.account_of(party)} must post {need} {NEODOLLAR} margin, offered {posted}")
        premium = qty * instrument.multiplier * entry if isinstance(instrument, Option) else ZERO
        venue = instrument.venue
        ledger = self.state.ledger
        if ledger.available(long, NEODOLLAR, venue) < lm + premium:
            raise ValidationError(f"{long} lacks {lm + premium} {NEODOLLAR} free at {venue}")
        if ledger.available(short, NEODOLLAR, venue) + premium < sm:
            raise ValidationError(f"{short} lacks {sm} {NEODOLLAR} free at {venue}")

        position_id = self._new_id("POS")
        self._record(EventType.POSITION_OPENED, at_h, venue, {
            "position_id": position_id, "symbol": symbol, "long": long, "short": short,
            "quantity": str(qty), "entry_price": str(entry), "reference_price": str(ref),
            "premium": str(premium),
            "long_margin": str(lm), "short_margin": str(sm),
            "long_encumbrance_id": self._new_id("ENC") if lm else None,
            "short_encumbrance_id": self._new_id("ENC") if sm else None,
        }, note)
        return position_id

    def post_margin(self, at_h: float, position_id: str, account: str, amount,
                    note: str | None = None) -> str:
        self._require_financial(at_h)
        pos = self._open_position(position_id)
        party = self._party(pos, account)
        q = to_quantity(NEODOLLAR, amount)
        if q <= 0:
            raise ValidationError("margin must be positive")
        enc_id = self._new_id("ENC")
        self._record(EventType.MARGIN_POSTED, at_h, account, {
            "position_id": position_id, "party": party.value, "amount": str(q),
            "encumbrance_id": enc_id}, note)
        return enc_id

    def mark_position(self, at_h: float, position_id: str, observation_id: str,
                      note: str | None = None) -> Event:
        """Re-value margin requirements on a received observation; issues calls."""
        self._require_financial(at_h)
        pos = self._open_position(position_id)
        obs = self._observation_for(pos, observation_id, at_h)
        return self._record(EventType.POSITION_MARKED, at_h, pos.venue, {
            "position_id": position_id, "observation_id": obs.id}, note)

    def settle_position(self, at_h: float, position_id: str, observation_ids: list[str],
                        note: str | None = None) -> Event:
        """Final settlement at maturity using the observations the contract names."""
        self._require_financial(at_h)
        pos = self._open_position(position_id)
        instrument = self._instrument(pos.symbol)
        if at_h < instrument.maturity_h:
            raise ValidationError(f"{pos.symbol} matures at hour {instrument.maturity_h}")
        if not observation_ids:
            raise ValidationError("settlement needs at least one observation")
        for oid in observation_ids:
            obs = self._observation_for(pos, oid, at_h)
            if not pos.opened_h < obs.released_h <= instrument.maturity_h:
                raise ValidationError(f"{oid} is outside ({pos.opened_h}, {instrument.maturity_h}]")
        return self._record(EventType.POSITION_SETTLED, at_h, pos.venue, {
            "position_id": position_id, "observation_ids": list(observation_ids)}, note)

    def declare_default(self, at_h: float, position_id: str, account: str,
                        note: str | None = None) -> Event:
        """Close out a side with an unmet margin call at the last mark."""
        self._require_financial(at_h)
        pos = self._open_position(position_id)
        party = self._party(pos, account)
        if party.value not in pos.margin_calls:
            raise InvalidState(f"{account} has no outstanding margin call on {position_id}")
        return self._record(EventType.POSITION_DEFAULTED, at_h, pos.venue, {
            "position_id": position_id, "defaulter": account}, note)

    # =====================================================================
    # Transfers between settlements
    # =====================================================================
    def initiate_transfer(self, at_h: float, owner: str, asset: str, amount,
                          from_settlement: str, to_settlement: str,
                          to_owner: str | None = None, stated_value=None,
                          reference: str | None = None, message_id: str | None = None,
                          note: str | None = None) -> str:
        self._require_financial(at_h)
        self._holder(owner)
        to_owner = to_owner or owner
        self._holder(to_owner)
        validate_asset(asset)
        validate_settlement(from_settlement)
        validate_settlement(to_settlement)
        q = to_quantity(asset, amount)
        if q <= 0:
            raise ValidationError("transfer amount must be positive")
        if self.state.ledger.available(owner, asset, from_settlement) < q:
            raise ValidationError(f"{owner} lacks {q} {asset} free at {from_settlement}")
        transfer_id = self._new_id("XFR")
        self._record(EventType.TRANSFER_INITIATED, at_h, owner, {
            "transfer_id": transfer_id, "owner": owner, "to_owner": to_owner,
            "asset": asset, "amount": str(q), "from_settlement": from_settlement,
            "to_settlement": to_settlement, "reference": reference,
            "message_id": message_id,
            "stated_value": None if stated_value is None else str(_d(stated_value))}, note)
        return transfer_id

    def complete_transfer(self, at_h: float, transfer_id: str, note: str | None = None) -> Event:
        self._require_financial(at_h)
        t = self._in_transit(transfer_id)
        return self._record(EventType.TRANSFER_COMPLETED, at_h, t.to_owner,
                            {"transfer_id": transfer_id}, note)

    def return_transfer(self, at_h: float, transfer_id: str, reason: str,
                        note: str | None = None) -> Event:
        self._require_financial(at_h)
        t = self._in_transit(transfer_id)
        return self._record(EventType.TRANSFER_RETURNED, at_h, t.owner,
                            {"transfer_id": transfer_id, "reason": reason}, note)

    def deliver_transfer(self, at_h: float, transfer_id: str, note: str | None = None) -> str:
        """Destination validates a transfer instruction, at most once per transaction id.

        The first delivery completes the transfer (destination finality: the
        value is usable at once). A repeat of the same transaction id, from a
        resubmission or a duplicate copy, changes nothing and is recorded.
        Returns "completed" or "duplicate".
        """
        self._require_financial(at_h)
        t = self._transfer(transfer_id)
        if t.status is TransferStatus.IN_TRANSIT:
            self._record(EventType.TRANSFER_COMPLETED, at_h, t.to_owner,
                         {"transfer_id": transfer_id}, note)
            return "completed"
        if t.status is TransferStatus.COMPLETED:
            self._record(EventType.TRANSFER_DUPLICATE_IGNORED, at_h, t.to_owner,
                         {"transfer_id": transfer_id, "stage": "delivery",
                          "first_completed_h": t.completed_h}, note)
            return "duplicate"
        raise InvalidState(f"{transfer_id} is {t.status.value}")

    def confirm_transfer(self, at_h: float, transfer_id: str, note: str | None = None) -> str:
        """The source learns that the destination validated the transfer (reconciliation).

        Until then the source record stays pending; the value is never usable
        at the source in the meantime. Returns "confirmed" or "duplicate".
        """
        self._require_financial(at_h)
        t = self._transfer(transfer_id)
        if t.status is not TransferStatus.COMPLETED:
            raise InvalidState(f"{transfer_id} is {t.status.value}; the destination has not validated it")
        if t.source_confirmed_h is not None:
            self._record(EventType.TRANSFER_DUPLICATE_IGNORED, at_h, t.owner,
                         {"transfer_id": transfer_id, "stage": "confirmation",
                          "first_completed_h": t.source_confirmed_h}, note)
            return "duplicate"
        self._record(EventType.TRANSFER_CONFIRMED, at_h, t.owner,
                     {"transfer_id": transfer_id}, note)
        return "confirmed"

    # =====================================================================
    # Global batch market (cross-settlement equity trading, batch.py)
    # =====================================================================
    def open_batch(self, at_h: float, symbol: str, market: str, closes_h: float,
                   timing: dict | None = None, note: str | None = None) -> str:
        self._require_financial(at_h)
        inst = self._instrument(symbol)
        if not isinstance(inst, Equity):
            raise ValidationError(f"{symbol}: only equities trade in the batch market")
        validate_settlement(market)
        if not any(isinstance(p, Institution) and p.settlement == market
                   and InstitutionRole.OPERATOR.value in p.roles
                   for p in self.state.principals.values()):
            raise ValidationError(f"no operator at {market} to hold the batch")
        if closes_h <= at_h:
            raise ValidationError("a batch must close after it opens")
        if any(b.symbol == symbol and b.status is BatchStatus.OPEN
               for b in self.state.batches.values()):
            raise InvalidState(f"a {symbol} batch is already open")
        batch_id = self._new_id("BAT")
        self._record(EventType.BATCH_OPENED, at_h, market, {
            "batch_id": batch_id, "symbol": symbol, "market": market,
            "closes_h": float(closes_h), "timing": timing or {}}, note)
        return batch_id

    def reserve_batch_order(self, at_h: float, account: str, symbol: str, side, quantity,
                            limit_price, note: str | None = None) -> str:
        """Home operator stamps a client order and locks its collateral at home.

        ``at_h`` is the operator's receipt time, which becomes the order's
        priority timestamp in the batch.
        """
        self._require_financial(at_h)
        side = Side(side)
        inst = self._instrument(symbol)
        if not isinstance(inst, Equity):
            raise ValidationError(f"{symbol}: only equities trade in the batch market")
        home = self._holder(account).settlement
        qty = to_quantity(inst.base_asset, quantity)
        price = to_quantity(inst.quote_asset, limit_price)
        if qty <= 0 or price <= 0:
            raise ValidationError("quantity and limit price must be positive")
        asset, amount = self._order_collateral(inst, side, qty, price)
        available = self.state.ledger.available(account, asset, home)
        if available < amount:
            raise ValidationError(f"{account} needs {amount} {asset} free at {home}, has {available}")
        order_id = self._new_id("BOR")
        self._record(EventType.BATCH_ORDER_RESERVED, at_h, account, {
            "order_id": order_id, "encumbrance_id": self._new_id("ENC"), "account": account,
            "symbol": symbol, "side": side.value, "quantity": str(qty),
            "limit_price": str(price), "home": home}, note)
        return order_id

    def accept_batch_order(self, at_h: float, order_id: str, batch_id: str,
                           note: str | None = None) -> str:
        """The order reaches the market. Returns "accepted", or "rolled" if the batch closed."""
        self._require_financial(at_h)
        o = self._batch_order(order_id)
        if o.status not in (BatchOrderStatus.RESERVED, BatchOrderStatus.ROLLED):
            raise InvalidState(f"{order_id} is {o.status.value}")
        b = self._batch(batch_id)
        if b.symbol != o.symbol:
            raise ValidationError(f"{order_id} is for {o.symbol}, not {b.symbol}")
        if b.status is BatchStatus.OPEN and at_h <= b.closes_h:
            self._record(EventType.BATCH_ORDER_ACCEPTED, at_h, b.market,
                         {"order_id": order_id, "batch_id": batch_id}, note)
            return "accepted"
        self._record(EventType.BATCH_ORDER_ROLLED, at_h, b.market,
                     {"order_id": order_id, "batch_id": batch_id}, note)
        return "rolled"

    def cancel_batch_order(self, at_h: float, order_id: str, note: str | None = None) -> str:
        """A cancel request reaches the market. Effective only before execution.

        Returns "cancelled", or "rejected" when the batch already executed
        (the trade is binding).
        """
        self._require_financial(at_h)
        o = self._batch_order(order_id)
        if o.status in (BatchOrderStatus.RESERVED, BatchOrderStatus.ACCEPTED,
                        BatchOrderStatus.ROLLED):
            self._record(EventType.BATCH_ORDER_CANCELLED, at_h, o.account,
                         {"order_id": order_id}, note)
            return "cancelled"
        if o.status in (BatchOrderStatus.EXECUTED, BatchOrderStatus.SETTLED):
            self._record(EventType.BATCH_CANCEL_REJECTED, at_h, o.account, {
                "order_id": order_id,
                "reason": f"batch {o.batch_id} executed at hour {o.executed_h}; the trade is binding"},
                note)
            return "rejected"
        raise InvalidState(f"{order_id} is {o.status.value}")

    def execute_batch(self, at_h: float, batch_id: str, note: str | None = None) -> dict:
        """Clear a closed batch at one uniform price. Collateral stays locked at home."""
        self._require_financial(at_h)
        b = self._batch(batch_id)
        if b.status is not BatchStatus.OPEN:
            raise InvalidState(f"{batch_id} is {b.status.value}")
        if at_h < b.closes_h:
            raise ValidationError(f"{batch_id} closes at hour {b.closes_h}")
        orders = [self.state.batch_orders[i] for i in b.order_ids]
        result = run_auction(b.id, orders)
        data = {
            "batch_id": batch_id,
            "price": None if result["price"] is None else str(result["price"]),
            "allocations": [{"order_id": oid, "quantity": str(a["qty"]), "rank": a["rank"],
                             "reason": a["reason"]} for oid, a in result["allocations"].items()],
            "trades": [{"trade_id": self._new_id("TRD"), "buy_order": t["buy_order"],
                        "sell_order": t["sell_order"], "quantity": str(t["quantity"])}
                       for t in result["trades"]],
            "table": [{k: str(v) for k, v in row.items()} for row in result["table"]],
        }
        self._record(EventType.BATCH_EXECUTED, at_h, b.market, data, note)
        return data

    def settle_batch_leg(self, at_h: float, order_id: str, note: str | None = None) -> dict:
        """The order's home operator applies the batch result to its ledger.

        Filled quantity leaves the lock: to the counterparty directly when it
        lives at the same settlement, otherwise into an in-transit transfer to
        the counterparty's home. Everything else (unfilled quantity, price
        improvement, a cancelled order) is released.
        """
        self._require_financial(at_h)
        o = self._batch_order(order_id)
        if o.status not in (BatchOrderStatus.EXECUTED, BatchOrderStatus.CANCELLED):
            raise InvalidState(f"{order_id} is {o.status.value}; there is no result to apply")
        inst = self.state.instruments[o.symbol]
        legs, used = [], ZERO
        if o.status is BatchOrderStatus.EXECUTED:
            for tid in self.state.batches[o.batch_id].trade_ids:
                tr = self.state.trades[tid]
                if o.side is Side.SELL and tr.sell_order == o.id:
                    leg, asset, amount, to_owner = "shares", inst.base_asset, tr.quantity, tr.buyer
                    to_home = self.state.batch_orders[tr.buy_order].home
                elif o.side is Side.BUY and tr.buy_order == o.id:
                    leg, asset, amount, to_owner = "cash", inst.quote_asset, tr.value, tr.seller
                    to_home = self.state.batch_orders[tr.sell_order].home
                else:
                    continue
                used += amount
                legs.append({"trade_id": tid, "leg": leg, "asset": asset, "amount": str(amount),
                             "to_owner": to_owner, "to_settlement": to_home,
                             "transfer_id": None if to_home == o.home else self._new_id("XFR")})
        release = self.state.ledger.encumbrances[o.encumbrance_id].amount - used
        data = {"order_id": order_id, "legs": legs, "released": str(release)}
        self._record(EventType.BATCH_LEG_SETTLED, at_h, o.home, data, note)
        return data

    def contribute_guarantee(self, at_h: float, account: str, clearing: str, amount,
                             note: str | None = None) -> str:
        """Fund a clearing house's guarantee fund from an account (after hour 0).

        Institutions start with nothing, so the fund exists only through these
        contributions. The cash moves to the clearing house at its settlement
        and stays encumbered for default losses.
        """
        self._require_financial(at_h)
        self._holder(account)
        inst = self._principal(clearing)
        if not (isinstance(inst, Institution) and InstitutionRole.CLEARING.value in inst.roles):
            raise ValidationError(f"{clearing} is not a clearing service")
        q = to_quantity(NEODOLLAR, amount)
        if q <= 0:
            raise ValidationError("contribution must be positive")
        if self.state.ledger.available(account, NEODOLLAR, inst.settlement) < q:
            raise ValidationError(f"{account} lacks {q} {NEODOLLAR} free at {inst.settlement}")
        enc_id = self._new_id("ENC")
        self._record(EventType.GUARANTEE_CONTRIBUTED, at_h, account, {
            "account": account, "clearing": clearing, "settlement": inst.settlement,
            "amount": str(q), "encumbrance_id": enc_id}, note)
        return enc_id

    # =====================================================================
    # Price observations
    # =====================================================================
    def release_observation(self, at_h: float, source: str, underlying: str, price,
                            note: str | None = None) -> str:
        self._require_financial(at_h)
        if not isinstance(self.state.principals.get(source), PriceSource):
            raise ValidationError(f"unknown price source {source!r}")
        p = _d(price)
        if p < 0:
            raise ValidationError("price must be non-negative")
        obs_id = self._new_id("OBS")
        self._record(EventType.OBSERVATION_RELEASED, at_h, source, {
            "observation_id": obs_id, "source": source, "underlying": underlying,
            "price": str(p)}, note)
        return obs_id

    # =====================================================================
    # Communication records
    # =====================================================================
    def open_session(self, at_h: float, initiator: str, peer: str, route: list[str],
                     note: str | None = None) -> str:
        """Start a backbone session; its SYN costs one shared quota packet."""
        self._require_open()
        a, b = self._backbone_user(initiator), self._backbone_user(peer)
        route = [validate_node(n) for n in route]
        validate_route(route)
        if route[0] != a.settlement or route[-1] != b.settlement:
            raise ValidationError("route must run from the initiator's gateway to the peer's")
        self.state.quotas.check_backbone(at_h, 1)
        session_id = self._new_id("SES")
        self._record(EventType.SESSION_OPENED, at_h, initiator, {
            "session_id": session_id, "initiator": initiator, "peer": peer,
            "route": route, "syn_packet_id": self._new_id("PKT")}, note)
        return session_id

    def set_session_state(self, at_h: float, session_id: str, state,
                          note: str | None = None) -> Event:
        self._require_open()
        session = self._session(session_id)
        state = SessionState(state)
        if session.state in (SessionState.EXPIRED, SessionState.RESET):
            raise InvalidState(f"{session_id} is {session.state.value}; identities are never reused")
        return self._record(EventType.SESSION_STATE_CHANGED, at_h, session.initiator,
                            {"session_id": session_id, "state": state.value}, note)

    def send_message(self, at_h: float, sender: str, recipient: str, service, traffic_class,
                     kind: str, payload: dict | None = None, size_bytes: int | None = None,
                     session_id: str | None = None, references=(),
                     note: str | None = None) -> str:
        """Record an application message and the packets it originates.

        Enforces who may use which service, that official coordination
        uses the backbone, and both origination quotas.
        """
        self._require_open()
        service, traffic_class = Service(service), TrafficClass(traffic_class)
        payload = payload or {}
        snd, rcv = self._principal(sender), self._principal(recipient)
        if size_bytes is None:
            size_bytes = len(json.dumps(payload).encode())
        n = packets_needed(max(size_bytes, 1))

        if traffic_class is TrafficClass.OFFICIAL and service is not Service.BACKBONE:
            raise ValidationError("official inter-exchange coordination must use the backbone")
        launch_times: list[float] = []
        if service is Service.LOCAL:
            if snd.settlement != rcv.settlement:
                raise ValidationError("local access only connects parties at one settlement")
        elif service is Service.DIRECT:
            if snd.settlement == rcv.settlement:
                raise ValidationError("same-settlement traffic uses local access")
            launch_times = self.state.quotas.plan_direct(sender, at_h, n)
        else:
            if not snd.may_use_backbone:
                raise ValidationError(f"{sender} may not originate backbone traffic")
            session = self._session(session_id)
            if session.state is not SessionState.ESTABLISHED:
                raise InvalidState(f"{session_id} is {session.state.value}, not established")
            if sender not in (session.initiator, session.peer):
                raise ValidationError(f"{sender} is not an endpoint of {session_id}")
            far_end = session.route[-1] if sender == session.initiator else session.route[0]
            if rcv.settlement != far_end:
                raise ValidationError(f"{recipient} is not at the far end of {session_id}")
            self.state.quotas.check_backbone(at_h, n)

        message_id = self._new_id("MSG")
        packet_ids = [self._new_id("PKT") for _ in range(n)]
        self._record(EventType.MESSAGE_SENT, at_h, sender, {
            "message_id": message_id, "sender": sender, "recipient": recipient,
            "service": service.value, "traffic_class": traffic_class.value, "kind": kind,
            "payload": payload, "size_bytes": size_bytes, "session_id": session_id,
            "packet_ids": packet_ids, "launch_times": launch_times,
            "references": list(references)}, note)
        return message_id

    def create_transport_packet(self, at_h: float, session_id: str, kind, from_node: str,
                                retry_of: str | None = None, note: str | None = None) -> str:
        """Record a quota-exempt transport packet (SYN-ACK, ACK, hop receipt, data ack).

        An automatic endpoint retry of a data packet (``retry_of``) is also
        quota-exempt: it gets a new packet id but carries the same bytes.
        """
        self._require_open()
        kind = PacketKind(kind)
        if kind.counts_against_backbone_quota and not (
                kind is PacketKind.DATA and retry_of in self.state.packets):
            raise ValidationError(f"{kind.value} packets are originated through send_message")
        self._session(session_id)
        validate_node(from_node)
        packet_id = self._new_id("PKT")
        self._record(EventType.TRANSPORT_PACKET_CREATED, at_h, from_node, {
            "packet_id": packet_id, "session_id": session_id, "kind": kind.value,
            "from_node": from_node, "retry_of": retry_of}, note)
        return packet_id

    def record_launch(self, at_h: float, packet_id: str, from_node: str, to_node: str,
                      arrival_h: float | None, lost: bool, attempt: int = 1,
                      note: str | None = None) -> Event:
        """Record one emission computed by the transport model (emission at ``at_h``)."""
        self._require_open()
        self._packet(packet_id)
        validate_node(from_node)
        validate_node(to_node)
        if arrival_h is not None and arrival_h < at_h:
            raise ValidationError("arrival precedes emission")
        return self._record(EventType.PACKET_LAUNCHED, at_h, from_node, {
            "packet_id": packet_id, "from_node": from_node, "to_node": to_node,
            "arrival_h": arrival_h, "lost": bool(lost), "attempt": attempt}, note)

    def set_message_status(self, at_h: float, message_id: str, status,
                           note: str | None = None) -> Event:
        self._require_open()
        msg = self._message(message_id)
        status = MessageStatus(status)
        if msg.status in (MessageStatus.DELIVERED, MessageStatus.EXPIRED):
            raise InvalidState(f"{message_id} is already {msg.status.value}")
        return self._record(EventType.MESSAGE_STATUS_CHANGED, at_h, msg.recipient,
                            {"message_id": message_id, "status": status.value}, note)

    def annotate(self, at_h: float, actor: str | None, note: str) -> Event:
        """Add a trace row that changes no state (e.g. a decision or local knowledge)."""
        self._require_open()
        return self._record(EventType.NOTE, at_h, actor, {}, note)

    # =====================================================================
    # Queries
    # =====================================================================
    def holdings(self, owner: str) -> dict:
        ledger = self.state.ledger
        return {f"{a}@{s}": {"balance": str(v),
                             "available": str(ledger.available(o, a, s)),
                             "encumbered": str(ledger.encumbered(o, a, s))}
                for (o, a, s), v in sorted(ledger.holdings(owner).items())}

    def trace(self, identifier: str) -> list[Event]:
        """Every journal event touching an order, trade, position, transfer, or message."""
        return self.journal.referencing(identifier)

    def check_invariants(self) -> list[str]:
        """Return a list of violated invariants (empty when consistent)."""
        st, ledger, problems = self.state, self.state.ledger, []
        for asset in set(st.opening_supply) | ledger.assets():
            have = ledger.total_supply(asset) + st.in_transit(asset)
            want = st.opening_supply.get(asset, ZERO)
            if have != want:
                problems.append(f"{asset}: supply {have} != opening {want}")
        for (o, a, s), bal in ledger.holdings().items():
            if bal < 0:
                problems.append(f"negative balance {o} {a}@{s}: {bal}")
            if ledger.encumbered(o, a, s) > bal:
                problems.append(f"over-encumbered {o} {a}@{s}")
        for order in st.orders.values():
            if order.status.resting:
                enc = ledger.encumbrances[order.encumbrance_id]
                inst = st.instruments[order.symbol]
                _, expect = self._order_collateral(inst, order.side, order.remaining,
                                                   order.limit_price)
                if enc.amount != expect:
                    problems.append(f"{order.id}: collateral {enc.amount} != {expect}")
        live_refs = ({o.id for o in st.orders.values() if o.status.resting}
                     | {p.id for p in st.positions.values() if p.state is PositionState.OPEN}
                     | {o.id for o in st.batch_orders.values() if o.status.live}
                     | {f"FUND:{c}" for c in st.guarantee_funds})
        for o in st.batch_orders.values():
            if o.status.live:
                enc = ledger.encumbrances[o.encumbrance_id]
                if enc.amount != o.lock:
                    problems.append(f"{o.id}: lock {enc.amount} != {o.lock}")
        for tid, legs in st.trade_legs.items():
            if st.trades[tid].status is TradeStatus.SETTLED and any(
                    leg is None or leg["completed_h"] is None for leg in legs.values()):
                problems.append(f"{tid}: settled with an incomplete leg")
        for enc in ledger.active_encumbrances():
            if enc.reference not in live_refs:
                problems.append(f"{enc.id} backs closed or unknown {enc.reference}")
        for pos in st.positions.values():
            if pos.shortfall:
                problems.append(f"{pos.id}: unbacked shortfall {pos.shortfall}")
        return problems

    def snapshot(self, as_of_h: float | None = None) -> dict:
        """JSON-safe view of the complete state, for traces and reports."""
        st = self.state
        as_of_h = st.clock_h if as_of_h is None else as_of_h
        return {
            "time_h": as_of_h,
            "events": len(self.journal),
            "opening_supply": {a: str(v) for a, v in st.opening_supply.items()},
            "principals": [p.to_dict() for p in st.principals.values()],
            "ledger": st.ledger.snapshot(),
            "instruments": [instrument_to_data(i)["fields"] for i in st.instruments.values()],
            "order_books": {s: b.to_dict() for s, b in st.books.items()},
            "orders": [o.to_dict() for o in st.orders.values()],
            "trades": [t.to_dict() for t in st.trades.values()],
            "positions": [p.to_dict() for p in st.positions.values()],
            "transfers": [t.to_dict() for t in st.transfers.values()],
            "batches": [b.to_dict() for b in st.batches.values()],
            "batch_orders": [o.to_dict() for o in st.batch_orders.values()],
            "trade_legs": st.trade_legs,
            "observations": [o.to_dict() for o in st.observations.values()],
            "sessions": [s.to_dict() for s in st.sessions.values()],
            "messages": [m.to_dict() for m in st.messages.values()],
            "packets": len(st.packets),
            "quotas": st.quotas.to_dict(as_of_h) if as_of_h is not None else None,
            "metrics": st.metrics.to_dict(st.ledger, as_of_h),
            "invariant_violations": self.check_invariants(),
        }

    # =====================================================================
    # Event recording and application
    # =====================================================================
    def _record(self, etype: EventType, at_h: float, actor: str | None, data: dict,
                note: str | None = None) -> Event:
        at_h = float(at_h)
        if self.state.clock_h is not None and at_h < self.state.clock_h:
            raise TimeOrderError(f"hour {at_h} is before the last event at {self.state.clock_h}")
        event = Event(self.journal.next_seq(), at_h, etype, actor, data, note)
        backup = copy.deepcopy(self.state)
        try:
            self._apply(event)
        except Exception:
            self.state = backup
            raise
        self.journal.append(event)
        return event

    def _apply(self, event: Event) -> None:
        st = self.state
        st.metrics.advance(st.ledger, event.time_h)
        st.clock_h = event.time_h
        for key, value in event.data.items():
            if key.endswith("_id") and isinstance(value, str):
                st.ids.observe(value)
            elif key.endswith("_ids") and isinstance(value, list):
                for v in value:
                    st.ids.observe(v)
        getattr(self, f"_on_{event.type.value}")(event)
        st.metrics.observe_peaks(st.ledger, event.time_h)

    # --- setup ------------------------------------------------------------
    def _on_exchange_opened(self, e: Event) -> None:
        st = self.state
        st.opened_h = e.time_h
        for row in e.data["accounts"]:
            name, settlement = row["account"], row["settlement"]
            st.principals[name] = Account(name, settlement)
            holdings = {NEODOLLAR: Decimal(row["neodollars"])}
            holdings.update({f"SHR:{c}": Decimal(q) for c, q in row["shares"].items()})
            for asset, qty in holdings.items():
                if qty:
                    st.ledger.credit(name, asset, settlement, qty)
                    st.opening_supply[asset] = st.opening_supply.get(asset, ZERO) + qty

    def _on_institution_chartered(self, e: Event) -> None:
        d = e.data
        self.state.principals[d["name"]] = Institution(
            d["name"], d["settlement"], roles=frozenset(d["roles"]),
            operator=d["operator"], chartered_h=e.time_h)

    def _on_price_source_registered(self, e: Event) -> None:
        self.state.principals[e.data["name"]] = PriceSource(e.data["name"], e.data["settlement"])

    def _on_instrument_listed(self, e: Event) -> None:
        inst = instrument_from_data(e.data)
        self.state.instruments[inst.symbol] = inst
        if isinstance(inst, Equity):
            self.state.books[inst.symbol] = OrderBook(inst.symbol)

    # --- orders -----------------------------------------------------------
    def _on_order_accepted(self, e: Event) -> None:
        st, d = self.state, e.data
        inst = st.instruments[d["symbol"]]
        st.order_seq += 1
        order = Order(d["order_id"], d["account"], d["symbol"], Side(d["side"]),
                      Decimal(d["quantity"]), Decimal(d["limit_price"]), e.time_h,
                      st.order_seq, d["encumbrance_id"], message_id=d.get("message_id"))
        asset, amount = self._order_collateral(inst, order.side, order.quantity,
                                               order.limit_price)
        st.ledger.encumber(order.encumbrance_id, order.account, asset, inst.venue, amount,
                           EncumbrancePurpose.ORDER, order.id, e.time_h)
        st.orders[order.id] = order
        st.books[order.symbol].add(order)

    def _on_trade_executed(self, e: Event) -> None:
        st, d = self.state, e.data
        buy, sell = st.orders[d["buy_order"]], st.orders[d["sell_order"]]
        inst = st.instruments[d["symbol"]]
        qty, price = Decimal(d["quantity"]), Decimal(d["price"])
        for o in (buy, sell):
            if not o.status.resting or o.remaining < qty:
                raise InvalidState(f"{o.id} cannot fill {qty}")
        # Delivery versus payment on the venue ledger, from encumbered collateral only.
        st.ledger.pay_from(buy.encumbrance_id, sell.account, e.time_h, qty * price)
        st.ledger.pay_from(sell.encumbrance_id, buy.account, e.time_h, qty)
        for o in (buy, sell):
            o.filled += qty
            o.status = OrderStatus.FILLED if o.remaining == 0 else OrderStatus.PARTIALLY_FILLED
            if o.remaining == 0:
                o.closed_h = e.time_h
        # Price improvement frees the buyer's excess collateral.
        enc = st.ledger.encumbrances[buy.encumbrance_id]
        excess = enc.amount - buy.remaining * buy.limit_price
        if excess > 0:
            st.ledger.release(buy.encumbrance_id, e.time_h, excess)
        st.books[inst.symbol].prune()
        trade = Trade(d["trade_id"], inst.symbol, buy.id, sell.id, buy.account,
                      sell.account, price, qty, e.time_h, inst.venue,
                      TradeStatus.SETTLED, e.time_h)
        st.trades[trade.id] = trade
        st.metrics.value_settled += trade.value
        st.metrics.completed_transactions += 1

    def _on_order_cancelled(self, e: Event) -> None:
        st = self.state
        order = st.orders[e.data["order_id"]]
        enc = st.ledger.encumbrances[order.encumbrance_id]
        if enc.active:
            st.ledger.release(enc.id, e.time_h)
        order.status, order.closed_h = OrderStatus.CANCELLED, e.time_h
        st.books[order.symbol].remove(order.id)

    # --- positions --------------------------------------------------------
    def _on_position_opened(self, e: Event) -> None:
        st, d = self.state, e.data
        inst = st.instruments[d["symbol"]]
        pos = Position(d["position_id"], d["symbol"], inst.venue, d["long"], d["short"],
                       Decimal(d["quantity"]), Decimal(d["entry_price"]), e.time_h,
                       opening_price=Decimal(d["reference_price"]))
        premium = Decimal(d["premium"])
        if premium:
            st.ledger.move(pos.long, pos.short, NEODOLLAR, inst.venue, premium)
        for party in Party:
            enc_id = d[f"{party.value}_encumbrance_id"]
            if enc_id:
                st.ledger.encumber(enc_id, pos.account_of(party), NEODOLLAR, inst.venue,
                                   Decimal(d[f"{party.value}_margin"]),
                                   EncumbrancePurpose.MARGIN, pos.id, e.time_h)
                pos.margin[party.value].append(enc_id)
        st.positions[pos.id] = pos

    def _on_margin_posted(self, e: Event) -> None:
        st, d = self.state, e.data
        pos = st.positions[d["position_id"]]
        party = Party(d["party"])
        st.ledger.encumber(d["encumbrance_id"], pos.account_of(party), NEODOLLAR, pos.venue,
                           Decimal(d["amount"]), EncumbrancePurpose.MARGIN, pos.id, e.time_h)
        pos.margin[party.value].append(d["encumbrance_id"])
        self._recompute_margin_calls(pos)

    def _on_position_marked(self, e: Event) -> None:
        pos = self.state.positions[e.data["position_id"]]
        obs = self.state.observations[e.data["observation_id"]]
        pos.last_mark_price, pos.last_mark_h = obs.price, e.time_h
        self._recompute_margin_calls(pos)

    def _on_position_settled(self, e: Event) -> None:
        st = self.state
        pos = st.positions[e.data["position_id"]]
        inst = st.instruments[pos.symbol]
        obs = sorted((st.observations[o] for o in e.data["observation_ids"]),
                     key=lambda o: (o.released_h, o.seq))
        if inst.observation_rule is ObservationRule.FINAL:
            price = obs[-1].price
        else:
            price = mean(o.price for o in obs)
        pos.observation_ids = [o.id for o in obs]
        self._close_position(pos, price, e.time_h, defaulter=None)

    def _on_position_defaulted(self, e: Event) -> None:
        pos = self.state.positions[e.data["position_id"]]
        self._close_position(pos, pos.last_mark_price, e.time_h, defaulter=e.data["defaulter"])

    def _close_position(self, pos: Position, price: Decimal, at_h: float,
                        defaulter: str | None) -> None:
        st, ledger = self.state, self.state.ledger
        inst = st.instruments[pos.symbol]
        if isinstance(inst, Future):
            payoff = inst.long_payoff(pos.quantity, pos.entry_price, price)
        else:
            payoff = inst.long_payoff(pos.quantity, price)
        pos.final_price, pos.long_payoff = price, payoff
        owed = abs(payoff)
        payer = Party.SHORT if payoff > 0 else Party.LONG
        winner = pos.account_of(payer.other)
        remaining = owed
        for enc_id in pos.margin[payer.value]:
            enc = ledger.encumbrances[enc_id]
            if remaining > 0 and enc.active:
                take = min(enc.amount, remaining)
                ledger.pay_from(enc_id, winner, at_h, take)
                remaining -= take
        margin_short = remaining > 0
        for clearing, enc_ids in st.guarantee_funds.items():
            if st.principals[clearing].settlement != pos.venue:
                continue
            for enc_id in enc_ids:
                enc = ledger.encumbrances[enc_id]
                if remaining > 0 and enc.active:
                    take = min(enc.amount, remaining)
                    ledger.pay_from(enc_id, winner, at_h, take)
                    remaining -= take
                    pos.guarantee_used += take
        for ids in pos.margin.values():
            for enc_id in ids:
                if ledger.encumbrances[enc_id].active:
                    ledger.release(enc_id, at_h)
        pos.paid, pos.shortfall = owed - remaining, remaining
        pos.winner = winner if owed else None
        if margin_short and defaulter is None:
            defaulter = pos.account_of(payer)
        pos.defaulter = defaulter
        pos.state = PositionState.FUNDED_DEFAULT if defaulter else PositionState.SETTLED
        pos.margin_calls = {}
        pos.discharged_h = pos.backed_claim_h = at_h
        if not owed or st.principals[winner].settlement == pos.venue:
            pos.spendable_h = at_h
        st.metrics.value_settled += pos.quantity * inst.multiplier * pos.opening_price
        st.metrics.completed_transactions += 1

    def _recompute_margin_calls(self, pos: Position) -> None:
        if pos.last_mark_price is None:
            return
        inst = self.state.instruments[pos.symbol]
        calls = {}
        for party in Party:
            need = self.margin_policy.required(inst, pos, party, pos.last_mark_price)
            floor = self.margin_policy.maintenance(inst, pos, party, pos.last_mark_price)
            posted = sum((self.state.ledger.encumbrances[i].amount
                          for i in pos.margin[party.value]), ZERO)
            if posted < floor:
                calls[party.value] = str(need - posted)
        pos.margin_calls = calls

    # --- transfers --------------------------------------------------------
    def _on_transfer_initiated(self, e: Event) -> None:
        st, d = self.state, e.data
        t = Transfer(d["transfer_id"], d["owner"], d["to_owner"], d["asset"],
                     Decimal(d["amount"]), d["from_settlement"], d["to_settlement"], e.time_h,
                     stated_value=None if d["stated_value"] is None else Decimal(d["stated_value"]),
                     reference=d["reference"], message_id=d["message_id"])
        st.ledger.debit(t.owner, t.asset, t.from_settlement, t.amount)
        st.transfers[t.id] = t

    def _on_transfer_completed(self, e: Event) -> None:
        st = self.state
        t = st.transfers[e.data["transfer_id"]]
        st.ledger.credit(t.to_owner, t.asset, t.to_settlement, t.amount)
        t.status, t.completed_h = TransferStatus.COMPLETED, e.time_h
        if t.leg_of is not None:
            for leg in st.trade_legs[t.leg_of].values():
                if leg["transfer_id"] == t.id:
                    leg["completed_h"] = e.time_h
            self._maybe_trade_settled(t.leg_of, e.time_h)
            return
        pos = st.positions.get(t.reference or "")
        if (pos is not None and pos.spendable_h is None and t.to_owner == pos.winner
                and t.to_settlement == st.principals[pos.winner].settlement):
            pos.spendable_h = e.time_h
        value = t.stated_value
        if value is None and t.asset == NEODOLLAR:
            value = t.amount
        st.metrics.value_settled += value or ZERO
        st.metrics.completed_transactions += 1

    def _on_transfer_duplicate_ignored(self, e: Event) -> None:
        self.state.transfers[e.data["transfer_id"]].duplicates_ignored += 1

    def _on_transfer_confirmed(self, e: Event) -> None:
        self.state.transfers[e.data["transfer_id"]].source_confirmed_h = e.time_h

    # --- batch market -----------------------------------------------------
    def _on_batch_opened(self, e: Event) -> None:
        d = e.data
        self.state.batches[d["batch_id"]] = Batch(d["batch_id"], d["symbol"], d["market"],
                                                  e.time_h, d["closes_h"], d["timing"])

    def _on_batch_order_reserved(self, e: Event) -> None:
        st, d = self.state, e.data
        inst = st.instruments[d["symbol"]]
        o = BatchOrder(d["order_id"], d["account"], d["symbol"], Side(d["side"]),
                       Decimal(d["quantity"]), Decimal(d["limit_price"]), d["home"], e.time_h,
                       d["encumbrance_id"])
        asset, amount = self._order_collateral(inst, o.side, o.quantity, o.limit_price)
        st.ledger.encumber(o.encumbrance_id, o.account, asset, o.home, amount,
                           EncumbrancePurpose.ORDER, o.id, e.time_h)
        st.batch_orders[o.id] = o

    def _on_batch_order_accepted(self, e: Event) -> None:
        o = self.state.batch_orders[e.data["order_id"]]
        b = self.state.batches[e.data["batch_id"]]
        o.batch_id, o.status = b.id, BatchOrderStatus.ACCEPTED
        o.arrived_h = e.time_h if o.arrived_h is None else o.arrived_h
        b.order_ids.append(o.id)

    def _on_batch_order_rolled(self, e: Event) -> None:
        o = self.state.batch_orders[e.data["order_id"]]
        o.status = BatchOrderStatus.ROLLED
        o.missed_batches.append(e.data["batch_id"])
        o.arrived_h = e.time_h if o.arrived_h is None else o.arrived_h

    def _on_batch_order_cancelled(self, e: Event) -> None:
        o = self.state.batch_orders[e.data["order_id"]]
        b = self.state.batches.get(o.batch_id or "")
        if b is not None and b.status is BatchStatus.OPEN and o.id in b.order_ids:
            b.order_ids.remove(o.id)
        o.status = BatchOrderStatus.CANCELLED

    def _on_batch_cancel_rejected(self, e: Event) -> None:
        pass

    def _on_batch_executed(self, e: Event) -> None:
        st, d = self.state, e.data
        b = st.batches[d["batch_id"]]
        b.status, b.executed_h = BatchStatus.EXECUTED, e.time_h
        b.price = None if d["price"] is None else Decimal(d["price"])
        for a in d["allocations"]:
            o = st.batch_orders[a["order_id"]]
            o.filled, o.status, o.executed_h = Decimal(a["quantity"]), BatchOrderStatus.EXECUTED, e.time_h
        for t in d["trades"]:
            buy, sell = st.batch_orders[t["buy_order"]], st.batch_orders[t["sell_order"]]
            st.trades[t["trade_id"]] = Trade(t["trade_id"], b.symbol, buy.id, sell.id, buy.account,
                                             sell.account, b.price, Decimal(t["quantity"]),
                                             e.time_h, b.market, TradeStatus.EXECUTED)
            st.trade_legs[t["trade_id"]] = {"shares": None, "cash": None}
            b.trade_ids.append(t["trade_id"])

    def _on_batch_leg_settled(self, e: Event) -> None:
        st, d = self.state, e.data
        o = st.batch_orders[d["order_id"]]
        for leg in d["legs"]:
            amount = Decimal(leg["amount"])
            if leg["transfer_id"] is None:
                st.ledger.pay_from(o.encumbrance_id, leg["to_owner"], e.time_h, amount)
                st.trade_legs[leg["trade_id"]][leg["leg"]] = {"transfer_id": None,
                                                             "completed_h": e.time_h}
            else:
                st.ledger.consume(o.encumbrance_id, e.time_h, amount)
                st.transfers[leg["transfer_id"]] = Transfer(
                    leg["transfer_id"], o.account, leg["to_owner"], leg["asset"], amount,
                    o.home, leg["to_settlement"], e.time_h, stated_value=ZERO,
                    reference=leg["trade_id"], leg_of=leg["trade_id"])
                st.trade_legs[leg["trade_id"]][leg["leg"]] = {"transfer_id": leg["transfer_id"],
                                                             "completed_h": None}
        if Decimal(d["released"]) > 0:
            st.ledger.release(o.encumbrance_id, e.time_h, Decimal(d["released"]))
        o.status, o.settled_h = BatchOrderStatus.SETTLED, e.time_h
        for leg in d["legs"]:
            self._maybe_trade_settled(leg["trade_id"], e.time_h)

    def _maybe_trade_settled(self, trade_id: str, at_h: float) -> None:
        st = self.state
        legs, trade = st.trade_legs[trade_id], st.trades[trade_id]
        if trade.status is TradeStatus.SETTLED:
            return
        if all(leg is not None and leg["completed_h"] is not None for leg in legs.values()):
            trade.status = TradeStatus.SETTLED
            trade.settled_h = max(leg["completed_h"] for leg in legs.values())
            st.metrics.value_settled += trade.value
            st.metrics.completed_transactions += 1

    def _on_guarantee_contributed(self, e: Event) -> None:
        st, d = self.state, e.data
        q = Decimal(d["amount"])
        st.ledger.move(d["account"], d["clearing"], NEODOLLAR, d["settlement"], q)
        st.ledger.encumber(d["encumbrance_id"], d["clearing"], NEODOLLAR, d["settlement"], q,
                           EncumbrancePurpose.GUARANTEE, f"FUND:{d['clearing']}", e.time_h)
        st.guarantee_funds.setdefault(d["clearing"], []).append(d["encumbrance_id"])

    def _on_transfer_returned(self, e: Event) -> None:
        st = self.state
        t = st.transfers[e.data["transfer_id"]]
        st.ledger.credit(t.owner, t.asset, t.from_settlement, t.amount)
        t.status, t.completed_h, t.reason = TransferStatus.RETURNED, e.time_h, e.data["reason"]

    # --- observations -----------------------------------------------------
    def _on_observation_released(self, e: Event) -> None:
        st, d = self.state, e.data
        source = st.principals[d["source"]]
        seq = st.observation_seq.get(source.name, 0) + 1
        st.observation_seq[source.name] = seq
        st.observations[d["observation_id"]] = PriceObservation(
            d["observation_id"], source.name, d["underlying"], Decimal(d["price"]),
            e.time_h, source.settlement, seq)

    # --- communication ----------------------------------------------------
    def _on_session_opened(self, e: Event) -> None:
        st, d = self.state, e.data
        st.sessions[d["session_id"]] = Session(d["session_id"], d["initiator"], d["peer"],
                                               list(d["route"]), e.time_h)
        self._add_packet(d["syn_packet_id"], None, d["session_id"], PacketKind.SYN,
                         d["route"][0], 0, e.time_h, True)
        st.quotas.record_backbone(e.time_h)
        st.metrics.originated["backbone"] += 1

    def _on_session_state_changed(self, e: Event) -> None:
        s = self.state.sessions[e.data["session_id"]]
        s.state = SessionState(e.data["state"])
        if s.state is SessionState.ESTABLISHED and s.established_h is None:
            s.established_h = e.time_h
        if s.state in (SessionState.EXPIRED, SessionState.RESET):
            s.closed_h = e.time_h

    def _on_message_sent(self, e: Event) -> None:
        st, d = self.state, e.data
        snd, rcv = st.principals[d["sender"]], st.principals[d["recipient"]]
        service = Service(d["service"])
        msg = Message(d["message_id"], snd.name, rcv.name, snd.settlement, rcv.settlement,
                      service, TrafficClass(d["traffic_class"]), d["kind"], d["payload"],
                      d["size_bytes"], e.time_h, d["session_id"], list(d["packet_ids"]),
                      references=list(d["references"]))
        if service is Service.LOCAL:
            msg.status = MessageStatus.DELIVERED
            msg.delivered_h = e.time_h + 2 / 3600  # 1 s local access each way
        st.messages[msg.id] = msg
        for pid in d["packet_ids"]:
            self._add_packet(pid, msg.id, d["session_id"], PacketKind.DATA, snd.settlement,
                             d["size_bytes"], e.time_h, service is not Service.LOCAL)
        st.metrics.originated[service.value] += len(d["packet_ids"])
        if service is Service.BACKBONE:
            st.quotas.record_backbone(e.time_h, len(d["packet_ids"]))
        elif service is Service.DIRECT:
            st.quotas.record_direct(snd.name, d["launch_times"])

    def _on_transport_packet_created(self, e: Event) -> None:
        d = e.data
        original = self.state.packets.get(d.get("retry_of") or "")
        self._add_packet(d["packet_id"], original.message_id if original else None,
                         d["session_id"], PacketKind(d["kind"]), d["from_node"],
                         original.size_bytes if original else 0, e.time_h, False)

    def _on_packet_launched(self, e: Event) -> None:
        st, d = self.state, e.data
        pkt = st.packets[d["packet_id"]]
        pkt.launches.append(Launch(pkt.id, d["from_node"], d["to_node"], e.time_h,
                                   d["arrival_h"], d["lost"], d["attempt"], pkt.kind))
        msg = st.messages.get(pkt.message_id or "")
        service = msg.service.value if msg else Service.BACKBONE.value
        st.metrics.launches[service] = st.metrics.launches.get(service, 0) + 1
        if msg and msg.status is MessageStatus.QUEUED:
            msg.status = MessageStatus.IN_FLIGHT

    def _on_message_status_changed(self, e: Event) -> None:
        st = self.state
        msg = st.messages[e.data["message_id"]]
        msg.status = MessageStatus(e.data["status"])
        if msg.status is MessageStatus.DELIVERED:
            msg.delivered_h = e.time_h
            session = st.sessions.get(msg.session_id or "")
            if session is not None:
                session.last_rx_h = e.time_h

    def _on_note(self, e: Event) -> None:
        pass

    def _add_packet(self, packet_id, message_id, session_id, kind, node, size, at_h,
                    counts) -> None:
        st = self.state
        seq = st.packet_seq.get(node, 0) + 1
        st.packet_seq[node] = seq
        st.packets[packet_id] = Packet(packet_id, message_id, session_id, kind, seq,
                                       size, at_h, counts)

    # =====================================================================
    # Lookups and guards
    # =====================================================================
    def _new_id(self, prefix: str) -> str:
        return self.state.ids.allocate(prefix)

    def _require_open(self) -> None:
        if self.state.opened_h is None:
            raise InvalidState("the exchange has not been opened")

    def _require_financial(self, at_h: float) -> None:
        self._require_open()
        if at_h < FIRST_FINANCIAL_ACTION_H:
            raise TimeOrderError("no financial action is allowed before hour 0")

    def _require_new_principal(self, name: str) -> None:
        if not name or name in self.state.principals:
            raise ValidationError(f"principal name {name!r} is empty or taken")

    def _principal(self, name: str) -> Principal:
        p = self.state.principals.get(name)
        if p is None:
            raise ValidationError(f"unknown principal {name!r}")
        return p

    def _holder(self, name: str) -> Principal:
        p = self._principal(name)
        if isinstance(p, PriceSource):
            raise ValidationError(f"{name} is a price source and holds no assets")
        return p

    def _backbone_user(self, name: str) -> Institution:
        p = self._principal(name)
        if not p.may_use_backbone:
            raise ValidationError(f"{name} is not an operator or its clearing/settlement service")
        return p

    def _instrument(self, symbol: str) -> Instrument:
        inst = self.state.instruments.get(symbol)
        if inst is None:
            raise ValidationError(f"unknown instrument {symbol!r}")
        return inst

    def _order(self, order_id: str) -> Order:
        if order_id not in self.state.orders:
            raise ValidationError(f"unknown order {order_id!r}")
        return self.state.orders[order_id]

    def _open_position(self, position_id: str) -> Position:
        pos = self.state.positions.get(position_id)
        if pos is None:
            raise ValidationError(f"unknown position {position_id!r}")
        if pos.state is not PositionState.OPEN:
            raise InvalidState(f"{position_id} is {pos.state.value}")
        return pos

    def _party(self, pos: Position, account: str) -> Party:
        try:
            return pos.party_of(account)
        except KeyError as exc:
            raise ValidationError(str(exc)) from None

    def _in_transit(self, transfer_id: str) -> Transfer:
        t = self.state.transfers.get(transfer_id)
        if t is None:
            raise ValidationError(f"unknown transfer {transfer_id!r}")
        if t.status is not TransferStatus.IN_TRANSIT:
            raise InvalidState(f"{transfer_id} is {t.status.value}")
        return t

    def _transfer(self, transfer_id: str) -> Transfer:
        t = self.state.transfers.get(transfer_id)
        if t is None:
            raise ValidationError(f"unknown transfer {transfer_id!r}")
        return t

    def _batch(self, batch_id: str) -> Batch:
        b = self.state.batches.get(batch_id)
        if b is None:
            raise ValidationError(f"unknown batch {batch_id!r}")
        return b

    def _batch_order(self, order_id: str) -> BatchOrder:
        o = self.state.batch_orders.get(order_id)
        if o is None:
            raise ValidationError(f"unknown batch order {order_id!r}")
        return o

    def _session(self, session_id: str | None) -> Session:
        s = self.state.sessions.get(session_id or "")
        if s is None:
            raise ValidationError(f"unknown session {session_id!r}")
        return s

    def _message(self, message_id: str) -> Message:
        m = self.state.messages.get(message_id)
        if m is None:
            raise ValidationError(f"unknown message {message_id!r}")
        return m

    def _packet(self, packet_id: str) -> Packet:
        p = self.state.packets.get(packet_id)
        if p is None:
            raise ValidationError(f"unknown packet {packet_id!r}")
        return p

    def _observation_for(self, pos: Position, observation_id: str, at_h: float) -> PriceObservation:
        obs = self.state.observations.get(observation_id)
        inst = self.state.instruments[pos.symbol]
        if obs is None:
            raise ValidationError(f"unknown observation {observation_id!r}")
        if obs.underlying != inst.underlying or obs.source != inst.price_source:
            raise ValidationError(f"{observation_id} is not {inst.price_source}'s "
                                  f"{inst.underlying} observation")
        if obs.released_h > at_h:
            raise ValidationError(f"{observation_id} is not released until hour {obs.released_h}")
        return obs

    def _latest_observation(self, inst, at_h: float) -> PriceObservation | None:
        cands = [o for o in self.state.observations.values()
                 if o.underlying == inst.underlying and o.source == inst.price_source
                 and o.released_h <= at_h]
        return max(cands, key=lambda o: (o.released_h, o.seq), default=None)

    @staticmethod
    def _order_collateral(inst: Equity, side: Side, qty: Decimal,
                          price: Decimal) -> tuple[str, Decimal]:
        if side is Side.BUY:
            return inst.quote_asset, qty * price
        return inst.base_asset, qty
