# S1 traces and E3 balances & obligations (every scenario)

**Reproduce:** `python3 -m evidence.appendix traces` → `traces/<scenario>_trace.csv` (S1), `traces/<scenario>_ledger.csv` and `traces/<scenario>_steps.csv` (E3), `traces/traces_summary.csv`.

Built by replaying each scenario's journal event by event: no step is re-simulated or edited. Conditional (no-loss) traces at the epoch with maintenance and geometry applied.

## Field coverage

| Brief field | Column | Where |
|---|---|---|
| S1 time | `time_h` | every trace row |
| S1 actor | `actor` | every row (account, institution, gateway or relay) |
| S1 local knowledge | `local_knowledge` | what that actor has received / is awaiting / its own free balance |
| S1 action | `action` | event + detail + rule note |
| S1 packet or transmission | `packet_or_transmission` | service, link, launch time, packet kind, attempt |
| S1 arrival | `arrival` | arrival time, or LOST with nominal time |
| S1 financial state after | `financial_state_after` | holdings changed by the step + value in transit |
| E3 owner / location / asset | `owner`, `location`, `asset` | ledger rows |
| E3 source | `source` | opening sheet, trade, transfer, contribution, payout… |
| E3 encumbrance | `encumbered_after`, `encumbrances` | purpose + reference + amount |
| E3 liability | `liability`, steps `obligations_open` | margin duty, transfers owed, fund claims |
| E3 conservation / one use | steps `cash_conserved`, `shares_conserved`, `over_encumbered_holdings` | every financial step |

## Every scenario

| Scenario | Trace rows | Ledger rows | Financial steps | Cash conserved | Shares conserved | Over-encumbered | Open at end |
|---|---|---|---|---|---|---|---|
| Local trade | 5 | 18 | 3 | ✓ every step | ✓ every step | 0 | none |
| Cross-planet trade | 134 | 18 | 12 | ✓ every step | ✓ every step | 0 | none |
| Equal-price auction | 168 | 20 | 15 | ✓ every step | ✓ every step | 0 | none |
| Exact tie / pro-rata | 232 | 22 | 19 | ✓ every step | ✓ every step | 0 | none |
| Partial fill | 134 | 18 | 12 | ✓ every step | ✓ every step | 0 | none |
| Late order | 374 | 32 | 37 | ✓ every step | ✓ every step | 0 | none |
| Limit-price protection | 270 | 28 | 25 | ✓ every step | ✓ every step | 0 | none |
| Cancellation before execution | 87 | 16 | 9 | ✓ every step | ✓ every step | 0 | none |
| Cancellation after execution | 151 | 18 | 13 | ✓ every step | ✓ every step | 0 | none |
| Packet retry / duplicate protection | 168 | 18 | 14 | ✓ every step | ✓ every step | 0 | none |
| Uncertain settlement locking | 220 | 18 | 16 | ✓ every step | ✓ every step | 0 | none |
| Settlement finality | 135 | 19 | 13 | ✓ every step | ✓ every step | 0 | none |
| Futures opening | 136 | 26 | 15 | ✓ every step | ✓ every step | 0 | POS-000001: N-Eve long / E-Alice short 10×MOI-F300 @ 100 | Ceres Clea… |
| Margin call | 844 | 45 | 88 | ✓ every step | ✓ every step | 0 | Ceres Clearing guarantee fund $50,000 |
| Funded default | 593 | 31 | 49 | ✓ every step | ✓ every step | 0 | N-Eve owes clearing $7,000.00 (fund draw, POS-000001) | Ceres Clearin… |
| Disconnection | 155 | 24 | 15 | ✓ every step | ✓ every step | 0 | none |
| Clearing-house outage/recovery | 279 | 32 | 18 | ✓ every step | ✓ every step | 0 | POS-000001: N-Eve long / E-Alice short 10×MOI-F300 @ 100 | Ceres Clea… |
| MOI future, rising path (price-rising run) | 894 | 48 | 92 | ✓ every step | ✓ every step | 0 | Ceres Clearing guarantee fund $50,000 |

## Cross-planet trade — value move between settlements

Key steps (30 of 134 rows; full trace `traces/cross-planet_trace.csv`).

