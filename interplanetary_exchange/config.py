from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

SETTLEMENTS = (
    "Mercury", "Venus", "Earth", "Mars", "Ceres",
    "Jupiter", "Saturn", "Uranus", "Neptune",
)
RELAYS = ("Relay A", "Relay B")


@dataclass
class ModelConfig:
    # Brief-fixed transport constraints
    backbone_originations_per_rolling_24h: int = 600
    direct_packets_per_principal_rolling_24h: int = 12
    direct_min_launch_spacing_seconds: float = 60.0
    packet_bytes: int = 1024
    header_bytes: int = 64
    backbone_link_capacity_packets_per_second: int = 1
    backbone_queue_capacity: int = 10_000
    max_backbone_links_per_route: int = 3
    hop_max_launches: int = 4
    endpoint_max_attempts: int = 4
    packet_lifetime_days: float = 30.0
    session_expiry_days: float = 7.0
    send_window_packets: int = 64
    serialization_seconds: float = 1.0
    relay_processing_seconds: float = 1.0
    local_access_seconds: float = 1.0
    duplicate_retention_extra_days: float = 30.0

    # Chosen defaults requested by user
    reliability_target: float = 0.99
    min_exchanges: int = 3
    max_exchanges: int = 5
    daily_client_instructions_per_principal: int = 4
    batch_interval_hours: float = 6.0
    price_source_settlement: str = "Ceres"
    price_observation_interval_hours: float = 24.0
    direct_application_retry_hours: float = 24.0
    direct_application_max_attempts: int = 4

    # Placement objective. Financial guarantees are hard constraints; weights only
    # rank feasible designs.
    availability_weight: float = 1_000_000.0
    quota_weight: float = 100_000.0
    latency_weight: float = 1.0
    loss_weight: float = 1_000.0
    packet_weight: float = 25.0
    institution_weight: float = 50.0

    # Screening settings (full E4 scan can be run separately at finer resolution)
    placement_scan_years: float = 20.0
    placement_scan_step_days: float = 90.0
    e4_scan_years: float = 200.0
    e4_link_step_days: float = 20.0
    e4_route_step_days: float = 45.0
    e4_boundary_tolerance_seconds: float = 1.0

    # Margin rule used for both rising/falling futures paths
    futures_base_margin_fraction: float = 0.20
    futures_margin_call_grace_hours: float = 24.0

    # Default equal demand; can be overridden per settlement.
    demand_per_day: Dict[str, int] = field(
        default_factory=lambda: {s: 4 for s in SETTLEMENTS}
    )

    # Stress balance sheet: 9 accounts, 3+ settlements, exactly $500k and 5,000 shares.
    opening_accounts: Tuple[dict, ...] = (
        {"account_id": "MERCURY_A", "settlement": "Mercury", "cash": 20_000.0, "shares": 0},
        {"account_id": "VENUS_A", "settlement": "Venus", "cash": 25_000.0, "shares": 200},
        {"account_id": "EARTH_A", "settlement": "Earth", "cash": 75_000.0, "shares": 100},
        {"account_id": "MARS_A", "settlement": "Mars", "cash": 80_000.0, "shares": 200},
        {"account_id": "CERES_A", "settlement": "Ceres", "cash": 40_000.0, "shares": 800},
        {"account_id": "JUPITER_A", "settlement": "Jupiter", "cash": 60_000.0, "shares": 700},
        {"account_id": "SATURN_A", "settlement": "Saturn", "cash": 55_000.0, "shares": 800},
        {"account_id": "URANUS_A", "settlement": "Uranus", "cash": 65_000.0, "shares": 1000},
        {"account_id": "NEPTUNE_A", "settlement": "Neptune", "cash": 80_000.0, "shares": 1200},
    )


def default_config() -> ModelConfig:
    return ModelConfig()
