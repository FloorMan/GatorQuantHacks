# Design choices: what we picked, what we compared, and the evidence

Generated 2026-10-03 by `python3 -m evidence.experiments --md notes/design-choices.md`. Every number below comes from running the exchange engine (`mpex`) on the network model, with one rule or parameter swapped at a time. Rerun the command after any change and these notes update.

**Test suite at generation time: 17 / 17 scenarios pass**, 10,146 invariant checks (one after every step), cash and shares conserved in every run, 6 double-spend attempts blocked, 0 duplicate settlements, 6 repeat deliveries safely ignored.

**To see the data:** run `python3 serve.py` and open http://localhost:8000/evidence.html. Each scenario has its checks, transaction flow, packet replay on the map, auction allocation, margin charts, balances and timeline.

## At a glance

| Choice | We picked | Compared against | Deciding evidence |
|---|---|---|---|
| Priority at equal price | Home operator's receipt stamp | Arrival time at the market | Arrival time gave the hub-local bidder (C-Finn) the win in 3/3 ties it should have lost |
| Clearing price | Midpoint of the tied range | Seller's limit, buyer's limit | Only rule that splits the surplus evenly (250 / 250) |
| Batch grace window | 3 hop retries | 0, 1, 2 retries | Neptune's slowest hop loses 42.7% of launches; only 3 retries cover the transport's worst case |
| Margin level | 5% per observation | 3%, 8% | Middle on capital ($93,700 peak locked) with 5 calls on the falling path |
| Maintenance trigger | 75% of the allowance | 50%, 100% | 5 calls vs 16 at 100%; fund draw $7,000 vs $10,000 at 50% |
| Margin window | Per location (round trip + retry) | Same for everyone | Location-blind rule defaulted a Neptune trader who paid as fast as light allows |
| Settlement legs | Direct operator-to-operator | Relayed through Ceres | Earth -> Mars 0.6 h vs 1.1 h, 1 quota packet vs 2 |
| Clearing location | Ceres | Earth, Mars | Market speed ties within 2%; Ceres spends 0.8% of the time on one relay (Earth 8.6%) and needs 4 re-routes in 200 yr (Earth 316) |

## 1. Priority at the same price: source stamp, not arrival time

Each remote trader bids $100 one minute **before** C-Finn, who trades at the hub (Ceres), bids the same price. We ran the same batch twice, once with each priority rule.

| Remote bidder | Arrives at Ceres later by | Winner: source stamp (ours) | Winner: arrival time |
|---|---|---|---|
| Earth | 0.5 h | E-Alice | C-Finn |
| Mars | 0.5 h | M-Carla | C-Finn |
| Neptune | 4.1 h | N-Eve | C-Finn |

**Why:** under arrival-time priority the trader nearest the market wins every tie, and Neptune is 4.1 h behind. That turns physics into a permanent advantage and fails the brief's access requirement for distant settlements. With source stamps, the rules are the same for everyone, and only the batch length grows with distance.

**Why the operator's stamp and not the client's:** the brief says clients may lie, so a client-written time could be backdated. The home operator stamps the order when it receives it (1 s of local access). Operators follow the published rules, so the stamp can be trusted.

**Test evidence:** *Equal-price auction*: PASS, 6/6 checks. Earth's bid arrived first, but Neptune's earlier stamp won all 10 shares. When I deliberately switched the engine to arrival-time priority, this test failed on 3 checks, so the test really does catch the difference.

## 2. Clearing price: midpoint of the tied range

A $45 sell meets a $50 buy for 100 shares. Every price from $45 to $50 trades the same volume, so the rule decides who keeps the $500 of surplus.

| Rule | Price | Buyer saves | Seller gains |
|---|---|---|---|
| seller's limit | 45 | $500 | $0 |
| midpoint (chosen) | 47.50 | $250 | $250 |
| buyer's limit | 50 | $0 | $500 |

**Why:** the midpoint is the only one of the three that doesn't systematically favor one side, and it is deterministic, so anyone can recompute it from the published orders. The two limit-based rules hand the whole surplus to one side, every time. Price protection holds under all three, so no trader ever pays more than its limit or receives less.

**Test evidence:** *Cross-planet trade* (PASS) cleared at $47.50, and N-Eve got $250 released at Neptune. *Limit-price protection* (PASS) had five orders clear at $47.50: every fill was within its limit, and the $40 bid was correctly left unfilled.

**When another rule could be better:** with real market makers you might pay the resting order's price to reward liquidity. In a batch with no resting orders, the midpoint is the neutral choice.

## 3. Batch grace window: cover all 3 hop retries

The brief allows 4 launches per hop (the first plus 3 retries), each waiting R_h = 2 × flight + 60 min. We varied how many retries the batch waits for. Batch length is measured at hour 0.5. The test order is sent from Neptune when the batch opens, and its slowest hop loses its first L launches.

