# Federated MultiPlanetary Exchange Model

Runnable Python model for the MultiPlanetary Exchange System brief using the supplied frozen `Alpha_Orbital_Data.zip`.

The model uses a **federated execution / single authoritative clearing-and-settlement core** architecture. Financial guarantees are hard constraints; network placement is optimized only among designs whose modeled application workload respects the direct and shared-backbone origination quotas.

## Current optimized baseline

The current 3–5-exchange placement screen selects:

- Exchanges: **Mercury, Venus, Earth, Mars, Ceres**
- Authoritative clearing/settlement core: **Ceres**
- Client assignment:
  - Mercury -> Mercury
  - Venus -> Venus
  - Earth -> Earth
  - Mars -> Mars
  - Ceres -> Ceres
  - Jupiter -> Ceres
  - Saturn -> Earth
  - Uranus -> Ceres
  - Neptune -> Earth

The placement screen is a topology-selection heuristic, not the E4 long-horizon evidence. The selected topology is subsequently evaluated by the separate 200-Julian-year E4 scan.

## Chosen workload/economic assumptions

- Equal baseline demand: **4 client application instructions per funded principal per day**.
- Optimizer may choose **3 to 5 exchange locations**.
- One authoritative clearing/settlement core; regional exchanges provide local/federated execution.
- Financial guarantees are hard constraints.
- Direct reliability target used in placement ranking: **99% when feasible under the direct quota**. If communication does not complete, financial finality is not invented; the operation remains incomplete or follows the modeled recovery path.
- Regional batching/netting is enabled on a **6-hour batch interval**.
- Products: **equities** and **cash-settled futures**.
- Stress balance sheet: 9 accounts, one per settlement, exactly **500,000 NeoDollars and 5,000 shares**.

## Fixed-width application encoding

The old screening assumption about records per packet has been removed. The implemented application wire format is exact and fixed-width:

- Network packet: **1,024 bytes**
- Network header: **64 bytes** (brief-fixed)
- Application payload: **960 bytes**
- Application envelope: **32 bytes**
- Financial record: **96 bytes**
- Capacity: **9 financial records per application packet**
- Remaining padding at full record capacity: **64 bytes**

`interplanetary_exchange/encoding.py` builds the actual 960-byte payload with `struct.Struct`; placement and workload packet counts call the same encoder's `packet_count()` method. See `WIRE_FORMAT.md` for the field layout.

## Exact rolling quota tooling

`RollingQuotaTracker` stores an exact timestamp for every application/control origination.

- Backbone: shared **600 originated packets per rolling 24 h**.
- Direct: **12 packets per principal per rolling 24 h**, with at least **60 s** between that principal's direct launches.
- Quota-exempt automatic transport messages remain in packet traces but are marked exempt from origination quota accounting.

The final selected topology is regenerated with moving-geometry arrival times and written to:

- `output/final_workload_packet_schedule.csv`
- `output/final_workload_quota_ledger.csv`

The current 3-day workload reaches a maximum modeled shared-backbone rolling-24h count of **36**, so it is well below the 600-packet ceiling.

## Transport/session model

`interplanetary_exchange/transport.py` implements the incident-trace transport behavior used by the scenarios:

- pinned simple backbone routes with at most 3 links;
- 3-message SYN / SYN-ACK / final-ACK session setup;
- reusable sessions and 7-day inactivity expiry;
- endpoint-reset invalidation and re-handshake;
- directed FIFO link queues at 1 packet/s, with 10,000-packet capacity;
- 1-second serialization and relay processing;
- next-open-link waiting for known solar closures and scheduled maintenance;
- hop timer `R_h = 2 * one-way flight time + 60 min`, up to 4 launches;
- hop receipts and duplicate-safe forwarding behavior;
- endpoint timer `R_e = 2 * T0 + 24 h`, up to 4 endpoint attempts;
- packet lifetime enforcement;
- application resubmission across sessions as new quota-consuming traffic;
- trace fields for endpoint attempts, hop attempts, queue depth, queue wait, known-link wait, and nominal/actual arrival.

The simulator intentionally separates **transport delivery** from **financial acceptance/finality**.

## Orbital/network model

Implemented from the supplied frozen data:

- fixed-Kepler propagation for all nine settlements;
- Relay A/B circular heliocentric orbits at radius `sqrt(8)` AU with 45°/135° epoch phases;
- epoch-vector validation;
- moving-receiver light-time solution to 1 ms tolerance;
- 0.10-AU solar-exclusion segment test;
- distance-dependent backbone/direct loss probabilities;
- scheduled B-Neptune and B-Ceres maintenance;
- fixed 19-candidate-link backbone and max-3-link route search.

## S1 / scenarios

`main.py` writes scenario traces for:

