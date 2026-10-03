# Fixed-Width Financial Application Wire Format

This is the exchange model's chosen application encoding. The participant brief fixes the packet/header/payload limits and requires fixed-width, uncompressed encoding; the field allocation below is the team's design.

## Network packet

| Component | Bytes |
|---|---:|
| Network header | 64 |
| Application payload | 960 |
| **Total** | **1,024** |

## Application payload

| Component | Bytes |
|---|---:|
| Fixed envelope | 32 |
| 9 financial records × 96 | 864 |
| Zero padding | 64 |
| **Total** | **960** |

Thus `RECORDS_PER_PACKET = 9`.

## 32-byte envelope

Big-endian struct: `>BBHQQHqH`

| Field | Bytes | Meaning |
|---|---:|---|
| version | 1 | Encoding version; currently 1 |
| message_type | 1 | BATCH, CLEAR_CONFIRM, SETTLEMENT, MARGIN, PRICE, ORDER, or APPLICATION_CONTROL |
| flags | 2 | Application flags |
| message_id | 8 | Unique application message identifier |
| batch_id | 8 | Batch identifier |
| record_count | 2 | Number of 96-byte records in this packet |
| created_time_us | 8 | Signed microseconds from simulation epoch |
| reserved | 2 | Zero |

## 96-byte financial record

Big-endian struct: `>2sH16s16s16s8sqqqqBB2s`

| Field | Bytes | Meaning |
|---|---:|---|
| record_type | 2 | ASCII fixed-width record code |
| flags | 2 | Record flags |
| object_id | 16 | Order/trade/contract/obligation identifier |
| account_a | 16 | First account identifier |
| account_b | 16 | Second account identifier |
| symbol | 8 | Product/security symbol |
| quantity | 8 | Signed 64-bit quantity |
| price_micros | 8 | Signed integer micro-NeoDollars |
| event_time_us | 8 | Signed microseconds from simulation epoch |
| aux_micros | 8 | Product-specific fixed-point auxiliary value |
| settlement_id | 1 | Settlement/node code |
| reserved | 1 | Zero |
| pad | 2 | Zero |
| **Total** | **96** | |

Text fields are ASCII and space-padded; over-width values are rejected. Monetary fixed-point values use integer micro-NeoDollars to avoid floating-point ambiguity on the wire.

`FixedWidthApplicationEncoding.encode_payload()` creates the actual 960-byte payload and asserts its exact length. `packet_count(record_count)` is used by both placement screening and final workload generation, so packet burden is derived from this encoding rather than a separate record-size assumption.
