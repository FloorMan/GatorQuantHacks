from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EquityOrder:
    order_id: str
    account_id: str
    side: str
    symbol: str
    quantity: int
    limit_price: float


@dataclass
class CashSettledFuture:
    contract_id: str
    long_account: str
    short_account: str
    quantity: int
    multiplier: float
    entry_price: float
    maturity_time_days: float
    latest_price: float

    @property
    def notional(self) -> float:
        return self.quantity * self.multiplier * self.entry_price

    def pnl_long(self, price: float) -> float:
        return self.quantity * self.multiplier * (price - self.entry_price)

    def required_margin(self, account_side: str, price: float, base_fraction: float) -> float:
        """One symmetric rule: base margin + current adverse mark-to-market loss."""
        pnl = self.pnl_long(price)
        side_pnl = pnl if account_side == "long" else -pnl
        adverse = max(0.0, -side_pnl)
        return base_fraction * self.notional + adverse
