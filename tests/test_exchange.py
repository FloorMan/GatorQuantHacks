import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from mpex import (Equity, EventType, Exchange, ExchangeError, Future, InstitutionRole,
                  Journal, MessageStatus, OpeningAccount, OpeningBalanceSheet, Option,
                  OrderStatus, PositionState, QuotaExceeded, SessionState, TimeOrderError,
                  TransferStatus, ValidationError)

D = Decimal


def balance_sheet():
    return OpeningBalanceSheet((
        OpeningAccount("Earth Investors", "Earth", D(200000)),
        OpeningAccount("Mars Builders", "Mars", D(100000), {"AresHabitat": 3000}),
        OpeningAccount("Ceres Mining", "Ceres", D(100000), {"AresHabitat": 1000}),
        OpeningAccount("Jupiter Traders", "Jupiter", D(100000)),
    ))


def opened_exchange():
    ex = Exchange()
    ex.open(balance_sheet())
    ex.charter_institution(0, "Earth Exchange", "Earth", [InstitutionRole.OPERATOR])
    ex.charter_institution(0, "Earth Clearing", "Earth", [InstitutionRole.CLEARING],
                           operator="Earth Exchange")
    ex.charter_institution(0, "Mars Exchange", "Mars", [InstitutionRole.OPERATOR])
    ex.register_price_source(0, "Ares Index", "Earth")
    ex.list_instrument(0, Equity(symbol="ARES", venue="Mars", base_asset="SHR:AresHabitat"))
    ex.list_instrument(0, Future(symbol="ARES-F300", venue="Earth", underlying="ARES",
                                 multiplier=D(10), maturity_h=300, price_source="Ares Index"))
    return ex


class BalanceSheetTests(unittest.TestCase):
    def test_limits(self):
        balance_sheet().validate()
        too_rich = OpeningBalanceSheet(balance_sheet().accounts[:3] + (
            OpeningAccount("Whale", "Venus", D(100001)),))
        with self.assertRaises(ValidationError):
            too_rich.validate()
        two_sites = OpeningBalanceSheet(tuple(
            OpeningAccount(f"A{i}", "Earth" if i % 2 else "Mars", D(1)) for i in range(4)))
        with self.assertRaises(ValidationError):
            two_sites.validate()


