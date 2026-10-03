from __future__ import annotations

from dataclasses import dataclass
from math import exp
from typing import Optional

import numpy as np

from .config import ModelConfig, RELAYS, SETTLEMENTS
from .orbital import OrbitalModel

MINUTES_PER_DAY = 1440.0
SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True)
class Incident:
    kind: str  # "gateway_isolation", "forced_loss", "endpoint_reset"
    node: str
    start_hours: float
    duration_hours: float

    @property
    def end_hours(self) -> float:
        return self.start_hours + self.duration_hours


class NetworkModel:
    def __init__(self, orbital: OrbitalModel, network_data: dict, config: ModelConfig):
        self.orbital = orbital
        self.data = network_data
        self.config = config
        self.light_min_per_au = float(network_data["light_minutes_per_au"])
        self.solar_radius = float(network_data["solar_exclusion_radius_au"])
        self.maintenance = network_data.get("maintenance", [])

    def moving_receiver_light_time(self, sender: str, receiver: str, t_emit_days: float,
                                   tol_ms: float = 1.0, max_iter: int = 100) -> dict:
        r_sender = self.orbital.position(sender, t_emit_days)
        r_receiver = self.orbital.position(receiver, t_emit_days)
        d = float(np.linalg.norm(r_receiver - r_sender))
        t_arr = t_emit_days + (self.light_min_per_au * d) / MINUTES_PER_DAY
        for iteration in range(1, max_iter + 1):
            r_receiver = self.orbital.position(receiver, t_arr)
            d = float(np.linalg.norm(r_receiver - r_sender))
            delay_min = self.light_min_per_au * d
            t_new = t_emit_days + delay_min / MINUTES_PER_DAY
            err_ms = abs(t_new - t_arr) * SECONDS_PER_DAY * 1000.0
            if err_ms < tol_ms:
                return {
                    "sender": sender, "receiver": receiver,
                    "emission_time_days": t_emit_days,
                    "arrival_time_days": t_new,
                    "light_time_minutes": delay_min,
                    "distance_au": d,
                    "iterations": iteration,
                    "error_ms": err_ms,
                }
            t_arr = t_new
        raise RuntimeError("Moving-receiver light-time solver did not converge")

    @staticmethod
    def closest_approach_to_origin(a: np.ndarray, b: np.ndarray) -> tuple[float, float, np.ndarray]:
        v = b - a
        vv = float(np.dot(v, v))
        if vv == 0.0:
            return float(np.linalg.norm(a)), 0.0, a.copy()
        u = -float(np.dot(a, v)) / vv
        u = float(np.clip(u, 0.0, 1.0))
        p = a + u * v
        return float(np.linalg.norm(p)), u, p

    def photon_geometry(self, sender: str, receiver: str, t_emit_days: float) -> dict:
        lt = self.moving_receiver_light_time(sender, receiver, t_emit_days)
        rs = self.orbital.position(sender, t_emit_days)
        rr = self.orbital.position(receiver, lt["arrival_time_days"])
        d_sun, u, p = self.closest_approach_to_origin(rs, rr)
        # Brief says "under 0.10 AU" in the propagation section.
        blocked = d_sun < self.solar_radius
        return {
            **lt,
            "solar_closest_approach_au": d_sun,
            "solar_closest_fraction": u,
            "solar_closest_point": p,
            "solar_blocked": blocked,
        }

    def backbone_loss_probability(self, distance_au: float) -> float:
        return 1.0 - exp(-0.02 * distance_au)

    def direct_loss_probability(self, distance_au: float) -> float:
        return 1.0 - exp(-0.08 * distance_au)

    @staticmethod
    def _undirected_edge_matches(a: str, b: str, edge: list[str]) -> bool:
        return {a, b} == set(edge)

    def maintenance_overlap(self, sender: str, receiver: str,
                            t_emit_days: float, t_arrive_days: float) -> bool:
        e0 = t_emit_days * 24.0
        e1 = t_arrive_days * 24.0
        for m in self.maintenance:
            if not self._undirected_edge_matches(sender, receiver, m["edge"]):
                continue
            if max(e0, float(m["start_hours"])) < min(e1, float(m["end_hours"])):
                return True
        return False

    def backbone_candidate_edge(self, sender: str, receiver: str) -> bool:
        if sender in SETTLEMENTS and receiver in RELAYS:
            return True
        if receiver in SETTLEMENTS and sender in RELAYS:
            return True
        return {sender, receiver} == set(RELAYS)

    def incident_effect(self, sender: str, receiver: str, t_emit_days: float,
                        incident: Optional[Incident]) -> Optional[str]:
        if incident is None:
            return None
        h = t_emit_days * 24.0
        if not (incident.start_hours <= h < incident.end_hours):
            return None
        if incident.kind == "gateway_isolation":
            if incident.node in {sender, receiver}:
                return "FORCED_FAIL"
        elif incident.kind == "forced_loss":
            if incident.node in {sender, receiver}:
                return "FORCED_FAIL"
        return None

    def link_launch(self, sender: str, receiver: str, t_emit_days: float,
                    service: str, incident: Optional[Incident] = None) -> dict:
        if service == "backbone" and not self.backbone_candidate_edge(sender, receiver):
            return {"can_launch": False, "status": "NOT_BACKBONE_EDGE"}
        geom = self.photon_geometry(sender, receiver, t_emit_days)
        if geom["solar_blocked"]:
            return {**geom, "can_launch": False, "status": "SOLAR_BLOCKED"}
        if service == "backbone" and self.maintenance_overlap(
            sender, receiver, t_emit_days, geom["arrival_time_days"]
        ):
            return {**geom, "can_launch": False, "status": "MAINTENANCE"}
        forced = self.incident_effect(sender, receiver, t_emit_days, incident)
        p = self.backbone_loss_probability(geom["distance_au"]) if service == "backbone" else self.direct_loss_probability(geom["distance_au"])
        if forced:
            return {**geom, "can_launch": True, "status": forced, "loss_probability": 1.0, "success_probability": 0.0}
        return {**geom, "can_launch": True, "status": "CAN_LAUNCH", "loss_probability": p, "success_probability": 1.0 - p}

    def next_open_emission_time(self, sender: str, receiver: str, t_candidate_days: float,
                                service: str = "backbone", max_wait_days: float | None = None,
                                scan_step_minutes: float = 60.0,
                                tolerance_seconds: float = 1.0) -> dict:
        """Earliest *known* launch opportunity at or after t_candidate_days.

        Solar geometry and scheduled maintenance are known in advance. Incidents are
        deliberately excluded because the brief says they are not announced.
        """
        if max_wait_days is None:
            max_wait_days = self.config.packet_lifetime_days
        if service == "backbone" and not self.backbone_candidate_edge(sender, receiver):
            return {"found": False, "status": "NOT_BACKBONE_EDGE"}

        def check(t: float) -> dict:
            return self.link_launch(sender, receiver, t, service, incident=None)

        first = check(t_candidate_days)
        if first.get("can_launch", False):
            return {"found": True, "emission_time_days": t_candidate_days, "wait_days": 0.0,
                    "launch": first}

        step = scan_step_minutes / MINUTES_PER_DAY
        end = t_candidate_days + max_wait_days
        left = t_candidate_days
        right = left
        open_result = None
        while right < end:
            left = right
            right = min(end, right + step)
            r = check(right)
            if r.get("can_launch", False):
                open_result = r
                break
        if open_result is None:
            return {"found": False, "status": "NO_OPEN_LINK_WITHIN_LIFETIME",
                    "last_status": first.get("status")}

        tol_days = tolerance_seconds / SECONDS_PER_DAY
        # Refine the first sampled closed->open boundary.
        while right - left > tol_days:
            mid = 0.5 * (left + right)
            r = check(mid)
            if r.get("can_launch", False):
                right = mid
                open_result = r
            else:
                left = mid
        final = check(right)
        return {"found": True, "emission_time_days": right,
                "wait_days": right - t_candidate_days, "launch": final}

    def refine_availability_boundary(self, sender: str, receiver: str,
                                     t_closed_days: float, t_open_days: float,
                                     service: str = "backbone",
                                     tolerance_seconds: float = 1.0) -> dict:
        """Refine a known closed/open bracket to the requested tolerance."""
        left, right = float(t_closed_days), float(t_open_days)
        if self.link_launch(sender, receiver, left, service).get("can_launch", False):
            raise ValueError("left boundary must be closed")
        if not self.link_launch(sender, receiver, right, service).get("can_launch", False):
            raise ValueError("right boundary must be open")
        tol = tolerance_seconds / SECONDS_PER_DAY
        while right - left > tol:
            mid = 0.5 * (left + right)
            if self.link_launch(sender, receiver, mid, service).get("can_launch", False):
                right = mid
            else:
                left = mid
        return {
            "sender": sender, "receiver": receiver,
            "closed_bracket_days": left, "open_bracket_days": right,
            "boundary_days": right, "tolerance_seconds": tolerance_seconds,
        }

    def direct_message_metrics(self, sender: str, receiver: str, t_ready_days: float,
                               incident: Optional[Incident] = None) -> dict:
        if sender == receiver:
            arr = t_ready_days + self.config.local_access_seconds / SECONDS_PER_DAY
            return {
                "service": "local", "can_launch": True, "status": "LOCAL",
                "distance_au": 0.0, "loss_probability": 0.0, "success_probability": 1.0,
                "emission_time_days": t_ready_days,
                "arrival_time_days": arr,
                "delay_minutes": self.config.local_access_seconds / 60.0,
            }
        # direct: 1s sending local access + 1s serialization before emission
        t_emit = t_ready_days + (self.config.local_access_seconds + self.config.serialization_seconds) / SECONDS_PER_DAY
        g = self.link_launch(sender, receiver, t_emit, "direct", incident=incident)
        if not g.get("can_launch", False):
            return {**g, "service": "direct"}
        arrival_application = g["arrival_time_days"] + self.config.local_access_seconds / SECONDS_PER_DAY
        return {
            **g, "service": "direct",
            "application_arrival_time_days": arrival_application,
            "delay_minutes": (arrival_application - t_ready_days) * MINUTES_PER_DAY,
        }
