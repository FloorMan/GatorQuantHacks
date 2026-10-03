# MultiPlanetary Exchange System

GatorQuantHacks entry. The design brief is `MultiPlanetary_Exchange_System_Participant_Brief.pdf`, and the orbital and network data are in `Data/`.

## `mpex`: exchange state model

`mpex` is a dependency-free Python (3.10+) package. It holds the complete state of the exchange at any moment: accounts, located balances, encumbrances, orders, trades, open obligations, transfers in flight, price observations, sessions, messages, packets, and quota use.

```
python3 examples/demo.py                    # walk a cross-settlement trade and print its trace
python3 -m unittest discover -s tests -v    # run the tests
```

### Architecture

```
            commands (validate)                 events                    state
 caller ──► Exchange.submit_order(...) ──► Journal (append-only) ──► ExchangeState
            Exchange.open_position(...)        │                       ├─ principals
            Exchange.initiate_transfer(...)    │ replay                ├─ Ledger (owner, asset, settlement)
            Exchange.send_message(...)         ▼                       │    └─ Encumbrances
            ...                          Exchange.state_at(t)          ├─ instruments, order books
                                                                       ├─ orders, trades, positions
                                                                       ├─ transfers, observations
                                                                       ├─ sessions, messages, packets
                                                                       ├─ QuotaTracker
                                                                       └─ Metrics
```

* **Event-sourced.** Every change is an `Event` (time, type, actor, data, and an optional note for "what this actor knows"). Commands validate first and then record events. Handlers (`Exchange._on_<type>`) apply them. If a handler fails, the state rolls back and nothing is journaled.
* **State at any point.** `ex.state_at(h)` replays the journal up to hour `h`. `ex.snapshot()` gives a JSON-safe view, used for the "financial state after" column in traces. `ex.trace(id)` returns every event that touched an order, trade, position, transfer or message. `Journal.save()` and `Journal.load()` persist the journal as JSON lines.
* **Locality is in the data model.** Balances are keyed by `(owner, asset, settlement)`. Value at Earth cannot pay for anything on Mars until a `Transfer` completes. While it travels, it is held *in transit*, outside both ledgers.
* **One use per asset.** Every reserved amount (order collateral, margin) is an `Encumbrance` against a specific located balance. A payment out of reserved value comes only from its encumbrance, so the payer never sees that value as available in between.
* **Invariants.** `ex.check_invariants()` checks the following:
  * Each asset's ledger supply plus its in-transit amount equals the opening supply.
  * No balance is negative or over-encumbered.
  * Every resting order's collateral is exact.
  * No encumbrance backs a closed obligation.
  * No position has an unbacked shortfall.

### Modules

| Module | Holds |
|---|---|
| `constants.py` | Fixed parameters from Section 2 of the brief: settlements, node IDs, quotas, timers, limits, maintenance windows |
| `balance_sheet.py` | `OpeningBalanceSheet` and its limit checks: at most $500k and 5,000 shares, 4–10 accounts, at least 3 settlements |
| `principals.py` | `Account`, `Institution` (roles: operator, clearing, settlement, agent, guarantor; at most 12), `PriceSource` |
| `ledger.py` | Located balances and `Encumbrance`s |
| `instruments.py` | `Equity`, `Future`, `Option` (with payoffs), and `Bond`, `Loan`, `Currency` (specification only) |
| `trading.py` | `Order`, `Trade`, and a price-time `OrderBook` with no self-trades |
| `positions.py` | `Position` (records the discharge, backed-claim and spendable moments) and `MarginPolicy` |
| `transfers.py` | Cross-settlement `Transfer` (in transit → completed or returned) |
| `observations.py` | Signed `PriceObservation`s released at one settlement |
| `comms.py` | `Session`, `Message`, `Packet`, `Launch`, route rules, and `QuotaTracker` (rolling 24 h windows) |
| `journal.py` | `Event`, `EventType`, `Journal` |
| `state.py` | `ExchangeState` container and `Metrics` (peak encumbrance, asset-hours, value settled, packet counts) |
| `exchange.py` | `Exchange`: commands, event handlers, queries, invariants |

### Rules enforced today

* No financial action before hour 0. Sessions may be set up from hour −168.
* Event time never goes backwards.
* An equity order must have its collateral free at the instrument's venue. Delivery-versus-payment settlement happens atomically on the venue ledger at execution.
* Futures and options:
  * Both sides must meet the margin rule at opening.
  * Marks recompute margin requirements and issue margin calls.
  * Money moves once, at settlement or default, paid from posted margin only.
  * If the winner lives at another settlement, the position becomes spendable only when the transfer home completes.