- cross-settlement equity value movement;
- 240-hour rising-price futures;
- 240-hour falling-price futures;
- a binding margin-size constraint.

The futures use the same predeclared symmetric margin rule in both price directions. The default quantity is chosen so the ±20% test path makes a real margin constraint bind while settlement remains fully funded from already encumbered resources.

## S2 worst-case incident search

`S2StressSearcher` evaluates all three permitted incident families on the price-dependent futures scenario:

- 72-hour **gateway isolation** at each settlement;
- 6-hour **forced loss** at each settlement and both relays;
- **endpoint reset** at modeled exchange/core endpoints.

The standard evidence run first searches start times every 6 hours from hour 24 through hour 240 across all legal incident families/nodes, then locally refines the worst family/node at 1-hour and 10-minute resolution. This remains numerical evidence rather than an analytical proof over every possible real-valued start time. The current exported search contains **951 evaluated/refined rows**. It exports:

- `output/evidence/S2_incident_search.csv`
- `output/evidence/S2_worst_incident.json`
- `output/evidence/S2_worst_trace/`

Current highest-scoring candidates are tied across several late gateway-isolation starts; the selected reproducible representative is a **72-hour Mars gateway isolation beginning at hour 228**. The CSV preserves the tied alternatives and refinement stage. The trace contains the handshake retries, `R_e` deadlines, hop retries/receipts, incident-forced losses, queue information, quota originations, and eventual financial recovery.

## S3 access tables

S3 tables are generated directly from `Router` for every settlement and both supported transaction types at:

- hour 0: `output/evidence/S3_access_hour_0.csv`
- hour 300: `output/evidence/S3_access_hour_300.csv`

Each row includes assigned exchange, authoritative core, route at the specified hour, conditional one-way delay, and the fraction of the following 24 hours in which **that selected route's required links** can launch. The default availability sampling step is 30 minutes.

## E4 long-horizon scan

The implemented E4 evidence scans **200 Julian years**.

Tier-3 output includes:

- all **38 directed launch directions** implied by the 19 candidate two-way backbone links (forward/reverse are scanned separately because receivers move);
- a **20-day link sampling step**;
- an explicit statement that a closure shorter than the 20-day sample interval could be missed;
- one closed/open event boundary refined to **1-second tolerance**;
- a **45-day service-route scan** for all nine settlements;
- sampled availability and min/max route/service delay;
- the poorest sampled service and a difficult epoch.

Current generated difficult epoch: approximately **day 16,830** from the original epoch. The poorest sampled service in the current topology is Saturn assigned through Earth.

Outputs:

- `output/evidence/E4_link_scan_200y.csv`
- `output/evidence/E4_route_service_200y.csv`
- `output/evidence/E4_summary.json`

## E5 shifted epochs

E5 advances **all planets and both relays** from the original epoch while resetting only financial balances.

Generated evidence includes:

- offsets of **1, 10, and 100 Julian years**;
- route delay and following-24h availability for all settlements;
- a value-move scenario at each required offset;
- the difficult epoch selected from E4;
- value move plus **both rising and falling futures directions** at the difficult epoch.

Outputs:

- `output/evidence/E5_shifted_epochs.csv`
- `output/evidence/E5_scenarios.json`

## Run

```bash
python3 -m pip install -r requirements.txt
python3 main.py
```

`main.py` performs the placement search and regenerates the final workload and baseline scenarios. To use the already-selected baseline without rerunning placement:

```bash
python3 main.py --reuse-baseline
```

Generate/re-generate all S2/S3/E4/E5 evidence with:

```bash
python3 run_evidence.py
```

Or run each component independently:

```bash
python3 run_s2.py
python3 run_s3.py
python3 run_e4.py
python3 run_e5.py
```

Run `run_e4.py` before `run_e5.py`, because E5 uses the difficult epoch selected by E4. The full evidence run is computationally heavier because it performs the 200-year orbital/network scan plus the S2 grid search.

## Tests

```bash
python3 -m pytest -q
```

Tests cover epoch positions, moving-receiver propagation, loss models, exact 960-byte application encoding, rolling direct quota/spacing, and Router service paths.

## Important interpretation limits

- A conditional no-random-loss trace is not an unconditional delivery promise.
- The E4 20-day directed-link and route 45-day scans are numerical evidence. The output explicitly states the closure duration that could be missed by the link sampling step; one event boundary is refined to 1 second.
- The S2 incident timing search uses a 6-hour all-family grid followed by 1-hour and 10-minute local refinement around the worst family/node. It is still numerical evidence, not a proof of the continuously worst real-valued start time.
- Random packet-loss Monte Carlo can be enabled for simulation, but probability accounting and deterministic incident/geometry logic remain separate from financial guarantees.
