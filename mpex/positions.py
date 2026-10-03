"""Open obligations from price-dependent contracts, and the margin rule.

A ``Position`` is one bilateral contract recorded at its instrument's venue
(the clearing location). Both sides' margin is encumbered there. Settlement
reports the three moments from Section 6 separately: discharge, backed
claim, and spendable.

Interim marks only recompute margin requirements; money moves once, at
final settlement or default, so interim and final payments can never be
double counted.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from .instruments import Future, Instrument, Option, OptionType

ZERO = Decimal(0)


class PositionState(str, Enum):
    OPEN = "open"
    SETTLED = "settled"                # paid in full at maturity
    FUNDED_DEFAULT = "funded_default"  # closed out from posted margin


class Party(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def other(self) -> "Party":
        return Party.SHORT if self is Party.LONG else Party.LONG


@dataclass(frozen=True)
class MarginPolicy:
    """One margin rule for both price directions (Section 7).

    Required margin for a side = its current mark-to-market loss plus an
    allowance for an adverse move of ``move_fraction`` of the reference
    price. It uses only contract terms, positions, and observations already
    received; it never looks at future prices.
    """
    move_fraction: Decimal = Decimal("0.25")

    def required(self, instrument: Instrument, position: "Position",
                 party: Party, reference_price: Decimal) -> Decimal:
        q = position.quantity
        if isinstance(instrument, Future):
            move = q * instrument.multiplier * reference_price * self.move_fraction
            pnl_long = instrument.long_payoff(q, position.entry_price, reference_price)
            loss = -pnl_long if party is Party.LONG else pnl_long
            return max(loss, ZERO) + move
        if isinstance(instrument, Option):
            if party is Party.LONG:
                return ZERO  # holder paid the premium up front and owes nothing more
            if instrument.option_type is OptionType.CALL:
                stressed = reference_price * (1 + self.move_fraction)
            else:
                stressed = reference_price * (1 - self.move_fraction)
            worst = max(instrument.long_payoff(q, reference_price),
                        instrument.long_payoff(q, max(stressed, ZERO)))
            return worst
        raise TypeError(f"no margin rule for {instrument.kind.value}")


@dataclass
class Position:
    id: str
    symbol: str
    venue: str
    long: str
    short: str
    quantity: Decimal
    entry_price: Decimal       # futures: entry price; options: premium per unit
    opened_h: float
    opening_price: Decimal = ZERO  # reference price at opening, for notional
    margin: dict[str, list[str]] = field(
        default_factory=lambda: {Party.LONG.value: [], Party.SHORT.value: []})
    state: PositionState = PositionState.OPEN
    last_mark_price: Decimal | None = None
    last_mark_h: float | None = None
    margin_calls: dict[str, str] = field(default_factory=dict)  # party -> amount (str)
    final_price: Decimal | None = None
    observation_ids: list[str] = field(default_factory=list)
    long_payoff: Decimal | None = None   # total owed to long (negative: long pays)
    paid: Decimal = ZERO
    shortfall: Decimal = ZERO            # unpaid and unbacked (must stay zero)
    winner: str | None = None
    defaulter: str | None = None
    discharged_h: float | None = None
    backed_claim_h: float | None = None
    spendable_h: float | None = None

    def party_of(self, account: str) -> Party:
        if account == self.long:
            return Party.LONG
        if account == self.short:
            return Party.SHORT
        raise KeyError(f"{account} is not a party to {self.id}")

    def account_of(self, party: Party) -> str:
        return self.long if party is Party.LONG else self.short

    def to_dict(self) -> dict:
        s = lambda d: None if d is None else str(d)
        return {"id": self.id, "symbol": self.symbol, "venue": self.venue,
                "long": self.long, "short": self.short,
                "quantity": str(self.quantity), "entry_price": str(self.entry_price),
                "opened_h": self.opened_h, "opening_price": str(self.opening_price),
                "state": self.state.value,
                "margin_encumbrances": {k: list(v) for k, v in self.margin.items()},
                "last_mark_price": s(self.last_mark_price), "last_mark_h": self.last_mark_h,
                "margin_calls": dict(self.margin_calls),
                "final_price": s(self.final_price), "observation_ids": list(self.observation_ids),
                "long_payoff": s(self.long_payoff), "paid": str(self.paid),
                "shortfall": str(self.shortfall), "winner": self.winner,
                "defaulter": self.defaulter, "discharged_h": self.discharged_h,
                "backed_claim_h": self.backed_claim_h, "spendable_h": self.spendable_h}
