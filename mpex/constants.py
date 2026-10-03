"""Fixed parameters shared by every team (Participant Brief, Section 2).

All model times in this package are elapsed hours since the epoch
(2026-09-22 00:00:00 TDB, t = 0). Use the helpers at the bottom to convert.
"""

from decimal import Decimal
from enum import Enum


class Settlement(str, Enum):
    MERCURY = "Mercury"
    VENUS = "Venus"
    EARTH = "Earth"
    MARS = "Mars"
    CERES = "Ceres"
    JUPITER = "Jupiter"
    SATURN = "Saturn"
    URANUS = "Uranus"
    NEPTUNE = "Neptune"


class Relay(str, Enum):
    A = "Relay A"
    B = "Relay B"


# Node IDs order simultaneous arrivals (ascending sender node ID, then sequence).
NODE_IDS = {
    Settlement.MERCURY.value: 1,
    Settlement.VENUS.value: 2,
    Settlement.EARTH.value: 3,
    Settlement.MARS.value: 4,
    Settlement.CERES.value: 5,
    Settlement.JUPITER.value: 6,
    Settlement.SATURN.value: 7,
    Settlement.URANUS.value: 8,
    Settlement.NEPTUNE.value: 9,
    Relay.A.value: 10,
    Relay.B.value: 11,
}

SETTLEMENTS = tuple(s.value for s in Settlement)
NODES = tuple(NODE_IDS)

# --- Time -----------------------------------------------------------------
SECONDS_PER_DAY = 86_400
HOURS_PER_DAY = 24
DAYS_PER_JULIAN_YEAR = 365.25
EARLIEST_SESSION_SETUP_H = -168.0  # sessions may be set up from hour -168
FIRST_FINANCIAL_ACTION_H = 0.0     # no financial action before hour 0

# --- Opening balance sheet limits (Section 2 / 6) -------------------------
MAX_OPENING_NEODOLLARS = Decimal("500000")
MAX_OPENING_SHARES = 5_000
MIN_ACCOUNTS = 4
MAX_ACCOUNTS = 10
MIN_ACCOUNT_SETTLEMENTS = 3
MAX_INSTITUTIONS = 12

# --- Communication (Section 2 / 4 / 5) ------------------------------------
LIGHT_MINUTES_PER_AU = 8.317
SOLAR_EXCLUSION_RADIUS_AU = 0.10
BACKBONE_LOSS_PER_AU = 0.02
DIRECT_LOSS_PER_AU = 0.08

QUOTA_WINDOW_H = 24.0
BACKBONE_QUOTA_PER_WINDOW = 600          # shared by the whole system
DIRECT_QUOTA_PER_WINDOW = 12             # per principal
DIRECT_MIN_SPACING_S = 60
INSTITUTION_DIRECT_QUOTA_PER_WINDOW = 12  # per institution, client comms only

MAX_PACKET_BYTES = 1_024
PACKET_HEADER_BYTES = 64
MAX_PAYLOAD_BYTES = MAX_PACKET_BYTES - PACKET_HEADER_BYTES

LINK_CAPACITY_PER_S = 1
LINK_QUEUE_LIMIT = 10_000
MAX_ROUTE_LINKS = 3
MAX_HOP_LAUNCHES = 4
MAX_ENDPOINT_ATTEMPTS = 4
SEND_WINDOW_PACKETS = 64
PACKET_LIFETIME_H = 30 * HOURS_PER_DAY
DUPLICATE_ID_RETENTION_H = 30 * HOURS_PER_DAY  # after last receipt
SESSION_IDLE_EXPIRY_H = 7 * HOURS_PER_DAY

SERIALIZATION_S = 1
RELAY_PROCESSING_S = 1
LOCAL_ACCESS_S = 1

# One-time scheduled maintenance, both directions: (node, node, start_h, end_h)
MAINTENANCE_WINDOWS = (
    (Relay.B.value, Settlement.NEPTUNE.value, 2.0, 26.0),
    (Relay.B.value, Settlement.CERES.value, 240.0, 264.0),
)

# --- Units ----------------------------------------------------------------
NEODOLLAR = "NEO"  # asset code for NeoDollars


def seconds_to_hours(seconds: float) -> float:
    return seconds / 3600.0


def days_to_hours(days: float) -> float:
    return days * HOURS_PER_DAY


def years_to_hours(julian_years: float) -> float:
    return julian_years * DAYS_PER_JULIAN_YEAR * HOURS_PER_DAY


def validate_node(name: str) -> str:
    if name not in NODE_IDS:
        raise ValueError(f"unknown node {name!r}")
    return name


def validate_settlement(name: str) -> str:
    if name not in SETTLEMENTS:
        raise ValueError(f"unknown settlement {name!r}")
    return name