| Retries covered | Batch length | Survives 0 lost | 1 lost | 2 lost | 3 lost | No-loss order sent 30 min late | Cross-planet trade complete |
|---|---|---|---|---|---|---|---|
| 0 | 4.5 h | ✓ yes | ✗ no | ✗ no | ✗ no | ✗ no | 12.8 h |
| 1 | 13.2 h | ✓ yes | ✓ yes | ✗ no | ✗ no | ✓ yes | 21.5 h |
| 2 | 22.0 h | ✓ yes | ✓ yes | ✓ yes | ✗ no | ✓ yes | 30.2 h |
| 3 | 30.7 h | ✓ yes | ✓ yes | ✓ yes | ✓ yes | ✓ yes | 39.0 h |

How likely each case is, from the brief's loss formula and today's geometry (slowest hop on each route to Ceres):

| From | Slowest hop | Launch lost | Needs > 0 retries | > 1 | > 2 | > 3 (hop abandoned) |
|---|---|---|---|---|---|---|
| Earth | Earth -> Relay A | 4.4% | 4.4% | 0.2% | 0.0% | 0.0% |
| Mars | Mars -> Relay A | 3.6% | 3.6% | 0.1% | 0.0% | 0.0% |
| Neptune | Neptune -> Relay A | 42.7% | 42.7% | 18.2% | 7.8% | 3.3% |

**Why 3:** the Neptune → Relay A hop is about 28 AU, so 42.7% of launches are lost. With 1 retry covered, a Neptune order still misses the batch 18.2% of the time. With 3 retries covered, it misses only when the hop is abandoned outright (3.3%), and the endpoint retry then carries it to the next batch with its limit unchanged. It is also the only setting where every case the transport allows fits in the window.

**The cost:** the batch grows from 13.2 h to 30.7 h, and a cross-planet trade takes 39.0 h to complete instead of 21.5 h. With no grace at all (4.5 h), even a no-loss Neptune order sent 30 minutes after the batch opens misses it.

**When another value is better:** if speed matters more than a deadline that holds, 1 retry (13.3 h) serves Earth, Mars and Ceres well (their worst hops lose under 5% of launches) and fails only for Neptune-sized distances. A per-batch rule (cover k retries for the settlements actually bidding) is a possible extension.

**Test evidence:** *Packet retry / duplicate protection* (PASS): an order sent at batch open lost 3 launches, landed on the 4th, and still executed in that batch.

## 4. Margin level: 5% per 12-hour observation