class TradingTests(unittest.TestCase):
    def test_order_match_settles_dvp_and_conserves(self):
        ex = opened_exchange()
        sell, trades = ex.submit_order(1, "Mars Builders", "ARES", "sell", 100, 50)
        self.assertEqual(trades, [])
        # Ceres Mining has no cash at Mars yet: order must be rejected.
        with self.assertRaises(ValidationError):
            ex.submit_order(1, "Ceres Mining", "ARES", "buy", 10, 60)
        xfr = ex.initiate_transfer(1, "Ceres Mining", "NEO", 10000, "Ceres", "Mars")
        ex.complete_transfer(1.5, xfr)
        buy, trades = ex.submit_order(2, "Ceres Mining", "ARES", "buy", 60, 55)
        self.assertEqual(len(trades), 1)
        led = ex.state.ledger
        # Trade at the resting price 50; price improvement released.
        self.assertEqual(led.balance("Ceres Mining", "SHR:AresHabitat", "Mars"), 60)
        self.assertEqual(led.balance("Ceres Mining", "NEO", "Mars"), D(10000 - 3000))
        self.assertEqual(led.encumbered("Ceres Mining", "NEO", "Mars"), 0)
        self.assertEqual(led.balance("Mars Builders", "NEO", "Mars"), D(103000))
        self.assertEqual(led.encumbered("Mars Builders", "SHR:AresHabitat", "Mars"), 40)
        self.assertEqual(ex.state.orders[sell].status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(ex.state.orders[buy].status, OrderStatus.FILLED)
        ex.cancel_order(3, sell)
        self.assertEqual(led.encumbered("Mars Builders", "SHR:AresHabitat", "Mars"), 0)
        self.assertEqual(ex.check_invariants(), [])

    def test_asset_cannot_back_two_orders(self):
        ex = opened_exchange()
        ex.submit_order(1, "Mars Builders", "ARES", "sell", 3000, 50)
        with self.assertRaises(ValidationError):
            ex.submit_order(1, "Mars Builders", "ARES", "sell", 1, 50)

    def test_no_financial_action_before_hour_zero(self):
        ex = Exchange()
        ex.open(balance_sheet(), at_h=-100)
        ex.charter_institution(-100, "Earth Exchange", "Earth", ["operator"])
        with self.assertRaises(TimeOrderError):
            ex.initiate_transfer(-1, "Earth Investors", "NEO", 1, "Earth", "Mars")


class PositionTests(unittest.TestCase):
    def setUp(self):
        self.ex = opened_exchange()
        xfr = self.ex.initiate_transfer(0, "Jupiter Traders", "NEO", 50000, "Jupiter", "Earth")
        self.ex.complete_transfer(1, xfr)

    def open_future(self):
        # Margin rule: 25% of notional (10 x 10 x 100 = 10,000) => 2,500 each.
        return self.ex.open_position(2, "ARES-F300", "Earth Investors", "Jupiter Traders",
                                     10, 100, long_margin=5000, short_margin=5000)

    def test_margin_rule_enforced(self):
        with self.assertRaises(ValidationError):
            self.ex.open_position(2, "ARES-F300", "Earth Investors", "Jupiter Traders",
                                  10, 100, long_margin=1000, short_margin=5000)

    def test_rising_price_settles_from_margin(self):
        pid = self.open_future()
        o1 = self.ex.release_observation(100, "Ares Index", "ARES", 115)
        self.ex.mark_position(101, pid, o1)
        with self.assertRaises(ValidationError):
            self.ex.settle_position(299, pid, [o1])  # not yet mature
        o2 = self.ex.release_observation(300, "Ares Index", "ARES", 125)
        self.ex.settle_position(300, pid, [o2])
        pos = self.ex.state.positions[pid]
        self.assertEqual(pos.state, PositionState.SETTLED)
        self.assertEqual(pos.long_payoff, D(2500))  # 10 x 10 x (125 - 100)
        led = self.ex.state.ledger
        self.assertEqual(led.balance("Earth Investors", "NEO", "Earth"), D(202500))
        self.assertEqual(led.balance("Jupiter Traders", "NEO", "Earth"), D(47500))
        self.assertEqual(led.encumbered_total("NEO"), 0)
        self.assertEqual(pos.spendable_h, 300)  # winner lives at the venue
        self.assertEqual(self.ex.check_invariants(), [])

    def test_falling_price_margin_call_and_funded_default(self):
        pid = self.open_future()
        obs = self.ex.release_observation(50, "Ares Index", "ARES", 60)
        self.ex.mark_position(51, pid, obs)
        pos = self.ex.state.positions[pid]
        # Long loses 4,000 and needs 1,500 adverse-move cover: 5,500 > 5,000 posted.
        self.assertEqual(pos.margin_calls, {"long": "500.00"})
        self.ex.declare_default(80, pid, "Earth Investors")
        self.assertEqual(pos.state, PositionState.FUNDED_DEFAULT)
        self.assertEqual(pos.paid, D(4000))
        self.assertEqual(pos.shortfall, 0)
        # Winner (short) lives at Jupiter: spendable only after transfer home.
        self.assertIsNone(pos.spendable_h)
        xfr = self.ex.initiate_transfer(81, "Jupiter Traders", "NEO", 4000, "Earth", "Jupiter",
                                        reference=pid)
        self.ex.complete_transfer(90, xfr)
        self.assertEqual(pos.spendable_h, 90)
        self.assertEqual(self.ex.check_invariants(), [])

    def test_option_premium_and_payoff(self):
        self.ex.list_instrument(1, Option(symbol="ARES-C110", venue="Earth", underlying="ARES",
                                          strike=D(110), multiplier=D(10), maturity_h=300,
                                          price_source="Ares Index"))
        self.ex.release_observation(1, "Ares Index", "ARES", 100)
        pid = self.ex.open_position(2, "ARES-C110", "Earth Investors", "Jupiter Traders",
                                    10, 4, short_margin=2000)
        led = self.ex.state.ledger
        self.assertEqual(led.balance("Jupiter Traders", "NEO", "Earth"), D(50400))
        final = self.ex.release_observation(300, "Ares Index", "ARES", 130)
        self.ex.settle_position(300, pid, [final])
        self.assertEqual(self.ex.state.positions[pid].long_payoff, D(2000))
        self.assertEqual(self.ex.check_invariants(), [])


class TransferTests(unittest.TestCase):
    def test_in_transit_value_is_conserved_and_returnable(self):
        ex = opened_exchange()
        xfr = ex.initiate_transfer(1, "Earth Investors", "NEO", 1000, "Earth", "Neptune")
        self.assertEqual(ex.state.ledger.total_supply("NEO"), D(499000))
        self.assertEqual(ex.check_invariants(), [])
        ex.return_transfer(400, xfr, "route abandoned")
        self.assertEqual(ex.state.transfers[xfr].status, TransferStatus.RETURNED)
        self.assertEqual(ex.state.ledger.balance("Earth Investors", "NEO", "Earth"), D(200000))
        with self.assertRaises(ExchangeError):
            ex.complete_transfer(401, xfr)


class CommsTests(unittest.TestCase):
    def test_session_message_and_quotas(self):
        ex = opened_exchange()
        sid = ex.open_session(0, "Earth Exchange", "Mars Exchange",
                              ["Earth", "Relay A", "Mars"])
        with self.assertRaises(Exception):
            ex.send_message(0.1, "Earth Exchange", "Mars Exchange", "backbone", "official",
                            "settlement_instruction", {"x": 1}, session_id=sid)
        ex.set_session_state(0.2, sid, SessionState.ESTABLISHED)
        mid = ex.send_message(0.3, "Earth Exchange", "Mars Exchange", "backbone", "official",
                              "settlement_instruction", {"transfer": "XFR-1"},
                              size_bytes=2000, session_id=sid)
        msg = ex.state.messages[mid]
        self.assertEqual(len(msg.packet_ids), 3)  # 2000 bytes / 960-byte payloads
        ex.record_launch(0.31, msg.packet_ids[0], "Earth", "Relay A", 0.5, lost=False)
        self.assertEqual(msg.status, MessageStatus.IN_FLIGHT)
        ex.set_message_status(0.9, mid, "delivered")
        self.assertEqual(ex.state.sessions[sid].last_rx_h, 0.9)
        self.assertEqual(ex.state.quotas.backbone_used(1), 4)  # SYN + 3 data

        # Official coordination may not use the direct service.
        with self.assertRaises(ValidationError):
            ex.send_message(1, "Earth Exchange", "Mars Exchange", "direct", "official", "x")
        # Clients may not use the backbone.
        with self.assertRaises(ValidationError):
            ex.send_message(1, "Earth Investors", "Mars Builders", "backbone", "client", "x",
                            session_id=sid)
        # Direct quota: 12 per rolling 24 h, launches 60 s apart.
        for _ in range(12):
            ex.send_message(1, "Earth Investors", "Mars Builders", "direct", "client", "order")
        with self.assertRaises(QuotaExceeded):
            ex.send_message(1, "Earth Investors", "Mars Builders", "direct", "client", "order")
        times = ex.state.quotas.direct["Earth Investors"]
        self.assertAlmostEqual(times[1] - times[0], 60 / 3600)
        ex.send_message(30, "Earth Investors", "Mars Builders", "direct", "client", "order")

    def test_route_rules(self):
        ex = opened_exchange()
        with self.assertRaises(ValidationError):
            ex.open_session(0, "Earth Exchange", "Mars Exchange", ["Earth", "Mars"])
        with self.assertRaises(ValidationError):
            ex.open_session(0, "Earth Exchange", "Earth Investors",
                            ["Earth", "Relay A", "Earth"])


class ReplayTests(unittest.TestCase):
    def build(self):
        ex = opened_exchange()
        ex.submit_order(1, "Mars Builders", "ARES", "sell", 100, 50)
        xfr = ex.initiate_transfer(2, "Earth Investors", "NEO", 20000, "Earth", "Mars")
        ex.annotate(2, "Earth Investors", "knows nothing about Mars order book yet")
        ex.complete_transfer(3, xfr)
        ex.submit_order(4, "Earth Investors", "ARES", "buy", 100, 50)
        return ex

    def test_state_at_reconstructs_history(self):
        ex = self.build()
        past = ex.state_at(2.5)
        self.assertEqual(past.state.ledger.balance("Earth Investors", "NEO", "Mars"), 0)
        self.assertEqual(past.state.in_transit("NEO"), D(20000))
        self.assertEqual(past.check_invariants(), [])
        now = ex.state_at(10)
        self.assertEqual(now.snapshot()["ledger"], ex.snapshot()["ledger"])
        self.assertEqual(now.state.ids.snapshot(), ex.state.ids.snapshot())

    def test_failed_command_leaves_no_trace(self):
        ex = self.build()
        before = (len(ex.journal), ex.snapshot())
        with self.assertRaises(Exception):
            ex.submit_order(5, "Jupiter Traders", "ARES", "buy", 1, 50)
        self.assertEqual((len(ex.journal), ex.snapshot()), before)
        with self.assertRaises(TimeOrderError):
            ex.annotate(1, None, "too early")

    def test_journal_round_trip_and_trace(self):
        ex = self.build()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "journal.jsonl"
            ex.journal.save(path)
            again = Exchange.from_events(Journal.load(path))
        self.assertEqual(again.snapshot(), ex.snapshot())
        trade_id = next(iter(ex.state.trades))
        types = [e.type for e in ex.trace(trade_id)]
        self.assertEqual(types, [EventType.TRADE_EXECUTED])

    def test_metrics(self):
        ex = self.build()
        m = ex.snapshot(as_of_h=5)["metrics"]
        self.assertEqual(m["value_settled"], "25000")  # transfer 20,000 + trade 5,000
        self.assertEqual(m["completed_transactions"], 2)
        # 100 shares encumbered from h1 to h4 = 300 share-hours.
        self.assertEqual(D(m["asset_hours"]["SHR:AresHabitat"]), 300)


if __name__ == "__main__":
    unittest.main()
