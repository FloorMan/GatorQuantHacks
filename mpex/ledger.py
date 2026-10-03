"""Located holdings and encumbrances.

Every balance is keyed by (owner, asset, settlement): an asset is always
somewhere, and value at Earth is not spendable at Mars until a transfer
completes. An ``Encumbrance`` reserves part of a balance for one use
(order collateral, margin, a guarantee, an in-progress transfer...), which
enforces "an asset supports only one use at a time" (Section 7).

The ledger only enforces arithmetic invariants. Business rules (who may do
what, and when) live in ``Exchange``.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from .assets import to_quantity
from .errors import InsufficientFunds, InvalidState, ValidationError

ZERO = Decimal(0)
HoldingKey = tuple[str, str, str]  # (owner, asset, settlement)


class EncumbrancePurpose(str, Enum):
    ORDER = "order"            # collateral for a resting order
    TRADE = "trade"            # executed trade awaiting settlement
    MARGIN = "margin"          # derivative margin
    GUARANTEE = "guarantee"
    LOAN_COLLATERAL = "loan_collateral"
    CURRENCY_BACKING = "currency_backing"
    OTHER = "other"


@dataclass
class Encumbrance:
    id: str
    owner: str
    asset: str
    settlement: str
    amount: Decimal       # amount still encumbered
    original: Decimal
    purpose: EncumbrancePurpose
    reference: str | None  # order/trade/position id this backs
    created_h: float
    closed_h: float | None = None

    @property
    def key(self) -> HoldingKey:
        return (self.owner, self.asset, self.settlement)

    @property
    def active(self) -> bool:
        return self.amount > 0

    def to_dict(self) -> dict:
        return {"id": self.id, "owner": self.owner, "asset": self.asset,
                "settlement": self.settlement, "amount": str(self.amount),
                "original": str(self.original), "purpose": self.purpose.value,
                "reference": self.reference, "created_h": self.created_h,
                "closed_h": self.closed_h}


class Ledger:
    def __init__(self) -> None:
        self._balances: dict[HoldingKey, Decimal] = {}
        self.encumbrances: dict[str, Encumbrance] = {}
        self._encumbered: dict[HoldingKey, Decimal] = {}

    # --- queries ----------------------------------------------------------
    def balance(self, owner: str, asset: str, settlement: str) -> Decimal:
        return self._balances.get((owner, asset, settlement), ZERO)

    def encumbered(self, owner: str, asset: str, settlement: str) -> Decimal:
        return self._encumbered.get((owner, asset, settlement), ZERO)

    def available(self, owner: str, asset: str, settlement: str) -> Decimal:
        return self.balance(owner, asset, settlement) - self.encumbered(owner, asset, settlement)

    def holdings(self, owner: str | None = None) -> dict[HoldingKey, Decimal]:
        return {k: v for k, v in self._balances.items()
                if v != 0 and (owner is None or k[0] == owner)}

    def total_supply(self, asset: str) -> Decimal:
        return sum((v for (_, a, _), v in self._balances.items() if a == asset), ZERO)

    def assets(self) -> set[str]:
        return {a for (_, a, _) in self._balances}

    def encumbered_total(self, asset: str) -> Decimal:
        return sum((v for (_, a, _), v in self._encumbered.items() if a == asset), ZERO)

    def active_encumbrances(self, reference: str | None = None) -> list[Encumbrance]:
        return [e for e in self.encumbrances.values()
                if e.active and (reference is None or e.reference == reference)]

    # --- mutations --------------------------------------------------------
    def credit(self, owner: str, asset: str, settlement: str, amount) -> None:
        q = self._positive(asset, amount)
        key = (owner, asset, settlement)
        self._balances[key] = self._balances.get(key, ZERO) + q

    def debit(self, owner: str, asset: str, settlement: str, amount) -> None:
        q = self._positive(asset, amount)
        if self.available(owner, asset, settlement) < q:
            raise InsufficientFunds(
                f"{owner} has {self.available(owner, asset, settlement)} {asset} "
                f"available at {settlement}, needs {q}")
        key = (owner, asset, settlement)
        self._balances[key] -= q

    def move(self, owner: str, to_owner: str, asset: str, settlement: str, amount) -> None:
        """Move free value between owners at one settlement."""
        self.debit(owner, asset, settlement, amount)
        self.credit(to_owner, asset, settlement, amount)

    def encumber(self, enc_id: str, owner: str, asset: str, settlement: str, amount,
                 purpose: EncumbrancePurpose, reference: str | None, at_h: float) -> Encumbrance:
        q = self._positive(asset, amount)
        if enc_id in self.encumbrances:
            raise ValidationError(f"duplicate encumbrance id {enc_id}")
        if self.available(owner, asset, settlement) < q:
            raise InsufficientFunds(
                f"{owner} cannot encumber {q} {asset} at {settlement}: "
                f"only {self.available(owner, asset, settlement)} available")
        enc = Encumbrance(enc_id, owner, asset, settlement, q, q,
                          EncumbrancePurpose(purpose), reference, at_h)
        self.encumbrances[enc_id] = enc
        self._encumbered[enc.key] = self._encumbered.get(enc.key, ZERO) + q
        return enc

    def release(self, enc_id: str, at_h: float, amount=None) -> Decimal:
        """Return encumbered value to the owner's free balance."""
        enc = self._active(enc_id)
        q = enc.amount if amount is None else self._positive(enc.asset, amount)
        self._reduce(enc, q, at_h)
        return q

    def pay_from(self, enc_id: str, to_owner: str, at_h: float, amount=None) -> Decimal:
        """Move encumbered value to another owner at the same settlement.

        The payer's encumbrance and balance fall together, so the value is
        never available to the payer in between.
        """
        enc = self._active(enc_id)
        q = enc.amount if amount is None else self._positive(enc.asset, amount)
        self._reduce(enc, q, at_h)
        self._balances[enc.key] -= q
        self.credit(to_owner, enc.asset, enc.settlement, q)
        return q

    def consume(self, enc_id: str, at_h: float, amount=None) -> Decimal:
        """Remove encumbered value from the ledger (e.g. it left for transit)."""
        enc = self._active(enc_id)
        q = enc.amount if amount is None else self._positive(enc.asset, amount)
        self._reduce(enc, q, at_h)
        self._balances[enc.key] -= q
        return q

    def snapshot(self) -> dict:
        return {
            "balances": [
                {"owner": o, "asset": a, "settlement": s, "balance": str(v),
                 "encumbered": str(self.encumbered(o, a, s)),
                 "available": str(self.available(o, a, s))}
                for (o, a, s), v in sorted(self._balances.items()) if v != 0],
            "encumbrances": [e.to_dict() for e in self.encumbrances.values() if e.active],
        }

    # --- helpers ----------------------------------------------------------
    def _active(self, enc_id: str) -> Encumbrance:
        enc = self.encumbrances.get(enc_id)
        if enc is None:
            raise ValidationError(f"unknown encumbrance {enc_id}")
        if not enc.active:
            raise InvalidState(f"encumbrance {enc_id} is already closed")
        return enc

    def _reduce(self, enc: Encumbrance, q: Decimal, at_h: float) -> None:
        if q > enc.amount:
            raise InsufficientFunds(f"encumbrance {enc.id} holds {enc.amount}, needs {q}")
        enc.amount -= q
        self._encumbered[enc.key] -= q
        if enc.amount == 0:
            enc.closed_h = at_h

    @staticmethod
    def _positive(asset: str, amount) -> Decimal:
        q = to_quantity(asset, amount)
        if q <= 0:
            raise ValidationError(f"amount must be positive, got {amount!r}")
        return q