Initial margin = notional × move × (1 + observations a participant's round trip can span). The contract is 10 MOI futures at 100 × multiplier 100 (notional $100,000), with N-Eve (Neptune) long and E-Alice (Earth) short. The *falling* path stays within the declared move and N-Eve pays every call. The *crash* path breaks the assumption and N-Eve stops paying.

| Move | IM N-Eve (Neptune) | IM E-Alice (Earth) | Falling: calls | Falling: lowest cushion | Falling: quota packets | Peak cash locked | Crash: fund used | Crash: unbacked |
|---|---|---|---|---|---|---|---|---|
| 3.0% | $9,000 | $6,000 | 7 | $4,100 | 56 | $85,020 | $6,000 | $0 |
| 5.0% | $15,000 | $10,000 | 5 | $8,300 | 50 | $93,700 | $7,000 | $0 |
| 8.0% | $24,000 | $16,000 | 3 | $13,400 | 44 | $106,720 | $1,000 | $0 |

**Why 5%:** it balances capital against cushion on the normal (falling) path. 3% locks the least capital ($85,020 peak) but needs 7 calls and leaves a $4,100 cushion. 8% has the widest cushion and fewest calls but locks $106,720 and prices smaller traders out.

**What the crash run shows, honestly:** the fund draw is not smaller for every increase in margin. 3% drew $6,000 and 5% drew $7,000: a smaller margin breaches maintenance sooner, so the default closes earlier at a better price. Only 8% clearly cuts the crash loss ($1,000). At every level nothing was left unbacked, because the guarantee fund absorbed the rest. If crash protection is the priority, choose 8%.

**When another value is better:** for a more volatile index, use 8%. The rule is the same formula, so only the declared move changes, and it must be declared before any price path is seen.

## 5. Maintenance trigger: 75% of the allowance

| Trigger | Falling: calls | Falling: quota packets | Falling: lowest cushion | Crash: paid to winner | Crash: fund used | Crash: cushion at close |
|---|---|---|---|---|---|---|
| 50.0% | 2 | 41 | $5,000 | $25,000 | $10,000 | $-7,000 |
| 75.0% | 5 | 50 | $8,300 | $22,000 | $7,000 | $0 |
| 100.0% | 16 | 83 | $10,850 | $7,000 | $0 | $12,000 |

**Why 75%:** 100% calls on any drift. That is 16 calls and 75 backbone packets on a normal falling path, which eats the shared 600-packet quota. 50% waits so long that in a crash the margin is gone before the default closes, and the fund pays $10,000. 75% keeps calls rare (5) and draws $3,000 less from the fund than 50%. It does not beat 100% in a crash; it trades that for quota.

**When another value is better:** if the quota is plentiful and crashes are the main worry, 100% has the smallest crash loss ($0 from the fund in this run).

## 6. Margin window by location, not one window for everyone

Our rule gives each participant a risk window equal to its real round trip to Ceres Clearing plus one hop retry. The window sets both its margin and its call deadline. The alternative gives everyone the same 0 h window, as if they were all at Ceres.

| Participant | Our window |
|---|---|
| E-Alice | 2.7 h |
| E-Bob | 2.7 h |
| M-Carla | 2.5 h |
| M-Diego | 2.5 h |
| N-Eve | 16.9 h |
| C-Finn | 0.0 h |

| Rule | N-Eve pays every call? | First call | Deadline | Top-up landed | Outcome |
|---|---|---|---|---|---|
| Same window for all | yes | 24.5 h | 24.5 h | 32.7 h | **funded default** at 94 |
| Per location (ours) | yes | 36.5 h | 53.4 h | 44.7 h | settled, all 5 calls met |

**Why:** with one window for everyone, N-Eve's deadline came before any reply could physically arrive, so she was defaulted even though she paid as fast as light allows. That is a wrongful default, and it fails the brief's access requirement for Neptune. In the crash path that rule used less of the fund ($0), but only because it closed out paying and non-paying traders alike at the first dip.

## 7. Settlement legs go directly between operators

| Leg | Direct session | Relayed through Ceres | Quota packets (direct vs relayed) |
|---|---|---|---|
| Earth -> Mars | 0.6 h | 1.1 h | 1 vs 2 |
| Earth -> Neptune | 4.2 h | 4.7 h | 1 vs 2 |
| Mars -> Neptune | 4.1 h | 4.6 h | 1 vs 2 |

**Why:** a direct leg is faster and costs half the quota of the shared 600. The cost is 6 sessions set up before hour 0 (each SYN costs 1 packet), which the brief allows from hour −168.

## 8. Clearing house and batch market at Ceres

Every scenario rerun with each hub (`python3 -m evidence.compare_hubs`, `notes/hub-earth-vs-ceres.md`):

| Measure | Earth hub | Ceres hub |
|---|---|---|
| Cross-planet trade complete | 38.6 h | 38.5 h |
| Batch length at hour 0 | 30.8 h | 30.7 h |
| Margin-call response (mean) | 8.3 h | 8.2 h |
| Clearing outage: service lost | 81.6 h | 80.7 h |
| Backbone quota packets, all 17 | 178 | 234 |
| Winner spendable after close | 0.0 h | 0.5 h |

| Network factor (200 years) | Earth | Ceres |
|---|---|---|
| Delivery, relays switched ahead of blockages | 94.23% | 94.28% |
| Time on a single relay | 8.6% | 0.8% |
| Relay re-routes needed | 316 | 4 |
| Longest single-relay blockage | 21 days | 190 days |
| Worst-decade expected delay incl. retries | 192.8 min | 215.5 min |

**Why Ceres:** market speed ties within 2% (Neptune's ~4 h one-way dominates every hub). Ceres is the more robust hub: it spends a tenth as much time on a single relay, so it is far less exposed to one relay outage, and it needs 4 planned re-routes in 200 years instead of 316. Its rare blockages last up to 190 days, so the operators switch sessions to the other relay before each one (blockages are predictable from the geometry, and the two relays are never blocked together).

**What it costs:** with the current balance sheet most collateral sits at Earth, so margin and guarantee cash must travel to Ceres: about 31% more backbone quota packets across the 17 scenarios, and Earth winners wait about 0.5 h to spend payouts. Ceres's worst decade is also 22 minutes slower than Earth's. Moving the large accounts or the guarantee contributors to Ceres would remove the quota cost.

## Test evidence

| Scenario | Result | Checks |
|---|---|---|
| Local trade | ✓ PASS | 9/9 |
| Cross-planet trade | ✓ PASS | 11/11 |
| Equal-price auction | ✓ PASS | 6/6 |
| Exact tie / pro-rata | ✓ PASS | 4/4 |
| Partial fill | ✓ PASS | 5/5 |
| Late order | ✓ PASS | 9/9 |
| Limit-price protection | ✓ PASS | 8/8 |
| Cancellation before execution | ✓ PASS | 6/6 |
| Cancellation after execution | ✓ PASS | 4/4 |
| Packet retry / duplicate protection | ✓ PASS | 8/8 |
| Uncertain settlement locking | ✓ PASS | 6/6 |
| Settlement finality | ✓ PASS | 4/4 |
| Futures opening | ✓ PASS | 7/7 |
| Margin call | ✓ PASS | 8/8 |
| Funded default | ✓ PASS | 8/8 |
| Disconnection | ✓ PASS | 5/5 |
| Clearing-house outage/recovery | ✓ PASS | 5/5 |

## Limits of this evidence

- Traces are conditional: a packet is lost only where a scenario forces a loss or an incident covers the launch. The probabilities in section 3 come from the brief's formula, not from random runs.
- Idle sessions are not expired, and link queues and capacity are not simulated.
- Price paths are scripted test inputs, as the brief requires. The margin results describe these paths, not a forecast.
- The relay placement is fixed by the brief. The what-if in `network/python/relay_placement.py` is an extension only.

