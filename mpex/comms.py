"""Communication records: sessions, messages, packets, launches, and quotas.

This module holds communication *state*: what was sent, by whom, over which
service, which launches happened, and how much quota is used. Computing
light time, visibility, and loss is the job of an orbital/transport model
that writes its results here through ``Exchange.record_launch``.
"""

import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from .constants import (BACKBONE_QUOTA_PER_WINDOW, DIRECT_MIN_SPACING_S,
                        DIRECT_QUOTA_PER_WINDOW, MAX_PAYLOAD_BYTES, MAX_ROUTE_LINKS,
                        NODE_IDS, PACKET_LIFETIME_H, QUOTA_WINDOW_H, SETTLEMENTS,
                        Relay, seconds_to_hours)
from .errors import QuotaExceeded, ValidationError


class Service(str, Enum):
    BACKBONE = "backbone"  # operators and their clearing/settlement services only
    DIRECT = "direct"      # any principal, client communication only, lossy
    LOCAL = "local"        # same settlement: 1 s, no loss, no quota


class TrafficClass(str, Enum):
    OFFICIAL = "official"  # creates a shared exchange record or settlement authority
    CLIENT = "client"      # orders, instructions, observations


class PacketKind(str, Enum):
    DATA = "data"
    SYN = "syn"
    SYN_ACK = "syn_ack"
    ACK = "ack"
    HOP_RECEIPT = "hop_receipt"
    DATA_ACK = "data_ack"

    @property
    def counts_against_backbone_quota(self) -> bool:
        # Section 4: data, application control, and SYN count; transport acks do not.
        return self in (PacketKind.DATA, PacketKind.SYN)


class MessageStatus(str, Enum):
    QUEUED = "queued"
    IN_FLIGHT = "in_flight"
    DELIVERED = "delivered"
    LOST = "lost"            # direct service: no retry exists
    ABANDONED = "abandoned"  # attempts exhausted; delivery status unknown to sender
    EXPIRED = "expired"      # 30-day packet lifetime passed


class SessionState(str, Enum):
    HANDSHAKING = "handshaking"
    ESTABLISHED = "established"
    EXPIRED = "expired"
    RESET = "reset"


def backbone_links() -> set[tuple[str, str]]:
    """The 19 two-way candidate links, as 38 directed pairs."""
    links = set()
    for s in SETTLEMENTS:
        for r in (Relay.A.value, Relay.B.value):
            links.add((s, r))
            links.add((r, s))
    links.add((Relay.A.value, Relay.B.value))
    links.add((Relay.B.value, Relay.A.value))
    return links


BACKBONE_LINKS = frozenset(backbone_links())


def validate_route(route: list[str]) -> None:
    if len(route) < 2:
        raise ValidationError("a route needs at least two nodes")
    if len(set(route)) != len(route):
        raise ValidationError("route repeats a node")
    if len(route) - 1 > MAX_ROUTE_LINKS:
        raise ValidationError(f"route exceeds {MAX_ROUTE_LINKS} backbone links")
    for a, b in zip(route, route[1:]):
        if (a, b) not in BACKBONE_LINKS:
            raise ValidationError(f"{a} -> {b} is not a backbone link")


def packets_needed(size_bytes: int) -> int:
    if size_bytes <= 0:
        raise ValidationError("message size must be positive")
    return math.ceil(size_bytes / MAX_PAYLOAD_BYTES)


@dataclass
class Launch:
    """One emission of one packet on one directed link (or a direct path)."""
    packet_id: str
    from_node: str
    to_node: str
    emit_h: float
    arrival_h: float | None   # None when the path was blocked
    lost: bool
    attempt: int = 1          # hop launch number (1-4) or endpoint attempt
    kind: PacketKind = PacketKind.DATA

    def to_dict(self) -> dict:
        return {"packet_id": self.packet_id, "from": self.from_node, "to": self.to_node,
                "emit_h": self.emit_h, "arrival_h": self.arrival_h, "lost": self.lost,
                "attempt": self.attempt, "kind": self.kind.value}


@dataclass
class Packet:
    id: str
    message_id: str | None
    session_id: str | None
    kind: PacketKind
    seq: int                  # sender-assigned sequence number
    size_bytes: int
    created_h: float
    counts_against_quota: bool
    launches: list[Launch] = field(default_factory=list)

    @property
    def expires_h(self) -> float:
        return self.created_h + PACKET_LIFETIME_H

    def to_dict(self) -> dict:
        return {"id": self.id, "message_id": self.message_id, "session_id": self.session_id,
                "kind": self.kind.value, "seq": self.seq, "size_bytes": self.size_bytes,
                "created_h": self.created_h, "expires_h": self.expires_h,
                "counts_against_quota": self.counts_against_quota,
                "launches": [l.to_dict() for l in self.launches]}


