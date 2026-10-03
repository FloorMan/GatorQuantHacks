"""Opening balance sheet (Section 6) and its limit checks."""

from dataclasses import dataclass, field
from decimal import Decimal

from .assets import share_asset, to_quantity
from .constants import (MAX_ACCOUNTS, MAX_OPENING_NEODOLLARS, MAX_OPENING_SHARES,
                        MIN_ACCOUNT_SETTLEMENTS, MIN_ACCOUNTS, NEODOLLAR,
                        validate_settlement)
from .errors import ValidationError


@dataclass(frozen=True)
class OpeningAccount:
    """One row of the opening balance sheet."""
    name: str
    settlement: str
    neodollars: Decimal = Decimal(0)
    shares: dict = field(default_factory=dict)  # company -> whole number of shares

    def holdings(self) -> dict[str, Decimal]:
        out = {}
        if self.neodollars:
            out[NEODOLLAR] = to_quantity(NEODOLLAR, self.neodollars)
        for company, qty in self.shares.items():
            if qty:
                asset = share_asset(company)
                out[asset] = to_quantity(asset, qty)
        return out


@dataclass(frozen=True)
class OpeningBalanceSheet:
    accounts: tuple[OpeningAccount, ...]

    def validate(self) -> None:
        names = [a.name for a in self.accounts]
        if len(set(names)) != len(names):
            raise ValidationError("account names must be unique")
        if not MIN_ACCOUNTS <= len(self.accounts) <= MAX_ACCOUNTS:
            raise ValidationError(
                f"need {MIN_ACCOUNTS}-{MAX_ACCOUNTS} accounts, got {len(self.accounts)}")
        for a in self.accounts:
            validate_settlement(a.settlement)
            for asset, qty in a.holdings().items():
                if qty < 0:
                    raise ValidationError(f"{a.name}: negative {asset}")
        if len({a.settlement for a in self.accounts}) < MIN_ACCOUNT_SETTLEMENTS:
            raise ValidationError(
                f"accounts must span at least {MIN_ACCOUNT_SETTLEMENTS} settlements")
        totals = self.totals()
        if totals.get(NEODOLLAR, Decimal(0)) > MAX_OPENING_NEODOLLARS:
            raise ValidationError(f"opening NeoDollars exceed {MAX_OPENING_NEODOLLARS}")
        shares = sum(q for asset, q in totals.items() if asset != NEODOLLAR)
        if shares > MAX_OPENING_SHARES:
            raise ValidationError(f"opening shares exceed {MAX_OPENING_SHARES}")

    def totals(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for a in self.accounts:
            for asset, qty in a.holdings().items():
                out[asset] = out.get(asset, Decimal(0)) + qty
        return out

    def to_rows(self) -> list[dict]:
        return [{"account": a.name, "settlement": a.settlement,
                 "neodollars": str(a.neodollars),
                 "shares": {c: int(q) for c, q in a.shares.items()}}
                for a in self.accounts]
