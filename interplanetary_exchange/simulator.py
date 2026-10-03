from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .config import ModelConfig
from .ledger import Account, Ledger, Obligation
from .network import NetworkModel, Incident, SECONDS_PER_DAY
from .routing import Router
from .trace import TraceLogger
from .products import CashSettledFuture
from .quota import RollingQuotaTracker
from .transport import BackboneTransport


class FederatedExchangeSimulator:
    """Financial/application layer over the moving interplanetary network.

    Default traces are conditional on no ordinary random packet loss, while all launch
    probabilities are retained. Incidents still cause deterministic launch failure under
    the brief's rules. `stochastic=True` additionally draws ordinary random loss.
    """

    def __init__(self, config: ModelConfig, network: NetworkModel, router: Router,
                 core: str, assignment: Dict[str, str], exchanges: tuple[str, ...],
                 seed: int = 4210):
        self.config = config
        self.network = network
        self.router = router
        self.core = core
        self.assignment = dict(assignment)
        self.exchanges = tuple(exchanges)
        self.rng = np.random.default_rng(seed)
        self.packet_counter = 0
        self.message_counter = 0
        self.session_counter = 0
        self.ledger = Ledger()
        self.trace = TraceLogger()
        self.quota = RollingQuotaTracker(config)
        self.transport = BackboneTransport(network, router, config, self.trace, self.quota,
                                           self._id, self.rng)
        self.knowledge: Dict[str, dict] = {}
        self.futures: Dict[str, CashSettledFuture] = {}
        self.reset_financial_state()

    def reset_financial_state(self) -> None:
        self.ledger = Ledger()
        self.trace = TraceLogger()
        self.quota = RollingQuotaTracker(self.config)
        self.transport = BackboneTransport(self.network, self.router, self.config,
                                           self.trace, self.quota, self._id, self.rng)
        self.knowledge = {}
        self.futures = {}
        for row in self.config.opening_accounts:
            self.ledger.add_account(Account(
                account_id=row["account_id"], settlement=row["settlement"],
                cash_available=float(row["cash"]), shares_available=float(row["shares"]),
            ))

    def _id(self, prefix: str) -> str:
        if prefix == "PKT":
            self.packet_counter += 1
            return f"PKT-{self.packet_counter:06d}"
        if prefix == "SES":
            self.session_counter += 1
            return f"SES-{self.session_counter:06d}"
        self.message_counter += 1
        return f"{prefix}-{self.message_counter:06d}"

    def _snapshot(self) -> dict:
        return {a.account_id: asdict(a) for a in self.ledger.accounts.values()}

    def send_client_message(self, principal: str, sender_settlement: str,
                            exchange_settlement: str, t_ready_days: float,
                            purpose: str, stochastic: bool = False,
                            incident: Optional[Incident] = None,
                            application_retries: int = 1) -> dict:
        msg_id = self._id("MSG")
        if sender_settlement == exchange_settlement:
            m = self.network.direct_message_metrics(sender_settlement, exchange_settlement,
                                                    t_ready_days, incident=incident)
            pkt = self._id("PKT")
            self.trace.packet(
                packet_id=pkt, financial_message_id=msg_id, service="local",
                sender=sender_settlement, receiver=exchange_settlement,
                route=[sender_settlement], emission_time_days=t_ready_days,
                arrival_time_days=m.get("arrival_time_days"), distance_au=0.0,
                loss_probability=0.0, outcome="DELIVERED", purpose=purpose,
                application_attempt=1, quota_exempt=True,
            )
            return {**m, "status": "DELIVERED", "message_id": msg_id, "packet_id": pkt,
                    "application_attempts": 1}

        ready = t_ready_days
        attempts = []
        for attempt in range(1, application_retries + 1):
            # Geometry is known: delay creation until an unobstructed direct path exists.
            candidate_emit = ready + (self.config.local_access_seconds + self.config.serialization_seconds) / SECONDS_PER_DAY
            nxt = self.network.next_open_emission_time(
                sender_settlement, exchange_settlement, candidate_emit,
                service="direct", max_wait_days=self.config.packet_lifetime_days,
            )
            if not nxt.get("found"):
                return {"status": "NO_DIRECT_PATH_WITHIN_LIFETIME", "message_id": msg_id,
                        "attempts": attempts}
            t_emit = float(nxt["emission_time_days"])
            # Packet is originated just before local-access+serialization begins.
            t_origin = t_emit - (self.config.local_access_seconds + self.config.serialization_seconds) / SECONDS_PER_DAY
            pkt = self._id("PKT")
            ok, reason = self.quota.can_originate("direct", principal, t_origin)
            if not ok:
                return {"status": reason, "message_id": msg_id, "attempts": attempts}
            self.quota.record("direct", principal, t_origin, pkt, purpose)
            g = self.network.link_launch(sender_settlement, exchange_settlement, t_emit,
                                         "direct", incident=incident)
            if g.get("status") == "FORCED_FAIL":
                outcome = "LOST_INCIDENT"
            elif stochastic and self.rng.random() < float(g.get("loss_probability", 0.0)):
                outcome = "LOST_RANDOM"
            else:
                outcome = "DELIVERED"
            arr_app = (g.get("arrival_time_days") + self.config.local_access_seconds / SECONDS_PER_DAY
                       if g.get("arrival_time_days") is not None else None)
            row = {
                "packet_id": pkt, "financial_message_id": msg_id, "service": "direct",
                "sender": sender_settlement, "receiver": exchange_settlement,
                "route": [sender_settlement, exchange_settlement],
                "origination_time_days": t_origin, "emission_time_days": t_emit,
                "arrival_time_days": arr_app if outcome == "DELIVERED" else None,
                "nominal_arrival_time_days": arr_app,
                "distance_au": g.get("distance_au"),
                "loss_probability": g.get("loss_probability"),
                "outcome": outcome, "purpose": purpose,
                "application_attempt": attempt,
                "known_link_wait_seconds": max(0.0, (t_emit - candidate_emit) * SECONDS_PER_DAY),
                "quota_exempt": False,
            }
            self.trace.packet(**row)
            attempts.append(row)
            if outcome == "DELIVERED":
                return {**g, "status": "DELIVERED", "message_id": msg_id,
                        "packet_id": pkt, "application_arrival_time_days": arr_app,
                        "application_attempts": attempt, "attempts": attempts}
            ready = ready + self.config.direct_application_retry_hours / 24.0
        return {"status": "DELIVERY_UNKNOWN", "message_id": msg_id, "attempts": attempts}

    def send_backbone_message(self, sender_exchange: str, receiver_exchange: str,
                              t_ready_days: float, purpose: str,
                              incident: Optional[Incident] = None,
                              stochastic: bool = False,
                              application_resubmissions: int = 2) -> dict:
        if sender_exchange == receiver_exchange:
            return {"status": "DELIVERED", "message_id": self._id("MSG"),
                    "route": [sender_exchange], "arrival_time_days": t_ready_days,
                    "conditional_delay_minutes": 0.0}
        msg_id = self._id("MSG")
        start = t_ready_days
        result = self.transport.send_application(
            sender_exchange, receiver_exchange, t_ready_days, purpose, msg_id,
            incident=incident, stochastic=stochastic,
            application_resubmissions=application_resubmissions,
        )
        if result.get("status") == "DELIVERED":
            result["conditional_delay_minutes"] = (
                result["arrival_time_days"] - start
            ) * 1440.0
            route_eval = self.router.evaluate_route(result["route"], start,
                                                    allow_known_wait=True)
            result["initial_success_probability"] = route_eval.get(
                "initial_success_probability") if route_eval else None
        return result

    def open_equity_trade(self, buyer_id: str, seller_id: str, quantity: int, price: float,
                          t0_days: float = 0.0, stochastic: bool = False,
                          incident: Optional[Incident] = None) -> dict:
        obligation_id = self._id("EQ")
        buyer = self.ledger.accounts[buyer_id]
        seller = self.ledger.accounts[seller_id]
        bx = self.assignment[buyer.settlement]
        sx = self.assignment[seller.settlement]
        cash = price * quantity

        bmsg = self.send_client_message(buyer_id, buyer.settlement, bx, t0_days,
                                        "EQUITY_BUY_ORDER", stochastic, incident)
        smsg = self.send_client_message(seller_id, seller.settlement, sx, t0_days,
                                        "EQUITY_SELL_ORDER", stochastic, incident)
        if bmsg["status"] != "DELIVERED" or smsg["status"] != "DELIVERED":
            return {"status": "INCOMPLETE_CLIENT_COMMUNICATION",
                    "buyer_message": bmsg, "seller_message": smsg}
        t_accept = max(bmsg.get("application_arrival_time_days", bmsg.get("arrival_time_days", t0_days)),
                       smsg.get("application_arrival_time_days", smsg.get("arrival_time_days", t0_days)))
        self.ledger.encumber_cash(buyer_id, cash, obligation_id, t_accept)
        self.ledger.encumber_shares(seller_id, quantity, obligation_id, t_accept)
        self.ledger.add_obligation(Obligation(
            obligation_id=obligation_id, product_type="EQUITY", long_account=buyer_id,
            short_account=seller_id, authority=self.core, status="COLLATERAL_LOCKED",
            notional=cash, open_time_days=t_accept,
        ))
        self.trace.event(time_days=t_accept, actor=f"Exchange@{bx}",
                         local_knowledge={"buyer_order": True, "seller_order": True},
                         action="Lock buyer cash and seller shares",
                         financial_state_after=self._snapshot(), obligation_id=obligation_id)

        arrivals = [t_accept]
        for x, purpose in {(bx, "BUY_SIDE_CLEAR"), (sx, "SELL_SIDE_CLEAR")}:
            if x != self.core:
                m = self.send_backbone_message(x, self.core, t_accept, purpose,
                                               incident=incident, stochastic=stochastic)
                if m["status"] != "DELIVERED":
                    return {"status": "CLEARING_INCOMPLETE", "obligation_id": obligation_id,
                            "message": m}
                arrivals.append(m["arrival_time_days"])
        t_clear = max(arrivals)
        self.ledger.obligations[obligation_id].status = "CLEARED"
        self.ledger.settle_equity(buyer_id, seller_id, price, quantity, obligation_id, t_clear)
        self.ledger.obligations[obligation_id].discharge_time_days = t_clear
        self.ledger.obligations[obligation_id].backed_claim_time_days = t_clear

        spend_times = []
        for x in {bx, sx}:
            if x == self.core:
                spend_times.append(t_clear)
            else:
                m = self.send_backbone_message(self.core, x, t_clear,
                                               "SETTLEMENT_CONFIRM", incident=incident,
                                               stochastic=stochastic)
                if m["status"] != "DELIVERED":
                    return {"status": "SETTLED_NOT_YET_SPENDABLE",
                            "obligation_id": obligation_id, "message": m}
                spend_times.append(m["arrival_time_days"])
        t_spend = max(spend_times)
        ob = self.ledger.obligations[obligation_id]
        ob.status = "SPENDABLE"
        ob.spendable_time_days = t_spend
        self.trace.event(time_days=t_spend, actor=f"Clearing@{self.core}",
                         local_knowledge={"cleared": True},
                         action="Equity settlement spendable at both sides",
                         financial_state_after=self._snapshot(), obligation_id=obligation_id)
        return {"status": "SPENDABLE", "obligation_id": obligation_id,
                "discharge_time_days": t_clear, "spendable_time_days": t_spend}

    def open_future(self, long_id: str, short_id: str, quantity: int, multiplier: float,
                    entry_price: float, maturity_hours: float, t0_days: float = 0.0,
                    stochastic: bool = False, incident: Optional[Incident] = None) -> dict:
        cid = self._id("FUT")
        long_acct = self.ledger.accounts[long_id]
        short_acct = self.ledger.accounts[short_id]
        lx = self.assignment[long_acct.settlement]
        sx = self.assignment[short_acct.settlement]

        lmsg = self.send_client_message(long_id, long_acct.settlement, lx, t0_days,
                                        "FUTURE_LONG_ORDER", stochastic, incident)
        smsg = self.send_client_message(short_id, short_acct.settlement, sx, t0_days,
                                        "FUTURE_SHORT_ORDER", stochastic, incident)
        if lmsg["status"] != "DELIVERED" or smsg["status"] != "DELIVERED":
            return {"status": "INCOMPLETE_CLIENT_COMMUNICATION",
                    "long_message": lmsg, "short_message": smsg}
        t_accept = max(lmsg.get("application_arrival_time_days", lmsg.get("arrival_time_days", t0_days)),
                       smsg.get("application_arrival_time_days", smsg.get("arrival_time_days", t0_days)))

        maturity = t0_days + maturity_hours / 24.0
        future = CashSettledFuture(cid, long_id, short_id, quantity, multiplier,
                                   entry_price, maturity, entry_price)
        base_margin = self.config.futures_base_margin_fraction * future.notional
        self.ledger.encumber_cash(long_id, base_margin, cid, t_accept)
        self.ledger.encumber_cash(short_id, base_margin, cid, t_accept)

        arrivals = [t_accept]
        for x, purpose in {(lx, "FUTURE_LONG_CLEAR"), (sx, "FUTURE_SHORT_CLEAR")}:
            if x != self.core:
                m = self.send_backbone_message(x, self.core, t_accept, purpose,
                                               incident=incident, stochastic=stochastic)
                if m["status"] != "DELIVERED":
                    return {"status": "CLEARING_INCOMPLETE", "contract_id": cid,
                            "message": m}
                arrivals.append(m["arrival_time_days"])
        t_open = max(arrivals)

        self.ledger.add_obligation(Obligation(
            obligation_id=cid, product_type="FUTURE", long_account=long_id,
            short_account=short_id, authority=self.core, status="OPEN",
            notional=future.notional, open_time_days=t_open, maturity_time_days=maturity,
        ))
        self.futures[cid] = future
        self.trace.event(time_days=t_open, actor=f"Clearing@{self.core}",
                         local_knowledge={"entry_price": entry_price},
                         action="Open cash-settled future and encumber symmetric base margin",
                         financial_state_after=self._snapshot(), obligation_id=cid)
        return {"status": "OPEN", "contract_id": cid, "notional": future.notional,
                "base_margin_each": base_margin, "open_time_days": t_open}

    def mark_future(self, contract_id: str, price: float, publication_time_days: float,
                    stochastic: bool = False, incident: Optional[Incident] = None) -> dict:
        price_msg = self.send_client_message(
            principal="PRICE_SOURCE", sender_settlement=self.config.price_source_settlement,
            exchange_settlement=self.core, t_ready_days=publication_time_days,
            purpose="PRICE_OBSERVATION", stochastic=stochastic, incident=incident,
            application_retries=(self.config.direct_application_max_attempts if incident else 1),
        )
        if price_msg["status"] != "DELIVERED":
            return {"status": "PRICE_NOT_RECEIVED", "message": price_msg}
        time_days = price_msg.get("application_arrival_time_days",
                                  price_msg.get("arrival_time_days", publication_time_days))

        f = self.futures[contract_id]
        f.latest_price = price
        requirements = {
            "long": f.required_margin("long", price, self.config.futures_base_margin_fraction),
            "short": f.required_margin("short", price, self.config.futures_base_margin_fraction),
        }
        calls = []
        for side, account_id in (("long", f.long_account), ("short", f.short_account)):
            a = self.ledger.accounts[account_id]
            need = requirements[side]
            if a.cash_encumbered + 1e-9 < need:
                top_up = need - a.cash_encumbered
                if a.cash_available + 1e-9 >= top_up:
                    self.ledger.encumber_cash(account_id, top_up, contract_id, time_days)
                    calls.append({"account": account_id, "top_up": top_up, "status": "MET"})
                else:
                    calls.append({"account": account_id, "top_up": top_up, "status": "UNMET"})
                    self.ledger.obligations[contract_id].status = "MARGIN_CALL_UNMET"
        self.trace.event(time_days=time_days, actor=f"Clearing@{self.core}",
                         local_knowledge={"price": price},
                         action="Receive signed price observation; mark future and apply symmetric margin rule",
                         transmission=price_msg, financial_state_after=self._snapshot(),
                         obligation_id=contract_id)
        return {"requirements": requirements, "margin_calls": calls,
                "status": self.ledger.obligations[contract_id].status,
                "observation_received_time_days": time_days,
                "price_message": price_msg}

    def settle_future(self, contract_id: str, final_price: float, time_days: float,
                      stochastic: bool = False, incident: Optional[Incident] = None) -> dict:
        f = self.futures[contract_id]
        pnl_long = f.pnl_long(final_price)
        if pnl_long >= 0:
            payer, payee, amount = f.short_account, f.long_account, pnl_long
        else:
            payer, payee, amount = f.long_account, f.short_account, -pnl_long
        payer_acct = self.ledger.accounts[payer]
        funded = min(amount, payer_acct.cash_encumbered)
        if funded > 0:
            self.ledger.transfer_encumbered_cash(payer, payee, funded, contract_id, time_days)
        for account_id in (f.long_account, f.short_account):
            remaining = self.ledger.accounts[account_id].cash_encumbered
            if remaining > 0:
                self.ledger.release_cash(account_id, remaining, contract_id, time_days)
        ob = self.ledger.obligations[contract_id]
        ob.discharge_time_days = time_days
        ob.backed_claim_time_days = time_days
        ob.status = "FUNDED_DEFAULT" if funded + 1e-9 < amount else "SETTLED_BACKED"

        long_x = self.assignment[self.ledger.accounts[f.long_account].settlement]
        short_x = self.assignment[self.ledger.accounts[f.short_account].settlement]
        spend_times = [time_days]
        for x in {long_x, short_x}:
            if x == self.core:
                spend_times.append(time_days)
            else:
                m = self.send_backbone_message(self.core, x, time_days,
                                               "FUTURE_SETTLEMENT_CONFIRM",
                                               incident=incident, stochastic=stochastic,
                                               application_resubmissions=3)
                if m["status"] == "DELIVERED":
                    spend_times.append(m["arrival_time_days"])
                else:
                    self.trace.event(time_days=time_days, actor=f"Clearing@{self.core}",
                                     local_knowledge={"final_price": final_price},
                                     action="Future discharged/backed but local spendability confirmation incomplete",
                                     transmission=m, financial_state_after=self._snapshot(),
                                     obligation_id=contract_id)
                    return {"status": ob.status, "amount_owed": amount,
                            "amount_funded": funded, "payer": payer, "payee": payee,
                            "spendable": False, "message": m}
        ob.spendable_time_days = max(spend_times)
        if ob.status != "FUNDED_DEFAULT":
            ob.status = "SPENDABLE"
        self.trace.event(time_days=ob.spendable_time_days, actor=f"Clearing@{self.core}",
                         local_knowledge={"final_price": final_price},
                         action="Future settlement/funded default made spendable from already encumbered assets",
                         financial_state_after=self._snapshot(), obligation_id=contract_id)
        return {"status": ob.status, "amount_owed": amount, "amount_funded": funded,
                "payer": payer, "payee": payee, "spendable": True,
                "spendable_time_days": ob.spendable_time_days}

    def metrics(self) -> dict:
        peak_cash_encumbered = sum(a.cash_encumbered for a in self.ledger.accounts.values())
        return {
            "direct_originations": sum(1 for r in self.quota.records if r.service == "direct" and not r.quota_exempt),
            "backbone_originations": sum(1 for r in self.quota.records if r.service == "backbone" and not r.quota_exempt),
            "max_backbone_rolling_24h": self.quota.max_rolling_count("backbone"),
            "current_cash_encumbered": peak_cash_encumbered,
            "opening_cash": sum(r["cash"] for r in self.config.opening_accounts),
        }

    def export(self, directory: str | Path) -> None:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        self.ledger.accounts_df().to_csv(d / "accounts.csv", index=False)
        self.ledger.obligations_df().to_csv(d / "obligations.csv", index=False)
        self.ledger.journal_df().to_csv(d / "ledger_journal.csv", index=False)
        self.trace.export(d)
        self.quota.dataframe().to_csv(d / "quota_originations.csv", index=False)
