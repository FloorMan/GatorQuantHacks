"""Global batch auction for cross-settlement equity orders (design paper, Section 6).

Same-settlement trades use the continuous order book. Any trade whose parties sit
at different settlements goes through a batch held at one market settlement:

* **Pre-funding.** An order's collateral is locked at the trader's *home* ledger
  (cash for a buy, shares for a sell) before the order leaves home.
* **Priority timestamp.** The home operator stamps the order on receipt (1 s of
  local access after the client sends it). That stamp, not the arrival time at
  the market, decides priority, so distance never buys priority.
* **Uniform clearing price.** The batch picks the price that maximizes executed
  quantity, then minimizes the buy/sell imbalance; if several prices tie, the
  midpoint of the tied range (to the cent). Every fill uses that one price, so a
  buyer never pays above its limit and a seller never receives below its limit.
* **Allocation** on the rationed side: better limit first, then earlier priority
  timestamp. Orders with the exact same limit and timestamp share pro rata in
  whole shares; leftover shares go one at a time in the order of
  sha256(batch id + order id).

The functions here are pure. ``Exchange`` records their results as events.
"""

import hashlib
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal
from enum import Enum

from .trading import Side

ZERO = Decimal(0)
CENT = Decimal("0.01")


class BatchOrderStatus(str, Enum):
    RESERVED = "reserved"    # collateral locked at home; order travelling to the market
    ACCEPTED = "accepted"    # inside an open batch
    ROLLED = "rolled"        # arrived after its batch closed; waits for the next batch
    EXECUTED = "executed"    # batch result recorded (filled, partly filled, or unfilled)
    CANCELLED = "cancelled"  # cancelled at the market before execution
    SETTLED = "settled"      # home operator has applied the result to its ledger

    @property
    def live(self) -> bool:
        return self is not BatchOrderStatus.SETTLED


class BatchStatus(str, Enum):
    OPEN = "open"
    EXECUTED = "executed"


@dataclass
class BatchOrder:
    id: str
    account: str
    symbol: str
    side: Side
    quantity: Decimal
    limit_price: Decimal
    home: str                 # settlement whose ledger holds the locked collateral
    source_h: float           # home operator's receipt stamp: the priority timestamp
    encumbrance_id: str
    batch_id: str | None = None
    status: BatchOrderStatus = BatchOrderStatus.RESERVED
    filled: Decimal = ZERO
    arrived_h: float | None = None   # when it reached the market
    executed_h: float | None = None
    settled_h: float | None = None
    missed_batches: list[str] = field(default_factory=list)

    @property
    def lock(self) -> Decimal:
        """Collateral this order keeps locked until its home applies the result."""
        return self.quantity * self.limit_price if self.side is Side.BUY else self.quantity

    def to_dict(self) -> dict:
        return {"id": self.id, "account": self.account, "symbol": self.symbol,
                "side": self.side.value, "quantity": str(self.quantity),
                "limit_price": str(self.limit_price), "home": self.home,
                "source_h": self.source_h, "batch_id": self.batch_id,
                "status": self.status.value, "filled": str(self.filled),
                "arrived_h": self.arrived_h, "executed_h": self.executed_h,
                "settled_h": self.settled_h, "missed_batches": list(self.missed_batches),
                "encumbrance_id": self.encumbrance_id}


@dataclass
class Batch:
    id: str
    symbol: str
    market: str               # settlement where the batch is held
    opened_h: float
    closes_h: float
    timing: dict              # how closes_h was derived (route delays, retry allowance)
    order_ids: list[str] = field(default_factory=list)
    status: BatchStatus = BatchStatus.OPEN
    executed_h: float | None = None
    price: Decimal | None = None
    trade_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"id": self.id, "symbol": self.symbol, "market": self.market,
                "opened_h": self.opened_h, "closes_h": self.closes_h, "timing": self.timing,
                "order_ids": list(self.order_ids), "status": self.status.value,
                "executed_h": self.executed_h,
                "price": None if self.price is None else str(self.price),
                "trade_ids": list(self.trade_ids)}


# --------------------------------------------------------------------- pricing

def _volume(orders: list[BatchOrder], p: Decimal) -> tuple[Decimal, Decimal]:
    demand = sum((o.quantity for o in orders if o.side is Side.BUY and o.limit_price >= p), ZERO)
    supply = sum((o.quantity for o in orders if o.side is Side.SELL and o.limit_price <= p), ZERO)
    return demand, supply


