# Requested Model Upgrades — Implementation Notes

This build incorporates the six requested upgrades.

1. **Actual fixed-width application encoding**
   - `encoding.py` defines a 32-byte application envelope and 96-byte financial records.
   - Every application packet is exactly 1,024 bytes: 64-byte network header + 960-byte payload.
   - Capacity is 9 financial records per application packet.
   - `workload.py` actually constructs and hashes each 960-byte payload; placement and workload packet counts use the same encoder.

2. **Exact rolling quota timestamps**
   - `quota.py` records every originated packet at an exact simulation timestamp.
   - Rolling windows are `(t-24h, t]`, not calendar-day buckets.
   - The exported ledger includes packet time in days/hours/seconds, window start, exact count after that packet, limit, remaining quota, and direct spacing from the previous origination.

3. **Session/timer/queue/next-open-link traces**
   - `transport.py` models SYN/SYN-ACK/final-ACK setup, session reuse/expiry/reset, hop receipts/retries, endpoint `R_e` timers, FIFO directed-link queues, serialization, relay processing, packet lifetime, and waiting for known solar/maintenance closures.
   - Packet traces include endpoint attempt, hop attempt, queue depth, queue wait, known-link wait, nominal arrival and actual outcome.
   - Trace events explicitly show session establishment/reuse/expiry/reset and wait-before-launch events.

4. **E4 + E5**
   - E4 scans 200 Julian years.
   - All 38 directed launch directions corresponding to the 19 two-way candidate links are scanned.
   - Link step: 20 days; route/service step: 45 days; one boundary is refined to 1 second.
   - E5 covers 1, 10, 100 Julian-year offsets plus the difficult E4 epoch and runs value-move + both futures directions at the difficult epoch.

5. **S3 directly from Router**
   - `EvidenceBuilder.access_table()` calls `Router.service_path()` and `Router.service_availability()`.
   - Outputs are generated at hour 0 and hour 300 for all nine settlements and both products.

6. **S2 worst-case incident search**
   - Searches gateway isolation, forced-loss, and endpoint-reset incidents.
   - Coarse search uses 6-hour starts over hours 24–240 across legal nodes.
   - The worst family/node is refined at 1-hour and then 10-minute resolution.
   - The worst trace is re-run and exported with transport, quota, ledger, and financial-state evidence.

## Reproducibility

Run:

```bash
python3 main.py --reuse-baseline
python3 run_e4.py
python3 run_e5.py
python3 run_s3.py
python3 run_s2.py
python3 -m pytest -q
```

Or regenerate all evidence after the baseline exists:

```bash
python3 run_evidence.py
```
