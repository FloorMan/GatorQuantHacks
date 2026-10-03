"""Orders, trades, and a price-time priority limit order book.

Each instrument has one book at its venue settlement. A resting order holds
an encumbrance on its collateral at the venue (cash for a buy, shares for a
sell), so the same asset cannot back two orders.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderStatus(str, Enum):
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"

    @property
    def resting(self) -> bool:
        return self in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED)


class TradeStatus(str, Enum):
    EXECUTED = "executed"   # matched; settlement pending
    SETTLED = "settled"     # delivery versus payment done on the venue ledger
    FAILED = "failed"


@dataclass
class Order:
    id: str
    account: str
    symbol: str
    side: Side
    quantity: Decimal
    limit_price: Decimal
    submitted_h: float
    seq: int                       # global arrival sequence for time priority
    encumbrance_id: str | None = None
    filled: Decimal = Decimal(0)
    status: OrderStatus = OrderStatus.OPEN
    closed_h: float | None = None
    message_id: str | None = None  # instruction that carried the order, if traced

    @property
    def remaining(self) -> Decimal:
        return self.quantity - self.filled

    def to_dict(self) -> dict:
        return {"id": self.id, "account": self.account, "symbol": self.symbol,
                "side": self.side.value, "quantity": str(self.quantity),
                "limit_price": str(self.limit_price), "filled": str(self.filled),
                "remaining": str(self.remaining), "status": self.status.value,
                "submitted_h": self.submitted_h, "closed_h": self.closed_h,
                "encumbrance_id": self.encumbrance_id, "message_id": self.message_id}


@dataclass
class Trade:
    id: str
    symbol: str
    buy_order: str
    sell_order: str
    buyer: str
    seller: str
    price: Decimal
    quantity: Decimal
    executed_h: float
    venue: str
    status: TradeStatus = TradeStatus.EXECUTED
    settled_h: float | None = None

    @property
    def value(self) -> Decimal:
        return self.price * self.quantity

    def to_dict(self) -> dict:
        return {"id": self.id, "symbol": self.symbol, "buy_order": self.buy_order,
                "sell_order": self.sell_order, "buyer": self.buyer, "seller": self.seller,
                "price": str(self.price), "quantity": str(self.quantity),
                "value": str(self.value), "venue": self.venue,
                "executed_h": self.executed_h, "status": self.status.value,
                "settled_h": self.settled_h}


@dataclass(frozen=True)
class Fill:
    resting_order_id: str
    quantity: Decimal
    price: Decimal  # resting order's price


@dataclass
class OrderBook:
    symbol: str
    bids: list[Order] = field(default_factory=list)  # best first
    asks: list[Order] = field(default_factory=list)  # best first

    def add(self, order: Order) -> None:
        if order.side is Side.BUY:
            self.bids.append(order)
            self.bids.sort(key=lambda o: (-o.limit_price, o.seq))
        else:
            self.asks.append(order)
            self.asks.sort(key=lambda o: (o.limit_price, o.seq))

    def remove(self, order_id: str) -> None:
        self.bids = [o for o in self.bids if o.id != order_id]
        self.asks = [o for o in self.asks if o.id != order_id]

    def prune(self) -> None:
        self.bids = [o for o in self.bids if o.status.resting]
        self.asks = [o for o in self.asks if o.status.resting]

    def match(self, account: str, side: Side, quantity: Decimal,
              limit_price: Decimal) -> list[Fill]:
        """Plan fills for an incoming order without changing the book.

        Resting orders from the same account are skipped (no self-trading).
        """
        fills, left = [], quantity
        for resting in (self.asks if side is Side.BUY else self.bids):
            if left <= 0:
                break
            crosses = (resting.limit_price <= limit_price if side is Side.BUY
                       else resting.limit_price >= limit_price)
            if not crosses:
                break
            if resting.account == account:
                continue
            q = min(left, resting.remaining)
            fills.append(Fill(resting.id, q, resting.limit_price))
            left -= q
        return fills

    def best_bid(self) -> Decimal | None:
        return self.bids[0].limit_price if self.bids else None

    def best_ask(self) -> Decimal | None:
        return self.asks[0].limit_price if self.asks else None

    def to_dict(self) -> dict:
        level = lambda o: {"order": o.id, "account": o.account,
                           "price": str(o.limit_price), "remaining": str(o.remaining)}
        return {"symbol": self.symbol, "bids": [level(o) for o in self.bids],
                "asks": [level(o) for o in self.asks]}