def clearing_price(orders: list[BatchOrder]) -> tuple[Decimal | None, list[dict]]:
    """The uniform price, and the demand/supply table it was chosen from."""
    table = []
    for p in sorted({o.limit_price for o in orders}):
        d, s = _volume(orders, p)
        table.append({"price": p, "demand": d, "supply": s, "volume": min(d, s),
                      "imbalance": abs(d - s)})
    best = max((r["volume"] for r in table), default=ZERO)
    if best == 0:
        return None, table
    least = min(r["imbalance"] for r in table if r["volume"] == best)
    tied = [r["price"] for r in table if r["volume"] == best and r["imbalance"] == least]
    mid = ((min(tied) + max(tied)) / 2).quantize(CENT, rounding=ROUND_HALF_EVEN)
    d, s = _volume(orders, mid)
    return (mid if min(d, s) == best else min(tied)), table


# ------------------------------------------------------------------ allocation

def _tiebreak(batch_id: str, order_id: str) -> str:
    return hashlib.sha256(f"{batch_id}:{order_id}".encode()).hexdigest()


def _priority_key(o: BatchOrder):
    return (-o.limit_price if o.side is Side.BUY else o.limit_price, o.source_h)


def _allocate_side(batch_id: str, orders: list[BatchOrder], volume: Decimal) -> dict[str, dict]:
    """Fill ``volume`` across eligible orders of one side by priority."""
    out, left, rank = {}, volume, 0
    groups: dict[tuple, list[BatchOrder]] = {}
    for o in sorted(orders, key=_priority_key):
        groups.setdefault(_priority_key(o), []).append(o)
    for key, group in groups.items():
        rank += 1
        total = sum((o.quantity for o in group), ZERO)
        if left >= total:
            for o in group:
                out[o.id] = {"qty": o.quantity, "rank": rank,
                             "reason": f"priority rank {rank}: filled in full"}
            left -= total
            continue
        if left <= 0:
            for o in group:
                out[o.id] = {"qty": ZERO, "rank": rank,
                             "reason": f"priority rank {rank}: outranked, quantity taken by "
                                       "better price or earlier source time"}
            continue
        if len(group) == 1:
            o = group[0]
            out[o.id] = {"qty": left, "rank": rank,
                         "reason": f"priority rank {rank}: partial fill, liquidity ran out"}
            left = ZERO
            continue
        # Exact tie on limit and source time: pro rata in whole shares.
        shares = {o.id: (o.quantity * left / total).to_integral_value(ROUND_DOWN) for o in group}
        spare = left - sum(shares.values(), ZERO)
        order = sorted(group, key=lambda o: _tiebreak(batch_id, o.id))
        i = 0
        while spare > 0:
            o = order[i % len(order)]
            if shares[o.id] < o.quantity:
                shares[o.id] += 1
                spare -= 1
            i += 1
        for o in group:
            out[o.id] = {"qty": shares[o.id], "rank": rank,
                         "reason": f"priority rank {rank}: exact tie with {len(group) - 1} other "
                                   f"order(s) on limit and source time; pro rata, remainder by "
                                   f"hash {_tiebreak(batch_id, o.id)[:8]}"}
        left = ZERO
    return out


def run_auction(batch_id: str, orders: list[BatchOrder]) -> dict:
    """Clear one batch. Returns price, per-order allocations, trades, and the table."""
    price, table = clearing_price(orders)
    alloc: dict[str, dict] = {}
    trades: list[dict] = []
    if price is not None:
        buys = [o for o in orders if o.side is Side.BUY and o.limit_price >= price]
        sells = [o for o in orders if o.side is Side.SELL and o.limit_price <= price]
        d, s = _volume(orders, price)
        volume = min(d, s)
        alloc.update(_allocate_side(batch_id, buys, volume))
        alloc.update(_allocate_side(batch_id, sells, volume))
        # Pair fills in priority order so each trade names one buyer and one seller.
        bq = [[o, alloc[o.id]["qty"]] for o in sorted(buys, key=_priority_key) if alloc[o.id]["qty"]]
        sq = [[o, alloc[o.id]["qty"]] for o in sorted(sells, key=_priority_key) if alloc[o.id]["qty"]]
        i = j = 0
        while i < len(bq) and j < len(sq):
            q = min(bq[i][1], sq[j][1])
            trades.append({"buy_order": bq[i][0].id, "sell_order": sq[j][0].id, "quantity": q})
            bq[i][1] -= q
            sq[j][1] -= q
            i += bq[i][1] == 0
            j += sq[j][1] == 0
    for o in orders:
        if o.id not in alloc:
            cross = "above" if o.side is Side.BUY else "below"
            reason = ("no crossing orders in this batch" if price is None else
                      f"limit {o.limit_price} does not reach clearing price {price} "
                      f"(price protection: would trade {cross} its limit)")
            alloc[o.id] = {"qty": ZERO, "rank": None, "reason": reason}
    return {"price": price, "allocations": alloc, "trades": trades, "table": table}