@dataclass
class Session:
    id: str
    initiator: str
    peer: str
    route: list[str]
    opened_h: float
    state: SessionState = SessionState.HANDSHAKING
    established_h: float | None = None
    last_rx_h: float | None = None
    closed_h: float | None = None

    def to_dict(self) -> dict:
        return {"id": self.id, "initiator": self.initiator, "peer": self.peer,
                "route": list(self.route), "opened_h": self.opened_h,
                "state": self.state.value, "established_h": self.established_h,
                "last_rx_h": self.last_rx_h, "closed_h": self.closed_h}


@dataclass
class Message:
    """An application-level message; may span several packets."""
    id: str
    sender: str
    recipient: str
    from_node: str
    to_node: str
    service: Service
    traffic_class: TrafficClass
    kind: str                 # e.g. "order", "settlement_instruction", "margin_call"
    payload: dict
    size_bytes: int
    created_h: float
    session_id: str | None = None
    packet_ids: list[str] = field(default_factory=list)
    status: MessageStatus = MessageStatus.QUEUED
    delivered_h: float | None = None
    references: list[str] = field(default_factory=list)  # order/trade/position ids

    def to_dict(self) -> dict:
        return {"id": self.id, "sender": self.sender, "recipient": self.recipient,
                "from": self.from_node, "to": self.to_node, "service": self.service.value,
                "traffic_class": self.traffic_class.value, "kind": self.kind,
                "payload": self.payload, "size_bytes": self.size_bytes,
                "created_h": self.created_h, "session_id": self.session_id,
                "packet_ids": list(self.packet_ids), "status": self.status.value,
                "delivered_h": self.delivered_h, "references": list(self.references)}


class QuotaTracker:
    """Rolling 24 h origination windows for both services (Section 4)."""

    def __init__(self) -> None:
        self.backbone: deque[float] = deque()
        self.direct: dict[str, deque[float]] = {}

    @staticmethod
    def _used(times: deque, at_h: float) -> int:
        return sum(1 for t in times if t > at_h - QUOTA_WINDOW_H)

    def backbone_used(self, at_h: float) -> int:
        return self._used(self.backbone, at_h)

    def direct_used(self, principal: str, at_h: float) -> int:
        return self._used(self.direct.get(principal, deque()), at_h)

    def check_backbone(self, at_h: float, count: int) -> None:
        if self.backbone_used(at_h) + count > BACKBONE_QUOTA_PER_WINDOW:
            raise QuotaExceeded(
                f"backbone quota: {self.backbone_used(at_h)} used in the last 24 h, "
                f"{count} more would exceed {BACKBONE_QUOTA_PER_WINDOW}")

    def plan_direct(self, principal: str, at_h: float, count: int) -> list[float]:
        """Earliest launch times for ``count`` direct packets, 60 s apart.

        Raises if any would exceed 12 in its rolling window.
        """
        times = list(self.direct.get(principal, deque()))
        spacing = seconds_to_hours(DIRECT_MIN_SPACING_S)
        out = []
        t = at_h
        for _ in range(count):
            if times:
                t = max(t, times[-1] + spacing)
            used = sum(1 for x in times if x > t - QUOTA_WINDOW_H)
            if used >= DIRECT_QUOTA_PER_WINDOW:
                raise QuotaExceeded(
                    f"direct quota for {principal}: {used} launches in the 24 h before h {t:.4f}")
            times.append(t)
            out.append(t)
        return out

    def record_backbone(self, at_h: float, count: int = 1) -> None:
        self.backbone.extend([at_h] * count)

    def record_direct(self, principal: str, launch_times: list[float]) -> None:
        self.direct.setdefault(principal, deque()).extend(launch_times)

    def prune(self, at_h: float) -> None:
        """Drop originations that can no longer affect any window (bounded storage)."""
        cutoff = at_h - QUOTA_WINDOW_H
        while self.backbone and self.backbone[0] <= cutoff:
            self.backbone.popleft()
        for q in self.direct.values():
            while q and q[0] <= cutoff:
                q.popleft()

    def to_dict(self, at_h: float) -> dict:
        return {"backbone_used_24h": self.backbone_used(at_h),
                "backbone_limit": BACKBONE_QUOTA_PER_WINDOW,
                "direct_used_24h": {p: self.direct_used(p, at_h) for p in sorted(self.direct)},
                "direct_limit": DIRECT_QUOTA_PER_WINDOW}


def node_order_key(node: str, seq: int) -> tuple[int, int]:
    """Ordering for simultaneous arrivals: sender node ID, then sequence."""
    return (NODE_IDS[node], seq)