* Official coordination must use the backbone. Only operators and their clearing or settlement services may originate backbone traffic.
* Local access requires both parties at the same settlement. Direct traffic connects different settlements.
* Backbone routes are simple paths of at most 3 links over the 19 candidate links.
* Quotas:
  * Backbone: 600 originations per rolling 24 h, shared, including SYNs.
  * Direct: 12 per principal per rolling 24 h, with launches 60 s apart.
  * Transport ACKs and receipts are exempt but still counted in totals.

### Batch market, settlement legs, and delay-aware margin

Added for the design paper's rules (see `mpex/batch.py`):

* **Global batch auction** for cross-settlement equity orders, held at Earth. Collateral is locked at the trader's home ledger before the order leaves home. Priority goes to the home operator's receipt stamp, not arrival time. One uniform price (maximum volume, then minimum imbalance, then the midpoint of the tied range). Exact ties split pro rata in whole shares, with the remainder ordered by sha256(batch:order). The batch stays open for 1.1 × the slowest eligible one-way delay plus a grace window covering all 3 hop retries (4 launches per hop), so an order that needs every retry still arrives in time. Late orders roll to the next batch with their limit unchanged. Cancels take effect only before execution.
* **Two-leg settlement.** Each home operator applies the batch result to its own ledger: filled quantity leaves the lock as an in-transit transfer to the counterparty's home, and the rest is released. A trade completes when both legs are usable.
* **At-most-once delivery** (`deliver_transfer`) and **source reconciliation** (`confirm_transfer`). A repeated transaction id is recorded and ignored.
* **Guarantee fund** (`contribute_guarantee`), funded from accounts after hour 0. It is the third step of the default waterfall, after the defaulter's margin.
* **`CommunicationRiskMarginPolicy`.** Initial margin covers the worst move in the observations a margin call's round trip (plus one hop retry) can span; maintenance is 75% of that allowance plus the current loss. The base `MarginPolicy` behaves exactly as before.

## Tests & Evidence dashboard

```
python3 serve.py                 # http://localhost:8000/evidence.html (map at /)
python3 -m evidence.runner       # same 17 scenarios in the terminal
```

`evidence/` runs 17 scenarios on `mpex` and the network model in time order (`evidence/world.py`). The run includes hop and endpoint retries, receipts, Sun and maintenance waits, and labelled incidents, and invariants are checked after every step. The dashboard shows each scenario's real output: checks with expected and actual values, the transaction flow and route, a replay of the packets on the solar-system map, the auction allocation and reasons, futures margin charts, balances before and after with conservation, and the full timeline. A scenario passes only if all of its checks pass, no invariant breaks, cash and shares are conserved, and nothing settles twice. The scenarios also run under `python3 -m unittest`.

Traces are conditional (no random loss): a packet is lost only where a scenario forces a loss or an incident covers the launch, and each loss is labelled. Simplifications: sessions are not expired for idleness (scenarios keep traffic flowing or run under 7 days of idle time), and link capacity and queues are not simulated.

### Not built yet

* The transport simulation (hop and endpoint retries, queues, sessions over time), and wiring the network model into `mpex`. The orbital propagator, light time, visibility, maintenance, loss and route timing now live in `network/` (see below); the simulation should write its results through `record_launch`, `set_message_status` and `set_session_state`.
* Order-book trading for derivatives (positions are currently opened bilaterally).
* Lifecycles for bonds, loans and currencies.
* Per-actor knowledge (who has received which observation).
* Rollback deep-copies the state on each command. That is fine at scenario scale; it would need replacing for very long runs.

## `network/`: orbits, light time, and routing

The physical network model, separate from `mpex`. See `network/README.md`.

```
open network/index.html                                              # interactive 2D network map
python3 network/python/network_graph.py --from Earth --to Neptune    # routes, hop timings, loss and abandonment probabilities
node network/scripts/check_epoch.js                                  # epoch check against the brief's Section 3 table
```

It implements the brief's launch rules: moving-receiver light time to 1 ms, the 0.10 AU solar exclusion tested on the emission-to-arrival segment, maintenance on the flight interval, 1 s serialization per launch and 1 s relay processing, and the four simple backbone routes of at most 3 links.
