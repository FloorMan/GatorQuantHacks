from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, Optional
import pandas as pd


@dataclass
class Account:
    account_id: str
    settlement: str
    cash_available: float
    shares_available: float
    cash_encumbered: float = 0.0
    shares_encumbered: float = 0.0

    @property
    def cash_total(self) -> float:
        return self.cash_available + self.cash_encumbered

    @property
    def shares_total(self) -> float:
        return self.shares_available + self.shares_encumbered


@dataclass
class Obligation:
    obligation_id: str
    product_type: str
    long_account: str
    short_account: str
    authority: str
    status: str = "PROPOSED"
    notional: float = 0.0
    open_time_days: Optional[float] = None
    maturity_time_days: Optional[float] = None
    discharge_time_days: Optional[float] = None
    backed_claim_time_days: Optional[float] = None
    spendable_time_days: Optional[float] = None


class Ledger:
    def __init__(self):
        self.accounts: Dict[str, Account] = {}
        self.obligations: Dict[str, Obligation] = {}
        self.journal: list[dict] = []
        self.encumbrances: list[dict] = []
        self._opening_cash = 0.0
        self._opening_shares = 0.0

    def add_account(self, account: Account) -> None:
        if account.account_id in self.accounts:
            raise ValueError(f"Duplicate account {account.account_id}")
        self.accounts[account.account_id] = account
        self._opening_cash += account.cash_total
        self._opening_shares += account.shares_total
        self.verify_invariants()

    def add_obligation(self, obligation: Obligation) -> None:
        if obligation.obligation_id in self.obligations:
            raise ValueError(f"Duplicate obligation {obligation.obligation_id}")
        self.obligations[obligation.obligation_id] = obligation
        self.journal.append({"time_days": obligation.open_time_days, "event": "OBLIGATION_CREATED", "obligation_id": obligation.obligation_id})

    def encumber_cash(self, account_id: str, amount: float, obligation_id: str, time_days: float) -> None:
        a = self.accounts[account_id]
        if amount < -1e-12 or a.cash_available + 1e-9 < amount:
            raise ValueError(f"Insufficient cash in {account_id}: need {amount}, have {a.cash_available}")
        a.cash_available -= amount
        a.cash_encumbered += amount
        self.encumbrances.append({"time_days": time_days, "account_id": account_id, "asset": "NeoDollar", "amount": amount, "obligation_id": obligation_id, "action": "ENCUMBER"})
        self.journal.append({"time_days": time_days, "event": "ENCUMBER_CASH", "account_id": account_id, "amount": amount, "obligation_id": obligation_id})
        self.verify_invariants()

    def release_cash(self, account_id: str, amount: float, obligation_id: str, time_days: float) -> None:
        a = self.accounts[account_id]
        if a.cash_encumbered + 1e-9 < amount:
            raise ValueError("Release exceeds encumbered cash")
        a.cash_encumbered -= amount
        a.cash_available += amount
        self.encumbrances.append({"time_days": time_days, "account_id": account_id, "asset": "NeoDollar", "amount": amount, "obligation_id": obligation_id, "action": "RELEASE"})
        self.journal.append({"time_days": time_days, "event": "RELEASE_CASH", "account_id": account_id, "amount": amount, "obligation_id": obligation_id})
        self.verify_invariants()

    def encumber_shares(self, account_id: str, quantity: float, obligation_id: str, time_days: float) -> None:
        a = self.accounts[account_id]
        if quantity < -1e-12 or a.shares_available + 1e-9 < quantity:
            raise ValueError(f"Insufficient shares in {account_id}")
        a.shares_available -= quantity
        a.shares_encumbered += quantity
        self.encumbrances.append({"time_days": time_days, "account_id": account_id, "asset": "Share", "amount": quantity, "obligation_id": obligation_id, "action": "ENCUMBER"})
        self.journal.append({"time_days": time_days, "event": "ENCUMBER_SHARES", "account_id": account_id, "amount": quantity, "obligation_id": obligation_id})
        self.verify_invariants()

    def settle_equity(self, buyer_id: str, seller_id: str, price: float, quantity: float,
                      obligation_id: str, time_days: float) -> None:
        cash = price * quantity
        buyer = self.accounts[buyer_id]
        seller = self.accounts[seller_id]
        if buyer.cash_encumbered + 1e-9 < cash:
            raise ValueError("Buyer cash not fully encumbered")
        if seller.shares_encumbered + 1e-9 < quantity:
            raise ValueError("Seller shares not fully encumbered")
        buyer.cash_encumbered -= cash
        seller.cash_available += cash
        seller.shares_encumbered -= quantity
        buyer.shares_available += quantity
        self.journal.append({"time_days": time_days, "event": "EQUITY_SETTLE", "obligation_id": obligation_id, "cash": cash, "shares": quantity, "buyer": buyer_id, "seller": seller_id})
        self.verify_invariants()

    def transfer_encumbered_cash(self, payer_id: str, payee_id: str, amount: float,
                                 obligation_id: str, time_days: float) -> None:
        payer = self.accounts[payer_id]
        payee = self.accounts[payee_id]
        if payer.cash_encumbered + 1e-9 < amount:
            raise ValueError("Payment exceeds payer encumbered cash")
        payer.cash_encumbered -= amount
        payee.cash_available += amount
        self.journal.append({"time_days": time_days, "event": "CASH_SETTLE", "obligation_id": obligation_id, "payer": payer_id, "payee": payee_id, "amount": amount})
        self.verify_invariants()

    def verify_invariants(self) -> None:
        for a in self.accounts.values():
            values = (a.cash_available, a.cash_encumbered, a.shares_available, a.shares_encumbered)
            if any(v < -1e-8 for v in values):
                raise AssertionError(f"Negative ledger field in {a.account_id}: {values}")
        cash = sum(a.cash_total for a in self.accounts.values())
        shares = sum(a.shares_total for a in self.accounts.values())
        if self.accounts and abs(cash - self._opening_cash) > 1e-6:
            raise AssertionError(f"Cash conservation failed: {cash} != {self._opening_cash}")
        if self.accounts and abs(shares - self._opening_shares) > 1e-6:
            raise AssertionError(f"Share conservation failed: {shares} != {self._opening_shares}")

    def accounts_df(self) -> pd.DataFrame:
        return pd.DataFrame([asdict(a) | {"cash_total": a.cash_total, "shares_total": a.shares_total} for a in self.accounts.values()])

    def obligations_df(self) -> pd.DataFrame:
        return pd.DataFrame([asdict(o) for o in self.obligations.values()])

    def journal_df(self) -> pd.DataFrame:
        return pd.DataFrame(self.journal)
