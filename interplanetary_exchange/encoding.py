from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Iterable


@dataclass(frozen=True)
class FinancialRecord:
    """One fixed-width 96-byte application record.

    Text fields are ASCII, space padded and rejected if they exceed their field.
    Monetary values use integer micro-NeoDollars so the wire representation is exact.
    Times are integer microseconds from the simulation epoch.
    """

    record_type: str
    object_id: str = ""
    account_a: str = ""
    account_b: str = ""
    symbol: str = ""
    quantity: int = 0
    price_micros: int = 0
    event_time_us: int = 0
    aux_micros: int = 0
    settlement_id: int = 0
    flags: int = 0


class FixedWidthApplicationEncoding:
    """Submitted application wire format.

    Every financial application network packet is exactly 1,024 bytes:
      * 64-byte network header (fixed by the brief)
      * 960-byte application payload

    The application payload is:
      * 32-byte fixed envelope
      * up to nine 96-byte records
      * zero padding to 960 bytes

    Therefore one financial application packet can carry exactly 9 records.  This
    replaces the old screening assumption of ten records per packet.
    """

    NETWORK_HEADER_BYTES = 64
    PAYLOAD_BYTES = 960
    ENVELOPE_BYTES = 32
    RECORD_BYTES = 96
    PACKET_BYTES = NETWORK_HEADER_BYTES + PAYLOAD_BYTES
    RECORDS_PER_PACKET = (PAYLOAD_BYTES - ENVELOPE_BYTES) // RECORD_BYTES
    PADDING_BYTES = PAYLOAD_BYTES - ENVELOPE_BYTES - RECORDS_PER_PACKET * RECORD_BYTES

    # 32 bytes: version(1), msg_type(1), flags(2), message_id(8), batch_id(8),
    # record_count(2), created_us(8), reserved(2)
    _ENVELOPE = struct.Struct(">BBHQQHqH")

    # 96 bytes exactly.
    # type(2), flags(2), object(16), acctA(16), acctB(16), symbol(8),
    # quantity(8), price_micro(8), event_us(8), aux_micro(8), settlement(1), reserved(1), pad(2)
    _RECORD = struct.Struct(">2sH16s16s16s8sqqqqBB2s")

    MESSAGE_TYPES = {
        "BATCH": 1,
        "CLEAR_CONFIRM": 2,
        "SETTLEMENT": 3,
        "MARGIN": 4,
        "PRICE": 5,
        "ORDER": 6,
        "APPLICATION_CONTROL": 7,
    }

    @staticmethod
    def _text(value: str, width: int) -> bytes:
        raw = value.encode("ascii")
        if len(raw) > width:
            raise ValueError(f"{value!r} exceeds fixed field width {width}")
        return raw.ljust(width, b" ")

    @classmethod
    def encode_record(cls, record: FinancialRecord) -> bytes:
        rtype = cls._text(record.record_type, 2)
        return cls._RECORD.pack(
            rtype,
            int(record.flags),
            cls._text(record.object_id, 16),
            cls._text(record.account_a, 16),
            cls._text(record.account_b, 16),
            cls._text(record.symbol, 8),
            int(record.quantity),
            int(record.price_micros),
            int(record.event_time_us),
            int(record.aux_micros),
            int(record.settlement_id),
            0,
            b"\x00\x00",
        )

    @classmethod
    def encode_payload(
        cls,
        records: Iterable[FinancialRecord],
        *,
        message_type: str = "BATCH",
        message_id: int = 0,
        batch_id: int = 0,
        created_time_us: int = 0,
        flags: int = 0,
    ) -> bytes:
        records = list(records)
        if len(records) > cls.RECORDS_PER_PACKET:
            raise ValueError(
                f"packet supports {cls.RECORDS_PER_PACKET} records, got {len(records)}"
            )
        if message_type not in cls.MESSAGE_TYPES:
            raise ValueError(f"unknown message type {message_type!r}")
        envelope = cls._ENVELOPE.pack(
            1,
            cls.MESSAGE_TYPES[message_type],
            int(flags),
            int(message_id),
            int(batch_id),
            len(records),
            int(created_time_us),
            0,
        )
        body = b"".join(cls.encode_record(r) for r in records)
        payload = envelope + body
        payload += b"\x00" * (cls.PAYLOAD_BYTES - len(payload))
        if len(payload) != cls.PAYLOAD_BYTES:
            raise AssertionError("fixed-width payload length is not 960 bytes")
        return payload

    @classmethod
    def packet_count(cls, record_count: int) -> int:
        if record_count <= 0:
            return 0
        return (record_count + cls.RECORDS_PER_PACKET - 1) // cls.RECORDS_PER_PACKET


assert FixedWidthApplicationEncoding._ENVELOPE.size == 32
assert FixedWidthApplicationEncoding._RECORD.size == 96
assert FixedWidthApplicationEncoding.RECORDS_PER_PACKET == 9
