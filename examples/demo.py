"""Walk a small cross-settlement trade through the exchange and print the trace.

Run from the repository root:  python3 examples/demo.py
"""

import json
import sys
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mpex import (Equity, Exchange, Future, OpeningAccount, OpeningBalanceSheet,
                  SessionState)

ex = Exchange()
ex.open(OpeningBalanceSheet((
    OpeningAccount("Earth Investors", "Earth", D(200000)),
    OpeningAccount("Mars Builders", "Mars", D(100000), {"AresHabitat": 3000}),
    OpeningAccount("Ceres Mining", "Ceres", D(100000), {"AresHabitat": 1000}),
    OpeningAccount("Jupiter Traders", "Jupiter", D(100000)),
)), at_h=-24)

# Institutions and products.
ex.charter_institution(-24, "Earth Exchange", "Earth", ["operator"])
ex.charter_institution(-24, "Mars Exchange", "Mars", ["operator"])
ex.register_price_source(-24, "Ares Index", "Earth")
ex.list_instrument(-24, Equity(symbol="ARES", venue="Mars", base_asset="SHR:AresHabitat"))
ex.list_instrument(-24, Future(symbol="ARES-F300", venue="Earth", underlying="ARES",
                               multiplier=D(10), maturity_h=300, price_source="Ares Index"))

# Pre-hour-0 session setup (the only thing allowed before hour 0).
sid = ex.open_session(-24, "Earth Exchange", "Mars Exchange", ["Earth", "Relay A", "Mars"])
ex.set_session_state(-23, sid, SessionState.ESTABLISHED)

# Hour 0+: Earth Investors moves cash to Mars and buys shares there.
xfr = ex.initiate_transfer(0, "Earth Investors", "NEO", 20000, "Earth", "Mars",
                           note="Earth Exchange debits the Earth ledger; value is in transit")
msg = ex.send_message(0, "Earth Exchange", "Mars Exchange", "backbone", "official",
                      "settlement_instruction", {"transfer": xfr}, session_id=sid,
                      references=[xfr])
pkt = ex.state.messages[msg].packet_ids[0]
ex.record_launch(0.0003, pkt, "Earth", "Relay A", 0.2, lost=False)
ex.record_launch(0.2006, pkt, "Relay A", "Mars", 0.31, lost=False)
ex.set_message_status(0.31, msg, "delivered")
ex.complete_transfer(0.31, xfr, note="Mars Exchange credits the Mars ledger")

ex.submit_order(0.5, "Mars Builders", "ARES", "sell", 300, 50)
ex.submit_order(0.6, "Earth Investors", "ARES", "buy", 300, 52)

print(f"{'hour':>9}  {'event':<22} {'actor':<16} note")
for e in ex.journal:
    print(f"{e.time_h:>9.4f}  {e.type.value:<22} {str(e.actor):<16} {e.note or ''}")

print("\nEarth Investors holdings:", json.dumps(ex.holdings("Earth Investors"), indent=2))
print("\nState at hour 0.1 (transfer in flight):")
print(json.dumps(ex.state_at(0.1).holdings("Earth Investors"), indent=2))
print("\nMetrics:", json.dumps(ex.snapshot()["metrics"], indent=2))
print("Invariant violations:", ex.check_invariants())
