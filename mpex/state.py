"""The complete mutable state of the exchange at one moment.

``Exchange`` owns one ``ExchangeState`` and changes it only by applying
journal events, so any past state can be rebuilt by replay.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from .comms import Message, Packet, QuotaTracker, Session
from .ids import IdAllocator
from .instruments import Instrument
from .ledger import Ledger
from .observations import PriceObservation
from .positions import Position
from .principals import Principal
from .trading import Order, OrderBook, Trade
from .transfers import Transfer, TransferStatus

ZERO = Decimal(0)


def _hours(h: float) -> Decimal:
    # Subtract exact decimal forms of each time so 0.6 - 0.5 is 0.1, not 0.0999...
    return Decimal(repr(h))


@dataclass
class Metrics:
    """Running measures from Section 7 ('How the measures are defined')."""
    last_h: float | None = None
    peak_encumbered: dict[str, Decimal] = field(default_factory=dict)
    peak_encumbered_h: dict[str, float] = field(default_factory=dict)
    asset_hours: dict[str, Decimal] = field(default_factory=dict)
    value_settled: Decimal = ZERO
    completed_transactions: int = 0
    originated: dict[str, int] = field(
        default_factory=lambda: {"backbone": 0, "direct": 0, "local": 0})
    launches: dict[str, int] = field(default_factory=lambda: {"backbone": 0, "direct": 0})

    def advance(self, ledger: Ledger, to_h: float) -> None:
        """Integrate encumbered amounts over (last_h, to_h]."""
        if self.last_h is not None and to_h > self.last_h:
            dt = _hours(to_h) - _hours(self.last_h)
            for asset in ledger.assets():
                enc = ledger.encumbered_total(asset)
                if enc:
                    self.asset_hours[asset] = self.asset_hours.get(asset, ZERO) + enc * dt
        self.last_h = to_h

    def observe_peaks(self, ledger: Ledger, at_h: float) -> None:
        for asset in ledger.assets():
            enc = ledger.encumbered_total(asset)
            if enc > self.peak_encumbered.get(asset, ZERO):
                self.peak_encumbered[asset] = enc
                self.peak_encumbered_h[asset] = at_h

    def to_dict(self, ledger: Ledger, as_of_h: float | None = None) -> dict:
        hours = dict(self.asset_hours)
        if as_of_h is not None and self.last_h is not None and as_of_h > self.last_h:
            dt = _hours(as_of_h) - _hours(self.last_h)
            for asset in ledger.assets():
                hours[asset] = hours.get(asset, ZERO) + ledger.encumbered_total(asset) * dt
        packets = sum(self.originated.values()) + sum(self.launches.values())
        return {
            "peak_encumbered": {a: str(v) for a, v in self.peak_encumbered.items()},
            "peak_encumbered_h": dict(self.peak_encumbered_h),
            "asset_hours": {a: str(v) for a, v in hours.items() if v},
            "value_settled": str(self.value_settled),
            "completed_transactions": self.completed_transactions,
            "packets_originated": dict(self.originated),
            "launches": dict(self.launches),
            "communication_efficiency": (packets / self.completed_transactions
                                         if self.completed_transactions else None),
        }


@dataclass
class ExchangeState:
    clock_h: float | None = None
    opened_h: float | None = None
    opening_supply: dict[str, Decimal] = field(default_factory=dict)
    ids: IdAllocator = field(default_factory=IdAllocator)
    principals: dict[str, Principal] = field(default_factory=dict)
    ledger: Ledger = field(default_factory=Ledger)
    instruments: dict[str, Instrument] = field(default_factory=dict)
    books: dict[str, OrderBook] = field(default_factory=dict)
    orders: dict[str, Order] = field(default_factory=dict)
    order_seq: int = 0
    trades: dict[str, Trade] = field(default_factory=dict)
    positions: dict[str, Position] = field(default_factory=dict)
    transfers: dict[str, Transfer] = field(default_factory=dict)
    observations: dict[str, PriceObservation] = field(default_factory=dict)
    observation_seq: dict[str, int] = field(default_factory=dict)  # per source
    sessions: dict[str, Session] = field(default_factory=dict)
    messages: dict[str, Message] = field(default_factory=dict)
    packets: dict[str, Packet] = field(default_factory=dict)
    packet_seq: dict[str, int] = field(default_factory=dict)  # per sending node
    quotas: QuotaTracker = field(default_factory=QuotaTracker)
    metrics: Metrics = field(default_factory=Metrics)

    def in_transit(self, asset: str) -> Decimal:
        return sum((t.amount for t in self.transfers.values()
                    if t.asset == asset and t.status is TransferStatus.IN_TRANSIT), ZERO)