| h | Actor | Local knowledge | Action | Transmission | Arrival | Financial state after |
|---|---|---|---|---|---|---|
| 1.000 | E-Alice | E-Alice sees own $70,000 free at Earth | ORDER STAMPED · RESERVED: E-Alice SELL 100 @ 45; 100 shares locked at Earth — E… | — | — | E-Alice 600 sh@Earth (locked 100 sh) | in transit $0 / 0 sh |
| 1.000 | N-Eve | N-Eve sees own $50,000 free at Neptune | ORDER STAMPED · RESERVED: N-Eve BUY 100 @ 50; $5,000.00 locked at Neptune — Nep… | — | — | N-Eve $55,000@Neptune (locked $5,000) | in transit $0 / 0 sh |
| 1.001 | Earth | Earth Exchange last received: order from E-Alice (local); Earth Excha… | PACKET SENT: batch_order: Earth → Relay A, lands h 1.3132 | backbone Earth→Relay A launch h 1.0006 (data, attempt 1) | h 1.3132 | unchanged |
| 1.001 | Neptune | Neptune Exchange last received: order from N-Eve (local); Neptune Exc… | PACKET SENT: batch_order: Neptune → Relay A, lands h 4.8620 | backbone Neptune→Relay A launch h 1.0006 (data, attempt 1) | h 4.8620 | unchanged |
| 1.314 | Relay A | relay: stores and forwards packet bytes only; no financial state | PACKET SENT: batch_order: Relay A → Ceres, lands h 1.5565 | backbone Relay A→Ceres launch h 1.3137 (data, attempt 1) | h 1.5565 | unchanged |
| 1.556 | Ceres Exchange | Ceres Exchange last received: batch_order from Earth Exchange @h1.56 | RECEIVED: batch_order at Ceres Exchange | — | — | unchanged |
| 4.863 | Relay A | relay: stores and forwards packet bytes only; no financial state | PACKET SENT: batch_order: Relay A → Ceres, lands h 5.1053 | backbone Relay A→Ceres launch h 4.8626 (data, attempt 1) | h 5.1053 | unchanged |
| 5.105 | Ceres Exchange | Ceres Exchange last received: batch_order from Earth Exchange @h1.56,… | RECEIVED: batch_order at Ceres Exchange | — | — | unchanged |
| 31.185 | Ceres | Ceres Exchange last received: batch_order from Earth Exchange @h1.56,… | PACKET SENT: batch_result: Ceres → Relay A, lands h 31.4274 | backbone Ceres→Relay A launch h 31.1846 (data, attempt 1) | h 31.4274 | unchanged |
| 31.185 | Ceres | Ceres Exchange last received: batch_order from Earth Exchange @h1.56,… | PACKET SENT: batch_result: Ceres → Relay A, lands h 31.4274 | backbone Ceres→Relay A launch h 31.1846 (data, attempt 1) | h 31.4274 | unchanged |
| 31.428 | Relay A | relay: stores and forwards packet bytes only; no financial state | PACKET SENT: batch_result: Relay A → Earth, lands h 31.7385 | backbone Relay A→Earth launch h 31.4280 (data, attempt 1) | h 31.7385 | unchanged |
| 31.428 | Relay A | relay: stores and forwards packet bytes only; no financial state | PACKET SENT: batch_result: Relay A → Neptune, lands h 35.2906 | backbone Relay A→Neptune launch h 31.4280 (data, attempt 1) | h 35.2906 | unchanged |
| 31.738 | Earth Exchange | Earth Exchange last received: order from E-Alice (local), batch_resul… | RECEIVED: batch_result at Earth Exchange | — | — | unchanged |
| 31.738 | Earth | Earth Exchange last received: order from E-Alice (local), batch_resul… | RESULT APPLIED AT HOME: BOR-000001: 100 shares → N-Eve (transfer XFR-000001) — … | — | — | E-Alice 500 sh@Earth | in transit $0 / 100 sh |
| 31.739 | Earth | Earth Exchange last received: order from E-Alice (local), batch_resul… | PACKET SENT: transfer: Earth → Relay A, lands h 32.0492 | backbone Earth→Relay A launch h 31.7387 (data, attempt 1) | h 32.0492 | unchanged |
| 32.050 | Relay A | relay: stores and forwards packet bytes only; no financial state | PACKET SENT: transfer: Relay A → Neptune, lands h 35.9124 | backbone Relay A→Neptune launch h 32.0498 (data, attempt 1) | h 35.9124 | unchanged |
| 35.291 | Neptune Exchange | Neptune Exchange last received: order from N-Eve (local), batch_resul… | RECEIVED: batch_result at Neptune Exchange | — | — | unchanged |
| 35.291 | Neptune | Neptune Exchange last received: order from N-Eve (local), batch_resul… | RESULT APPLIED AT HOME: BOR-000002: $4,750.00 → E-Alice (transfer XFR-000002); … | — | — | N-Eve $50,250.00@Neptune | in transit $4,750.00 / 100 sh |
| 35.291 | Neptune | Neptune Exchange last received: order from N-Eve (local), batch_resul… | PACKET SENT: transfer: Neptune → Relay A, lands h 39.1538 | backbone Neptune→Relay A launch h 35.2909 (data, attempt 1) | h 39.1538 | unchanged |
| 35.912 | Neptune Exchange | Neptune Exchange last received: batch_result from Ceres Exchange @h35… | RECEIVED: transfer at Neptune Exchange | — | — | unchanged |
| 35.912 | N-Eve | N-Eve sees own $50,250.00 free at Neptune | VALIDATED · FINAL: XFR-000001: usable by N-Eve at Neptune — destination validat… | — | — | N-Eve 600 sh@Neptune | in transit $4,750.00 / 0 sh |
| 35.913 | Neptune | Neptune Exchange last received: batch_result from Ceres Exchange @h35… | PACKET SENT: transfer_status: Neptune → Relay A, lands h 39.7756 | backbone Neptune→Relay A launch h 35.9126 (data, attempt 1) | h 39.7756 | unchanged |
| 39.154 | Relay A | relay: stores and forwards packet bytes only; no financial state | PACKET SENT: transfer: Relay A → Earth, lands h 39.4642 | backbone Relay A→Earth launch h 39.1543 (data, attempt 1) | h 39.4642 | unchanged |
| 39.464 | Earth Exchange | Earth Exchange last received: batch_result from Ceres Exchange @h31.7… | RECEIVED: transfer at Earth Exchange | — | — | unchanged |
| 39.464 | E-Alice | E-Alice sees own $74,750.00 free at Earth | VALIDATED · FINAL: XFR-000002: usable by E-Alice at Earth — destination validat… | — | — | E-Alice $74,750.00@Earth | in transit $0 / 0 sh |
| 39.465 | Earth | Earth Exchange last received: batch_result from Ceres Exchange @h31.7… | PACKET SENT: transfer_status: Earth → Relay A, lands h 39.7745 | backbone Earth→Relay A launch h 39.4645 (data, attempt 1) | h 39.7745 | unchanged |
| 39.775 | Relay A | relay: stores and forwards packet bytes only; no financial state | PACKET SENT: transfer_status: Relay A → Neptune, lands h 43.6379 | backbone Relay A→Neptune launch h 39.7750 (data, attempt 1) | h 43.6379 | unchanged |
| 39.776 | Relay A | relay: stores and forwards packet bytes only; no financial state | PACKET SENT: transfer_status: Relay A → Earth, lands h 40.0860 | backbone Relay A→Earth launch h 39.7761 (data, attempt 1) | h 40.0860 | unchanged |
| 40.086 | Earth Exchange | Earth Exchange last received: transfer from Neptune Exchange @h39.46,… | RECEIVED: transfer_status at Earth Exchange | — | — | unchanged |
| 43.638 | Neptune Exchange | Neptune Exchange last received: transfer from Earth Exchange @h35.91,… | RECEIVED: transfer_status at Neptune Exchange | — | — | unchanged |

Balances and obligations (E3), 6 changes after the opening rows; full ledger `traces/cross-planet_ledger.csv`.

| h | Owner | Asset @ location | Change | Balance | Encumbered | Source | Liability |
|---|---|---|---|---|---|---|---|
| 1.000 | E-Alice | shares @ Earth | lock change | 600 sh | 100 sh | batch order lock BOR-000001 | committed to order BOR-000001 |
| 1.000 | N-Eve | cash @ Neptune | lock change | $55,000 | $5,000 | batch order lock BOR-000002 | committed to order BOR-000002 |
| 31.738 | E-Alice | shares @ Earth | -100 sh | 500 sh | 0 sh | batch result for BOR-000001: shares XFR-000001 | owes 100 sh to N-Eve@Neptune via XFR-000001 (in transit) |
| 35.291 | N-Eve | cash @ Neptune | $-4,750.00 | $50,250.00 | $0.00 | batch result for BOR-000002: cash XFR-000002 | owes $4,750.00 to E-Alice@Earth via XFR-000002 (in transit) |
| 35.912 | N-Eve | shares @ Neptune | 100 sh | 600 sh | 0 sh | transfer XFR-000001 validated at destination |  |
| 39.464 | E-Alice | cash @ Earth | $4,750.00 | $74,750.00 | $0 | transfer XFR-000002 validated at destination |  |

## Margin call — obligation open ≥ 240 h, price-falling run

Key steps (35 of 844 rows; full trace `traces/margin-call_trace.csv`).

