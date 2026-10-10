"""Append-only event journal: the durable record the exchange state derives from.

Every state change is one ``Event``. Replaying the journal up to hour ``t``
rebuilds the exact state at ``t``, which is how trace rows ("financial state
after") and audits are produced. Events carry JSON-safe data only, so the
journal can be written to and read from a JSON-lines file.
"""

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class EventType(str, Enum):
    EXCHANGE_OPENED = "exchange_opened"
    INSTITUTION_CHARTERED = "institution_chartered"
    PRICE_SOURCE_REGISTERED = "price_source_registered"
    INSTRUMENT_LISTED = "instrument_listed"
    ORDER_ACCEPTED = "order_accepted"
    TRADE_EXECUTED = "trade_executed"
    ORDER_CANCELLED = "order_cancelled"
    POSITION_OPENED = "position_opened"
    MARGIN_POSTED = "margin_posted"
    POSITION_MARKED = "position_marked"
    POSITION_SETTLED = "position_settled"
    POSITION_DEFAULTED = "position_defaulted"
    TRANSFER_INITIATED = "transfer_initiated"
    TRANSFER_COMPLETED = "transfer_completed"
    TRANSFER_RETURNED = "transfer_returned"
    OBSERVATION_RELEASED = "observation_released"
    SESSION_OPENED = "session_opened"
    SESSION_STATE_CHANGED = "session_state_changed"
    MESSAGE_SENT = "message_sent"
    TRANSPORT_PACKET_CREATED = "transport_packet_created"
    PACKET_LAUNCHED = "packet_launched"
    MESSAGE_STATUS_CHANGED = "message_status_changed"
    BATCH_OPENED = "batch_opened"
    BATCH_ORDER_RESERVED = "batch_order_reserved"
    BATCH_ORDER_ACCEPTED = "batch_order_accepted"
    BATCH_ORDER_ROLLED = "batch_order_rolled"
    BATCH_ORDER_CANCELLED = "batch_order_cancelled"
    BATCH_CANCEL_REJECTED = "batch_cancel_rejected"
    BATCH_EXECUTED = "batch_executed"
    BATCH_LEG_SETTLED = "batch_leg_settled"
    TRANSFER_DUPLICATE_IGNORED = "transfer_duplicate_ignored"
    TRANSFER_CONFIRMED = "transfer_confirmed"
    GUARANTEE_CONTRIBUTED = "guarantee_contributed"
    NOTE = "note"  # free-text trace annotation; no state change


@dataclass(frozen=True)
class Event:
    seq: int
    time_h: float
    type: EventType
    actor: str | None
    data: dict = field(default_factory=dict)
    note: str | None = None  # e.g. what the actor knows at this moment

    def to_dict(self) -> dict:
        return {"seq": self.seq, "time_h": self.time_h, "type": self.type.value,
                "actor": self.actor, "data": self.data, "note": self.note}

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        return cls(d["seq"], d["time_h"], EventType(d["type"]), d.get("actor"),
                   d.get("data", {}), d.get("note"))


class Journal:
    def __init__(self) -> None:
        self._events: list[Event] = []

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self):
        return iter(self._events)

    @property
    def last_time_h(self) -> float | None:
        return self._events[-1].time_h if self._events else None

    def next_seq(self) -> int:
        return len(self._events) + 1

    def append(self, event: Event) -> None:
        self._events.append(event)

    def until(self, time_h: float) -> list[Event]:
        return [e for e in self._events if e.time_h <= time_h]

    def of_type(self, *types: EventType) -> list[Event]:
        return [e for e in self._events if e.type in types]

    def referencing(self, identifier: str) -> list[Event]:
        """Every event whose data mentions ``identifier`` (follow one transaction)."""
        needle = json.dumps(identifier)
        return [e for e in self._events if needle in json.dumps(e.data)]

    def save(self, path: str | Path) -> None:
        with open(path, "w") as f:
            for e in self._events:
                f.write(json.dumps(e.to_dict()) + "\n")

    @staticmethod
    def load(path: str | Path) -> list[Event]:
        with open(path) as f:
            return [Event.from_dict(json.loads(line)) for line in f if line.strip()]