| h | Actor | Local knowledge | Action | Transmission | Arrival | Financial state after |
|---|---|---|---|---|---|---|
| 0.100 | E-Alice | E-Alice sees own $45,000 free at Earth | TRANSFER INITIATED: XFR-000001: $25,000.00 Earth → Ceres (in transit) — Earth E… | — | — | E-Alice $45,000@Earth | in transit $25,000 / 0 sh |
| 0.100 | E-Bob | E-Bob sees own $20,000 free at Earth | TRANSFER INITIATED: XFR-000002: $25,000.00 Earth → Ceres (in transit) — Earth E… | — | — | E-Bob $20,000@Earth | in transit $50,000 / 0 sh |
| 0.500 | N-Eve | N-Eve sees own $40,000.00 free at Neptune | TRANSFER INITIATED: XFR-000003: $15,000.00 Neptune → Ceres (in transit) — Neptu… | — | — | N-Eve $40,000.00@Neptune | in transit $65,000.00 / 0 sh |
| 0.500 | E-Alice | E-Alice sees own $35,000.00 free at Earth | TRANSFER INITIATED: XFR-000004: $10,000.00 Earth → Ceres (in transit) — Earth E… | — | — | E-Alice $35,000.00@Earth | in transit $75,000.00 / 0 sh |
| 0.656 | E-Alice | E-Alice sees own $35,000.00 free at Earth | VALIDATED · FINAL: XFR-000001: usable by E-Alice at Ceres — cash validated at C… | — | — | E-Alice $25,000@Ceres | in transit $50,000.00 / 0 sh |
| 0.656 | E-Alice | E-Alice sees own $35,000.00 free at Earth | GUARANTEE CONTRIBUTION: E-Alice → Ceres Clearing: $25,000.00 — pre-funded guara… | — | — | Ceres Clearing $25,000@Ceres (locked $25,000); E-Alice $0@Ceres | in transit $50,000.00 / 0 sh |
| 0.656 | E-Bob | E-Bob sees own $20,000 free at Earth | VALIDATED · FINAL: XFR-000002: usable by E-Bob at Ceres — cash validated at Cer… | — | — | E-Bob $25,000@Ceres | in transit $25,000.00 / 0 sh |
| 0.656 | E-Bob | E-Bob sees own $20,000 free at Earth | GUARANTEE CONTRIBUTION: E-Bob → Ceres Clearing: $25,000.00 — pre-funded guarant… | — | — | Ceres Clearing $50,000@Ceres (locked $50,000); E-Bob $0@Ceres | in transit $25,000.00 / 0 sh |
| 1.000 |  |  | NOTE: REJECTED: open the future before N-Eve's margin reaches Earth (N-Eve lack… | — | — | unchanged |
| 1.056 | E-Alice | E-Alice sees own $35,000.00 free at Earth | VALIDATED · FINAL: XFR-000004: usable by E-Alice at Ceres — cash validated at C… | — | — | E-Alice $10,000.00@Ceres | in transit $15,000.00 / 0 sh |
| 4.605 | N-Eve | N-Eve sees own $40,000.00 free at Neptune | VALIDATED · FINAL: XFR-000003: usable by N-Eve at Ceres — cash validated at Cer… | — | — | N-Eve $15,000.00@Ceres | in transit $0 / 0 sh |
| 4.605 | Ceres | Ceres Clearing last received: margin_transfer from Earth Exchange @h1… | FUTURE OPENED: MOI-F300 10 @ 100: long N-Eve (margin 15000.00), short E-Alice (… | — | — | E-Alice $10,000.00@Ceres (locked $10,000.00); N-Eve $15,000.00@Ceres (locked $15,000.00) | in transit $0 / 0 … |
| 36.497 | Ceres Clearing | Ceres Clearing last received: mark from Mars Exchange @h24.50, mark f… | NOTE: MARGIN CALL N-Eve 5100.00 (deadline h 53.43) | — | — | unchanged |
| 40.604 | N-Eve | N-Eve sees own $34,900.00 free at Neptune | TRANSFER INITIATED: XFR-000005: $5,100.00 Neptune → Ceres (in transit) — Neptun… | — | — | N-Eve $34,900.00@Neptune | in transit $5,100.00 / 0 sh |
| 44.711 | N-Eve | N-Eve sees own $34,900.00 free at Neptune | VALIDATED · FINAL: XFR-000005: usable by N-Eve at Ceres — cash validated at Cer… | — | — | N-Eve $20,100.00@Ceres (locked $15,000.00) | in transit $0 / 0 sh |
| 44.711 | N-Eve | N-Eve sees own $34,900.00 free at Neptune | MARGIN POSTED: long +5100.00 — top-up validated at Ceres Clearing | — | — | N-Eve $20,100.00@Ceres (locked $20,100.00) | in transit $0 / 0 sh |
| 60.498 | Ceres Clearing | Ceres Clearing last received: mark from Mars Exchange @h48.50, mark f… | NOTE: MARGIN CALL N-Eve 3400.00 (deadline h 77.43) | — | — | unchanged |
| 64.606 | N-Eve | N-Eve sees own $31,500.00 free at Neptune | TRANSFER INITIATED: XFR-000006: $3,400.00 Neptune → Ceres (in transit) — Neptun… | — | — | N-Eve $31,500.00@Neptune | in transit $3,400.00 / 0 sh |
| 68.714 | N-Eve | N-Eve sees own $31,500.00 free at Neptune | VALIDATED · FINAL: XFR-000006: usable by N-Eve at Ceres — cash validated at Cer… | — | — | N-Eve $23,500.00@Ceres (locked $20,100.00) | in transit $0 / 0 sh |
| 68.714 | N-Eve | N-Eve sees own $31,500.00 free at Neptune | MARGIN POSTED: long +3400.00 — top-up validated at Ceres Clearing | — | — | N-Eve $23,500.00@Ceres (locked $23,500.00) | in transit $0 / 0 sh |
| 96.500 | Ceres Clearing | Ceres Clearing last received: mark from Mars Exchange @h84.50, mark f… | NOTE: MARGIN CALL N-Eve 3400.00 (deadline h 113.43) | — | — | unchanged |
| 100.609 | N-Eve | N-Eve sees own $28,100.00 free at Neptune | TRANSFER INITIATED: XFR-000007: $3,400.00 Neptune → Ceres (in transit) — Neptun… | — | — | N-Eve $28,100.00@Neptune | in transit $3,400.00 / 0 sh |
| 104.719 | N-Eve | N-Eve sees own $28,100.00 free at Neptune | VALIDATED · FINAL: XFR-000007: usable by N-Eve at Ceres — cash validated at Cer… | — | — | N-Eve $26,900.00@Ceres (locked $23,500.00) | in transit $0 / 0 sh |
| 104.719 | N-Eve | N-Eve sees own $28,100.00 free at Neptune | MARGIN POSTED: long +3400.00 — top-up validated at Ceres Clearing | — | — | N-Eve $26,900.00@Ceres (locked $26,900.00) | in transit $0 / 0 sh |
| 144.502 | Ceres Clearing | Ceres Clearing last received: mark from Mars Exchange @h132.50, mark … | NOTE: MARGIN CALL N-Eve 3400.00 (deadline h 161.44) | — | — | unchanged |
| 148.613 | N-Eve | N-Eve sees own $24,700.00 free at Neptune | TRANSFER INITIATED: XFR-000008: $3,400.00 Neptune → Ceres (in transit) — Neptun… | — | — | N-Eve $24,700.00@Neptune | in transit $3,400.00 / 0 sh |
| 152.725 | N-Eve | N-Eve sees own $24,700.00 free at Neptune | VALIDATED · FINAL: XFR-000008: usable by N-Eve at Ceres — cash validated at Cer… | — | — | N-Eve $30,300.00@Ceres (locked $26,900.00) | in transit $0 / 0 sh |
| 152.725 | N-Eve | N-Eve sees own $24,700.00 free at Neptune | MARGIN POSTED: long +3400.00 — top-up validated at Ceres Clearing | — | — | N-Eve $30,300.00@Ceres (locked $30,300.00) | in transit $0 / 0 sh |
| 216.505 | Ceres Clearing | Ceres Clearing last received: mark from Mars Exchange @h204.50, mark … | NOTE: MARGIN CALL N-Eve 3400.00 (deadline h 233.44) | — | — | unchanged |
| 220.620 | N-Eve | N-Eve sees own $21,300.00 free at Neptune | TRANSFER INITIATED: XFR-000009: $3,400.00 Neptune → Ceres (in transit) — Neptun… | — | — | N-Eve $21,300.00@Neptune | in transit $3,400.00 / 0 sh |
| 224.735 | N-Eve | N-Eve sees own $21,300.00 free at Neptune | VALIDATED · FINAL: XFR-000009: usable by N-Eve at Ceres — cash validated at Cer… | — | — | N-Eve $33,700.00@Ceres (locked $30,300.00) | in transit $0 / 0 sh |
| 224.735 | N-Eve | N-Eve sees own $21,300.00 free at Neptune | MARGIN POSTED: long +3400.00 — top-up validated at Ceres Clearing | — | — | N-Eve $33,700.00@Ceres (locked $33,700.00) | in transit $0 / 0 sh |
| 300.509 | Ceres | Ceres Clearing last received: mark from Mars Exchange @h288.51, mark … | SETTLED AT MATURITY: POS-000001 — maturity: first scheduled MOI at or after h 3… | — | — | E-Alice $30,000.00@Ceres; N-Eve $13,700.00@Ceres | in transit $0 / 0 sh |
| 300.509 | E-Alice | E-Alice sees own $35,000.00 free at Earth | TRANSFER INITIATED: XFR-000010: $20,000.00 Ceres → Earth (in transit) — payout … | — | — | E-Alice $10,000.00@Ceres | in transit $20,000.00 / 0 sh |
| 301.046 | E-Alice | E-Alice sees own $55,000.00 free at Earth | VALIDATED · FINAL: XFR-000010: usable by E-Alice at Earth — destination validat… | — | — | E-Alice $55,000.00@Earth | in transit $0 / 0 sh |

Balances and obligations (E3), 33 changes after the opening rows; full ledger `traces/margin-call_ledger.csv`.

| h | Owner | Asset @ location | Change | Balance | Encumbered | Source | Liability |
|---|---|---|---|---|---|---|---|
| 0.100 | E-Alice | cash @ Earth | $-25,000 | $45,000 | $0 | transfer XFR-000001 sent (Earth → Ceres) | owes $25,000 to E-Alice@Ceres via XFR-000001 (in transit) |
| 0.100 | E-Bob | cash @ Earth | $-25,000 | $20,000 | $0 | transfer XFR-000002 sent (Earth → Ceres) | owes $25,000 to E-Bob@Ceres via XFR-000002 (in transit) |
| 0.500 | N-Eve | cash @ Neptune | $-15,000.00 | $40,000.00 | $0 | transfer XFR-000003 sent (Neptune → Ceres) | owes $15,000.00 to N-Eve@Ceres via XFR-000003 (in transit) |
| 0.500 | E-Alice | cash @ Earth | $-10,000.00 | $35,000.00 | $0 | transfer XFR-000004 sent (Earth → Ceres) | owes $25,000 to E-Alice@Ceres via XFR-000001 (in transit); owes $10,0… |
| 0.656 | E-Alice | cash @ Ceres | $25,000 | $25,000 | $0 | transfer XFR-000001 validated at destination |  |
| 0.656 | Ceres Clearing | cash @ Ceres | $25,000 | $25,000 | $25,000 | guarantee contribution E-Alice → Ceres Clearing | guarantee fund: covers default losses; repayable to contributors |
| 0.656 | E-Alice | cash @ Ceres | $-25,000 | $0 | $0 | guarantee contribution E-Alice → Ceres Clearing |  |
| 0.656 | E-Bob | cash @ Ceres | $25,000 | $25,000 | $0 | transfer XFR-000002 validated at destination |  |
| 0.656 | Ceres Clearing | cash @ Ceres | $25,000 | $50,000 | $50,000 | guarantee contribution E-Bob → Ceres Clearing | guarantee fund: covers default losses; repayable to contributors; gua… |
| 0.656 | E-Bob | cash @ Ceres | $-25,000 | $0 | $0 | guarantee contribution E-Bob → Ceres Clearing |  |
| 1.056 | E-Alice | cash @ Ceres | $10,000.00 | $10,000.00 | $0 | transfer XFR-000004 validated at destination |  |
| 4.605 | N-Eve | cash @ Ceres | $15,000.00 | $15,000.00 | $0 | transfer XFR-000003 validated at destination |  |
| 4.605 | E-Alice | cash @ Ceres | lock change | $10,000.00 | $10,000.00 | position POS-000001 opened (margin locked) | short margin on POS-000001 (pays losses) |
| 4.605 | N-Eve | cash @ Ceres | lock change | $15,000.00 | $15,000.00 | position POS-000001 opened (margin locked) | long margin on POS-000001 (pays losses) |
| 40.604 | N-Eve | cash @ Neptune | $-5,100.00 | $34,900.00 | $0 | transfer XFR-000005 sent (Neptune → Ceres) | owes $5,100.00 to N-Eve@Ceres via XFR-000005 (in transit) |
| 44.711 | N-Eve | cash @ Ceres | $5,100.00 | $20,100.00 | $15,000.00 | transfer XFR-000005 validated at destination | long margin on POS-000001 (pays losses) |
| 44.711 | N-Eve | cash @ Ceres | lock change | $20,100.00 | $20,100.00 | margin top-up on POS-000001 | long margin on POS-000001 (pays losses); long margin on POS-000001 (p… |
| 64.606 | N-Eve | cash @ Neptune | $-3,400.00 | $31,500.00 | $0 | transfer XFR-000006 sent (Neptune → Ceres) | owes $3,400.00 to N-Eve@Ceres via XFR-000006 (in transit) |
| 68.714 | N-Eve | cash @ Ceres | $3,400.00 | $23,500.00 | $20,100.00 | transfer XFR-000006 validated at destination | long margin on POS-000001 (pays losses); long margin on POS-000001 (p… |
| 68.714 | N-Eve | cash @ Ceres | lock change | $23,500.00 | $23,500.00 | margin top-up on POS-000001 | long margin on POS-000001 (pays losses); long margin on POS-000001 (p… |
| 100.609 | N-Eve | cash @ Neptune | $-3,400.00 | $28,100.00 | $0 | transfer XFR-000007 sent (Neptune → Ceres) | owes $3,400.00 to N-Eve@Ceres via XFR-000007 (in transit) |
| 104.719 | N-Eve | cash @ Ceres | $3,400.00 | $26,900.00 | $23,500.00 | transfer XFR-000007 validated at destination | long margin on POS-000001 (pays losses); long margin on POS-000001 (p… |
| 104.719 | N-Eve | cash @ Ceres | lock change | $26,900.00 | $26,900.00 | margin top-up on POS-000001 | long margin on POS-000001 (pays losses); long margin on POS-000001 (p… |
| 148.613 | N-Eve | cash @ Neptune | $-3,400.00 | $24,700.00 | $0 | transfer XFR-000008 sent (Neptune → Ceres) | owes $3,400.00 to N-Eve@Ceres via XFR-000008 (in transit) |
| 152.725 | N-Eve | cash @ Ceres | $3,400.00 | $30,300.00 | $26,900.00 | transfer XFR-000008 validated at destination | long margin on POS-000001 (pays losses); long margin on POS-000001 (p… |
| 152.725 | N-Eve | cash @ Ceres | lock change | $30,300.00 | $30,300.00 | margin top-up on POS-000001 | long margin on POS-000001 (pays losses); long margin on POS-000001 (p… |
| 220.620 | N-Eve | cash @ Neptune | $-3,400.00 | $21,300.00 | $0 | transfer XFR-000009 sent (Neptune → Ceres) | owes $3,400.00 to N-Eve@Ceres via XFR-000009 (in transit) |
| 224.735 | N-Eve | cash @ Ceres | $3,400.00 | $33,700.00 | $30,300.00 | transfer XFR-000009 validated at destination | long margin on POS-000001 (pays losses); long margin on POS-000001 (p… |
| 224.735 | N-Eve | cash @ Ceres | lock change | $33,700.00 | $33,700.00 | margin top-up on POS-000001 | long margin on POS-000001 (pays losses); long margin on POS-000001 (p… |
| 300.509 | E-Alice | cash @ Ceres | $20,000.00 | $30,000.00 | $0.00 | position POS-000001 settled at maturity |  |
| 300.509 | N-Eve | cash @ Ceres | $-20,000.00 | $13,700.00 | $0.00 | position POS-000001 settled at maturity |  |
| 300.509 | E-Alice | cash @ Ceres | $-20,000.00 | $10,000.00 | $0.00 | transfer XFR-000010 sent (Ceres → Earth) | owes $20,000.00 to E-Alice@Earth via XFR-000010 (in transit) |
| 301.046 | E-Alice | cash @ Earth | $20,000.00 | $55,000.00 | $0 | transfer XFR-000010 validated at destination |  |

## MOI future, rising path (price-rising run) — price-rising run

Key steps (39 of 894 rows; full trace `traces/futures-rising_trace.csv`).

| h | Actor | Local knowledge | Action | Transmission | Arrival | Financial state after |
|---|---|---|---|---|---|---|
| 0.100 | E-Alice | E-Alice sees own $45,000 free at Earth | TRANSFER INITIATED: XFR-000001: $25,000.00 Earth → Ceres (in transit) — Earth E… | — | — | E-Alice $45,000@Earth | in transit $25,000 / 0 sh |
| 0.100 | E-Bob | E-Bob sees own $20,000 free at Earth | TRANSFER INITIATED: XFR-000002: $25,000.00 Earth → Ceres (in transit) — Earth E… | — | — | E-Bob $20,000@Earth | in transit $50,000 / 0 sh |
| 0.500 | N-Eve | N-Eve sees own $40,000.00 free at Neptune | TRANSFER INITIATED: XFR-000003: $15,000.00 Neptune → Ceres (in transit) — Neptu… | — | — | N-Eve $40,000.00@Neptune | in transit $65,000.00 / 0 sh |
| 0.500 | E-Alice | E-Alice sees own $35,000.00 free at Earth | TRANSFER INITIATED: XFR-000004: $10,000.00 Earth → Ceres (in transit) — Earth E… | — | — | E-Alice $35,000.00@Earth | in transit $75,000.00 / 0 sh |
| 0.656 | E-Alice | E-Alice sees own $35,000.00 free at Earth | VALIDATED · FINAL: XFR-000001: usable by E-Alice at Ceres — cash validated at C… | — | — | E-Alice $25,000@Ceres | in transit $50,000.00 / 0 sh |
| 0.656 | E-Alice | E-Alice sees own $35,000.00 free at Earth | GUARANTEE CONTRIBUTION: E-Alice → Ceres Clearing: $25,000.00 — pre-funded guara… | — | — | Ceres Clearing $25,000@Ceres (locked $25,000); E-Alice $0@Ceres | in transit $50,000.00 / 0 sh |
| 0.656 | E-Bob | E-Bob sees own $20,000 free at Earth | VALIDATED · FINAL: XFR-000002: usable by E-Bob at Ceres — cash validated at Cer… | — | — | E-Bob $25,000@Ceres | in transit $25,000.00 / 0 sh |
| 0.656 | E-Bob | E-Bob sees own $20,000 free at Earth | GUARANTEE CONTRIBUTION: E-Bob → Ceres Clearing: $25,000.00 — pre-funded guarant… | — | — | Ceres Clearing $50,000@Ceres (locked $50,000); E-Bob $0@Ceres | in transit $25,000.00 / 0 sh |
| 1.000 |  |  | NOTE: REJECTED: open the future before N-Eve's margin reaches Earth (N-Eve lack… | — | — | unchanged |
| 1.056 | E-Alice | E-Alice sees own $35,000.00 free at Earth | VALIDATED · FINAL: XFR-000004: usable by E-Alice at Ceres — cash validated at C… | — | — | E-Alice $10,000.00@Ceres | in transit $15,000.00 / 0 sh |
| 4.605 | N-Eve | N-Eve sees own $40,000.00 free at Neptune | VALIDATED · FINAL: XFR-000003: usable by N-Eve at Ceres — cash validated at Cer… | — | — | N-Eve $15,000.00@Ceres | in transit $0 / 0 sh |
| 4.605 | Ceres | Ceres Clearing last received: margin_transfer from Earth Exchange @h1… | FUTURE OPENED: MOI-F300 10 @ 100: long N-Eve (margin 15000.00), short E-Alice (… | — | — | E-Alice $10,000.00@Ceres (locked $10,000.00); N-Eve $15,000.00@Ceres (locked $15,000.00) | in transit $0 / 0 … |
| 24.497 | Ceres Clearing | Ceres Clearing last received: mark from Mars Exchange @h12.50, mark f… | NOTE: MARGIN CALL E-Alice 3300.00 (deadline h 27.23) | — | — | unchanged |
| 25.051 | E-Alice | E-Alice sees own $31,700.00 free at Earth | TRANSFER INITIATED: XFR-000005: $3,300.00 Earth → Ceres (in transit) — Earth Ex… | — | — | E-Alice $31,700.00@Earth | in transit $3,300.00 / 0 sh |
| 25.606 | E-Alice | E-Alice sees own $31,700.00 free at Earth | VALIDATED · FINAL: XFR-000005: usable by E-Alice at Ceres — cash validated at C… | — | — | E-Alice $13,300.00@Ceres (locked $10,000.00) | in transit $0 / 0 sh |
| 25.606 | E-Alice | E-Alice sees own $31,700.00 free at Earth | MARGIN POSTED: short +3300.00 — top-up validated at Ceres Clearing | — | — | E-Alice $13,300.00@Ceres (locked $13,300.00) | in transit $0 / 0 sh |
| 36.497 | Ceres Clearing | Ceres Clearing last received: margin_transfer from Earth Exchange @h2… | NOTE: MARGIN CALL E-Alice 3300.00 (deadline h 39.24) | — | — | unchanged |
| 37.051 | E-Alice | E-Alice sees own $28,400.00 free at Earth | TRANSFER INITIATED: XFR-000006: $3,300.00 Earth → Ceres (in transit) — Earth Ex… | — | — | E-Alice $28,400.00@Earth | in transit $3,300.00 / 0 sh |
| 37.605 | E-Alice | E-Alice sees own $28,400.00 free at Earth | VALIDATED · FINAL: XFR-000006: usable by E-Alice at Ceres — cash validated at C… | — | — | E-Alice $16,600.00@Ceres (locked $13,300.00) | in transit $0 / 0 sh |
| 37.605 | E-Alice | E-Alice sees own $28,400.00 free at Earth | MARGIN POSTED: short +3300.00 — top-up validated at Ceres Clearing | — | — | E-Alice $16,600.00@Ceres (locked $16,600.00) | in transit $0 / 0 sh |
| 60.498 | Ceres Clearing | Ceres Clearing last received: mark from Mars Exchange @h48.50, mark f… | NOTE: MARGIN CALL E-Alice 4400.00 (deadline h 63.24) | — | — | unchanged |
| 61.051 | E-Alice | E-Alice sees own $24,000.00 free at Earth | TRANSFER INITIATED: XFR-000007: $4,400.00 Earth → Ceres (in transit) — Earth Ex… | — | — | E-Alice $24,000.00@Earth | in transit $4,400.00 / 0 sh |
| 61.603 | E-Alice | E-Alice sees own $24,000.00 free at Earth | VALIDATED · FINAL: XFR-000007: usable by E-Alice at Ceres — cash validated at C… | — | — | E-Alice $21,000.00@Ceres (locked $16,600.00) | in transit $0 / 0 sh |
| 61.603 | E-Alice | E-Alice sees own $24,000.00 free at Earth | MARGIN POSTED: short +4400.00 — top-up validated at Ceres Clearing | — | — | E-Alice $21,000.00@Ceres (locked $21,000.00) | in transit $0 / 0 sh |
| 96.500 | Ceres Clearing | Ceres Clearing last received: mark from Mars Exchange @h84.50, mark f… | NOTE: MARGIN CALL E-Alice 4400.00 (deadline h 99.24) | — | — | unchanged |
| 97.050 | E-Alice | E-Alice sees own $19,600.00 free at Earth | TRANSFER INITIATED: XFR-000008: $4,400.00 Earth → Ceres (in transit) — Earth Ex… | — | — | E-Alice $19,600.00@Earth | in transit $4,400.00 / 0 sh |
| 97.600 | E-Alice | E-Alice sees own $19,600.00 free at Earth | VALIDATED · FINAL: XFR-000008: usable by E-Alice at Ceres — cash validated at C… | — | — | E-Alice $25,400.00@Ceres (locked $21,000.00) | in transit $0 / 0 sh |
| 97.600 | E-Alice | E-Alice sees own $19,600.00 free at Earth | MARGIN POSTED: short +4400.00 — top-up validated at Ceres Clearing | — | — | E-Alice $25,400.00@Ceres (locked $25,400.00) | in transit $0 / 0 sh |
| 132.501 | Ceres Clearing | Ceres Clearing last received: mark from Mars Exchange @h120.50, mark … | NOTE: MARGIN CALL E-Alice 3300.00 (deadline h 135.24) | — | — | unchanged |
| 133.049 | E-Alice | E-Alice sees own $16,300.00 free at Earth | TRANSFER INITIATED: XFR-000009: $3,300.00 Earth → Ceres (in transit) — Earth Ex… | — | — | E-Alice $16,300.00@Earth | in transit $3,300.00 / 0 sh |
| 133.597 | E-Alice | E-Alice sees own $16,300.00 free at Earth | VALIDATED · FINAL: XFR-000009: usable by E-Alice at Ceres — cash validated at C… | — | — | E-Alice $28,700.00@Ceres (locked $25,400.00) | in transit $0 / 0 sh |
| 133.597 | E-Alice | E-Alice sees own $16,300.00 free at Earth | MARGIN POSTED: short +3300.00 — top-up validated at Ceres Clearing | — | — | E-Alice $28,700.00@Ceres (locked $28,700.00) | in transit $0 / 0 sh |
| 168.503 | Ceres Clearing | Ceres Clearing last received: mark from Mars Exchange @h156.50, mark … | NOTE: MARGIN CALL E-Alice 3300.00 (deadline h 171.24) | — | — | unchanged |
| 169.048 | E-Alice | E-Alice sees own $13,000.00 free at Earth | TRANSFER INITIATED: XFR-000010: $3,300.00 Earth → Ceres (in transit) — Earth Ex… | — | — | E-Alice $13,000.00@Earth | in transit $3,300.00 / 0 sh |
| 169.594 | E-Alice | E-Alice sees own $13,000.00 free at Earth | VALIDATED · FINAL: XFR-000010: usable by E-Alice at Ceres — cash validated at C… | — | — | E-Alice $32,000.00@Ceres (locked $28,700.00) | in transit $0 / 0 sh |
| 169.594 | E-Alice | E-Alice sees own $13,000.00 free at Earth | MARGIN POSTED: short +3300.00 — top-up validated at Ceres Clearing | — | — | E-Alice $32,000.00@Ceres (locked $32,000.00) | in transit $0 / 0 sh |
| 300.509 | Ceres | Ceres Clearing last received: mark from Mars Exchange @h288.51, mark … | SETTLED AT MATURITY: POS-000001 — maturity: first scheduled MOI at or after h 3… | — | — | E-Alice $12,000.00@Ceres; N-Eve $35,000.00@Ceres | in transit $0 / 0 sh |
| 300.509 | N-Eve | N-Eve sees own $40,000.00 free at Neptune | TRANSFER INITIATED: XFR-000011: $20,000.00 Ceres → Neptune (in transit) — payou… | — | — | N-Eve $15,000.00@Ceres | in transit $20,000.00 / 0 sh |
| 304.627 | N-Eve | N-Eve sees own $60,000.00 free at Neptune | VALIDATED · FINAL: XFR-000011: usable by N-Eve at Neptune — destination validat… | — | — | N-Eve $60,000.00@Neptune | in transit $0 / 0 sh |

Balances and obligations (E3), 36 changes after the opening rows; full ledger `traces/futures-rising_ledger.csv`.

| h | Owner | Asset @ location | Change | Balance | Encumbered | Source | Liability |
|---|---|---|---|---|---|---|---|
| 0.100 | E-Alice | cash @ Earth | $-25,000 | $45,000 | $0 | transfer XFR-000001 sent (Earth → Ceres) | owes $25,000 to E-Alice@Ceres via XFR-000001 (in transit) |
| 0.100 | E-Bob | cash @ Earth | $-25,000 | $20,000 | $0 | transfer XFR-000002 sent (Earth → Ceres) | owes $25,000 to E-Bob@Ceres via XFR-000002 (in transit) |
| 0.500 | N-Eve | cash @ Neptune | $-15,000.00 | $40,000.00 | $0 | transfer XFR-000003 sent (Neptune → Ceres) | owes $15,000.00 to N-Eve@Ceres via XFR-000003 (in transit) |
| 0.500 | E-Alice | cash @ Earth | $-10,000.00 | $35,000.00 | $0 | transfer XFR-000004 sent (Earth → Ceres) | owes $25,000 to E-Alice@Ceres via XFR-000001 (in transit); owes $10,0… |
| 0.656 | E-Alice | cash @ Ceres | $25,000 | $25,000 | $0 | transfer XFR-000001 validated at destination |  |
| 0.656 | Ceres Clearing | cash @ Ceres | $25,000 | $25,000 | $25,000 | guarantee contribution E-Alice → Ceres Clearing | guarantee fund: covers default losses; repayable to contributors |
| 0.656 | E-Alice | cash @ Ceres | $-25,000 | $0 | $0 | guarantee contribution E-Alice → Ceres Clearing |  |
| 0.656 | E-Bob | cash @ Ceres | $25,000 | $25,000 | $0 | transfer XFR-000002 validated at destination |  |
| 0.656 | Ceres Clearing | cash @ Ceres | $25,000 | $50,000 | $50,000 | guarantee contribution E-Bob → Ceres Clearing | guarantee fund: covers default losses; repayable to contributors; gua… |
| 0.656 | E-Bob | cash @ Ceres | $-25,000 | $0 | $0 | guarantee contribution E-Bob → Ceres Clearing |  |
| 1.056 | E-Alice | cash @ Ceres | $10,000.00 | $10,000.00 | $0 | transfer XFR-000004 validated at destination |  |
| 4.605 | N-Eve | cash @ Ceres | $15,000.00 | $15,000.00 | $0 | transfer XFR-000003 validated at destination |  |
| 4.605 | E-Alice | cash @ Ceres | lock change | $10,000.00 | $10,000.00 | position POS-000001 opened (margin locked) | short margin on POS-000001 (pays losses) |
| 4.605 | N-Eve | cash @ Ceres | lock change | $15,000.00 | $15,000.00 | position POS-000001 opened (margin locked) | long margin on POS-000001 (pays losses) |
| 25.051 | E-Alice | cash @ Earth | $-3,300.00 | $31,700.00 | $0 | transfer XFR-000005 sent (Earth → Ceres) | owes $3,300.00 to E-Alice@Ceres via XFR-000005 (in transit) |
| 25.606 | E-Alice | cash @ Ceres | $3,300.00 | $13,300.00 | $10,000.00 | transfer XFR-000005 validated at destination | short margin on POS-000001 (pays losses) |
| 25.606 | E-Alice | cash @ Ceres | lock change | $13,300.00 | $13,300.00 | margin top-up on POS-000001 | short margin on POS-000001 (pays losses); short margin on POS-000001 … |
| 37.051 | E-Alice | cash @ Earth | $-3,300.00 | $28,400.00 | $0 | transfer XFR-000006 sent (Earth → Ceres) | owes $3,300.00 to E-Alice@Ceres via XFR-000006 (in transit) |
| 37.605 | E-Alice | cash @ Ceres | $3,300.00 | $16,600.00 | $13,300.00 | transfer XFR-000006 validated at destination | short margin on POS-000001 (pays losses); short margin on POS-000001 … |
| 37.605 | E-Alice | cash @ Ceres | lock change | $16,600.00 | $16,600.00 | margin top-up on POS-000001 | short margin on POS-000001 (pays losses); short margin on POS-000001 … |
| 61.051 | E-Alice | cash @ Earth | $-4,400.00 | $24,000.00 | $0 | transfer XFR-000007 sent (Earth → Ceres) | owes $4,400.00 to E-Alice@Ceres via XFR-000007 (in transit) |
| 61.603 | E-Alice | cash @ Ceres | $4,400.00 | $21,000.00 | $16,600.00 | transfer XFR-000007 validated at destination | short margin on POS-000001 (pays losses); short margin on POS-000001 … |
| 61.603 | E-Alice | cash @ Ceres | lock change | $21,000.00 | $21,000.00 | margin top-up on POS-000001 | short margin on POS-000001 (pays losses); short margin on POS-000001 … |
| 97.050 | E-Alice | cash @ Earth | $-4,400.00 | $19,600.00 | $0 | transfer XFR-000008 sent (Earth → Ceres) | owes $4,400.00 to E-Alice@Ceres via XFR-000008 (in transit) |
| 97.600 | E-Alice | cash @ Ceres | $4,400.00 | $25,400.00 | $21,000.00 | transfer XFR-000008 validated at destination | short margin on POS-000001 (pays losses); short margin on POS-000001 … |
| 97.600 | E-Alice | cash @ Ceres | lock change | $25,400.00 | $25,400.00 | margin top-up on POS-000001 | short margin on POS-000001 (pays losses); short margin on POS-000001 … |
| 133.049 | E-Alice | cash @ Earth | $-3,300.00 | $16,300.00 | $0 | transfer XFR-000009 sent (Earth → Ceres) | owes $3,300.00 to E-Alice@Ceres via XFR-000009 (in transit) |
| 133.597 | E-Alice | cash @ Ceres | $3,300.00 | $28,700.00 | $25,400.00 | transfer XFR-000009 validated at destination | short margin on POS-000001 (pays losses); short margin on POS-000001 … |
| 133.597 | E-Alice | cash @ Ceres | lock change | $28,700.00 | $28,700.00 | margin top-up on POS-000001 | short margin on POS-000001 (pays losses); short margin on POS-000001 … |
| 169.048 | E-Alice | cash @ Earth | $-3,300.00 | $13,000.00 | $0 | transfer XFR-000010 sent (Earth → Ceres) | owes $3,300.00 to E-Alice@Ceres via XFR-000010 (in transit) |
| 169.594 | E-Alice | cash @ Ceres | $3,300.00 | $32,000.00 | $28,700.00 | transfer XFR-000010 validated at destination | short margin on POS-000001 (pays losses); short margin on POS-000001 … |
| 169.594 | E-Alice | cash @ Ceres | lock change | $32,000.00 | $32,000.00 | margin top-up on POS-000001 | short margin on POS-000001 (pays losses); short margin on POS-000001 … |
| 300.509 | E-Alice | cash @ Ceres | $-20,000.00 | $12,000.00 | $0.00 | position POS-000001 settled at maturity |  |
| 300.509 | N-Eve | cash @ Ceres | $20,000.00 | $35,000.00 | $0.00 | position POS-000001 settled at maturity |  |
| 300.509 | N-Eve | cash @ Ceres | $-20,000.00 | $15,000.00 | $0.00 | transfer XFR-000011 sent (Ceres → Neptune) | owes $20,000.00 to N-Eve@Neptune via XFR-000011 (in transit) |
| 304.627 | N-Eve | cash @ Neptune | $20,000.00 | $60,000.00 | $0 | transfer XFR-000011 validated at destination |  |

## Funded default — variation where a real constraint binds (default)

Key steps (17 of 593 rows; full trace `traces/funded-default_trace.csv`).

| h | Actor | Local knowledge | Action | Transmission | Arrival | Financial state after |
|---|---|---|---|---|---|---|
| 0.100 | E-Alice | E-Alice sees own $45,000 free at Earth | TRANSFER INITIATED: XFR-000001: $25,000.00 Earth → Ceres (in transit) — Earth E… | — | — | E-Alice $45,000@Earth | in transit $25,000 / 0 sh |
| 0.100 | E-Bob | E-Bob sees own $20,000 free at Earth | TRANSFER INITIATED: XFR-000002: $25,000.00 Earth → Ceres (in transit) — Earth E… | — | — | E-Bob $20,000@Earth | in transit $50,000 / 0 sh |
| 0.500 | N-Eve | N-Eve sees own $40,000.00 free at Neptune | TRANSFER INITIATED: XFR-000003: $15,000.00 Neptune → Ceres (in transit) — Neptu… | — | — | N-Eve $40,000.00@Neptune | in transit $65,000.00 / 0 sh |
| 0.500 | E-Alice | E-Alice sees own $35,000.00 free at Earth | TRANSFER INITIATED: XFR-000004: $10,000.00 Earth → Ceres (in transit) — Earth E… | — | — | E-Alice $35,000.00@Earth | in transit $75,000.00 / 0 sh |
| 0.656 | E-Alice | E-Alice sees own $35,000.00 free at Earth | VALIDATED · FINAL: XFR-000001: usable by E-Alice at Ceres — cash validated at C… | — | — | E-Alice $25,000@Ceres | in transit $50,000.00 / 0 sh |
| 0.656 | E-Alice | E-Alice sees own $35,000.00 free at Earth | GUARANTEE CONTRIBUTION: E-Alice → Ceres Clearing: $25,000.00 — pre-funded guara… | — | — | Ceres Clearing $25,000@Ceres (locked $25,000); E-Alice $0@Ceres | in transit $50,000.00 / 0 sh |
| 0.656 | E-Bob | E-Bob sees own $20,000 free at Earth | VALIDATED · FINAL: XFR-000002: usable by E-Bob at Ceres — cash validated at Cer… | — | — | E-Bob $25,000@Ceres | in transit $25,000.00 / 0 sh |
| 0.656 | E-Bob | E-Bob sees own $20,000 free at Earth | GUARANTEE CONTRIBUTION: E-Bob → Ceres Clearing: $25,000.00 — pre-funded guarant… | — | — | Ceres Clearing $50,000@Ceres (locked $50,000); E-Bob $0@Ceres | in transit $25,000.00 / 0 sh |
| 1.000 |  |  | NOTE: REJECTED: open the future before N-Eve's margin reaches Earth (N-Eve lack… | — | — | unchanged |
| 1.056 | E-Alice | E-Alice sees own $35,000.00 free at Earth | VALIDATED · FINAL: XFR-000004: usable by E-Alice at Ceres — cash validated at C… | — | — | E-Alice $10,000.00@Ceres | in transit $15,000.00 / 0 sh |
| 4.605 | N-Eve | N-Eve sees own $40,000.00 free at Neptune | VALIDATED · FINAL: XFR-000003: usable by N-Eve at Ceres — cash validated at Cer… | — | — | N-Eve $15,000.00@Ceres | in transit $0 / 0 sh |
| 4.605 | Ceres | Ceres Clearing last received: margin_transfer from Earth Exchange @h1… | FUTURE OPENED: MOI-F300 10 @ 100: long N-Eve (margin 15000.00), short E-Alice (… | — | — | E-Alice $10,000.00@Ceres (locked $10,000.00); N-Eve $15,000.00@Ceres (locked $15,000.00) | in transit $0 / 0 … |
| 36.497 | Ceres Clearing | Ceres Clearing last received: mark from Mars Exchange @h24.50, mark f… | NOTE: MARGIN CALL N-Eve 5950.00 (deadline h 53.43) | — | — | unchanged |
| 53.430 | Ceres Clearing | Ceres Clearing last received: mark from Mars Exchange @h36.50, mark f… | NOTE: margin deadline passed for N-Eve: default at the next MOI observation | — | — | unchanged |
| 60.498 | Ceres | Ceres Clearing last received: mark from Mars Exchange @h48.50, mark f… | FUNDED DEFAULT: N-Eve on POS-000001 — margin deadline passed without a validate… | — | — | Ceres Clearing $43,000.00@Ceres (locked $43,000.00); E-Alice $32,000.00@Ceres; N-Eve $0@Ceres | in transit $0… |
| 60.498 | E-Alice | E-Alice sees own $35,000.00 free at Earth | TRANSFER INITIATED: XFR-000005: $22,000.00 Ceres → Earth (in transit) — payout … | — | — | E-Alice $10,000.00@Ceres | in transit $22,000.00 / 0 sh |
| 61.050 | E-Alice | E-Alice sees own $57,000.00 free at Earth | VALIDATED · FINAL: XFR-000005: usable by E-Alice at Earth — destination validat… | — | — | E-Alice $57,000.00@Earth | in transit $0 / 0 sh |

Balances and obligations (E3), 19 changes after the opening rows; full ledger `traces/funded-default_ledger.csv`.

| h | Owner | Asset @ location | Change | Balance | Encumbered | Source | Liability |
|---|---|---|---|---|---|---|---|
| 0.100 | E-Alice | cash @ Earth | $-25,000 | $45,000 | $0 | transfer XFR-000001 sent (Earth → Ceres) | owes $25,000 to E-Alice@Ceres via XFR-000001 (in transit) |
| 0.100 | E-Bob | cash @ Earth | $-25,000 | $20,000 | $0 | transfer XFR-000002 sent (Earth → Ceres) | owes $25,000 to E-Bob@Ceres via XFR-000002 (in transit) |
| 0.500 | N-Eve | cash @ Neptune | $-15,000.00 | $40,000.00 | $0 | transfer XFR-000003 sent (Neptune → Ceres) | owes $15,000.00 to N-Eve@Ceres via XFR-000003 (in transit) |
| 0.500 | E-Alice | cash @ Earth | $-10,000.00 | $35,000.00 | $0 | transfer XFR-000004 sent (Earth → Ceres) | owes $25,000 to E-Alice@Ceres via XFR-000001 (in transit); owes $10,0… |
| 0.656 | E-Alice | cash @ Ceres | $25,000 | $25,000 | $0 | transfer XFR-000001 validated at destination |  |
| 0.656 | Ceres Clearing | cash @ Ceres | $25,000 | $25,000 | $25,000 | guarantee contribution E-Alice → Ceres Clearing | guarantee fund: covers default losses; repayable to contributors |
| 0.656 | E-Alice | cash @ Ceres | $-25,000 | $0 | $0 | guarantee contribution E-Alice → Ceres Clearing |  |
| 0.656 | E-Bob | cash @ Ceres | $25,000 | $25,000 | $0 | transfer XFR-000002 validated at destination |  |
| 0.656 | Ceres Clearing | cash @ Ceres | $25,000 | $50,000 | $50,000 | guarantee contribution E-Bob → Ceres Clearing | guarantee fund: covers default losses; repayable to contributors; gua… |
| 0.656 | E-Bob | cash @ Ceres | $-25,000 | $0 | $0 | guarantee contribution E-Bob → Ceres Clearing |  |
| 1.056 | E-Alice | cash @ Ceres | $10,000.00 | $10,000.00 | $0 | transfer XFR-000004 validated at destination |  |
| 4.605 | N-Eve | cash @ Ceres | $15,000.00 | $15,000.00 | $0 | transfer XFR-000003 validated at destination |  |
| 4.605 | E-Alice | cash @ Ceres | lock change | $10,000.00 | $10,000.00 | position POS-000001 opened (margin locked) | short margin on POS-000001 (pays losses) |
| 4.605 | N-Eve | cash @ Ceres | lock change | $15,000.00 | $15,000.00 | position POS-000001 opened (margin locked) | long margin on POS-000001 (pays losses) |
| 60.498 | Ceres Clearing | cash @ Ceres | $-7,000.00 | $43,000.00 | $43,000.00 | position POS-000001 funded default | guarantee fund: covers default losses; repayable to contributors; gua… |
| 60.498 | E-Alice | cash @ Ceres | $22,000.00 | $32,000.00 | $0.00 | position POS-000001 funded default |  |
| 60.498 | N-Eve | cash @ Ceres | $-15,000.00 | $0 | $0 | position POS-000001 funded default | owes $7,000.00 to clearing for fund draw on POS-000001 |
| 60.498 | E-Alice | cash @ Ceres | $-22,000.00 | $10,000.00 | $0.00 | transfer XFR-000005 sent (Ceres → Earth) | owes $22,000.00 to E-Alice@Earth via XFR-000005 (in transit) |
| 61.050 | E-Alice | cash @ Earth | $22,000.00 | $57,000.00 | $0 | transfer XFR-000005 validated at destination |  |