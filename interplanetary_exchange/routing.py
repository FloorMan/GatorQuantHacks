from __future__ import annotations

from typing import Optional

from .config import ModelConfig, RELAYS
from .network import Incident, NetworkModel, SECONDS_PER_DAY, MINUTES_PER_DAY


class Router:
    def __init__(self, network: NetworkModel, config: ModelConfig):
        self.network = network
        self.config = config

    @staticmethod
    def candidate_routes(sender: str, receiver: str) -> list[list[str]]:
        if sender == receiver:
            return [[sender]]
        return [
            [sender, "Relay A", receiver],
            [sender, "Relay B", receiver],
            [sender, "Relay A", "Relay B", receiver],
            [sender, "Relay B", "Relay A", receiver],
        ]

    def evaluate_route(self, route: list[str], t_ready_days: float,
                       incident: Optional[Incident] = None,
                       allow_known_wait: bool = False) -> dict:
        if len(route) == 1:
            return {
                "route": route, "available": True, "arrival_time_days": t_ready_days,
                "delay_minutes": 0.0, "initial_success_probability": 1.0,
                "hop_results": [], "known_wait_minutes": 0.0,
            }
        current = t_ready_days
        prob = 1.0
        hops = []
        known_wait = 0.0
        for i in range(len(route) - 1):
            sender, receiver = route[i], route[i + 1]
            candidate_emit = current + self.config.serialization_seconds / SECONDS_PER_DAY
            if allow_known_wait:
                nxt = self.network.next_open_emission_time(
                    sender, receiver, candidate_emit, service="backbone",
                    max_wait_days=self.config.packet_lifetime_days,
                )
                if not nxt.get("found"):
                    return {"route": route, "available": False,
                            "blocked_hop": (sender, receiver), "status": nxt.get("status"),
                            "hop_results": hops}
                t_emit = float(nxt["emission_time_days"])
                known_wait += max(0.0, (t_emit - candidate_emit) * MINUTES_PER_DAY)
                # Incidents are unannounced and are evaluated only at the actual emission.
                g = self.network.link_launch(sender, receiver, t_emit, "backbone", incident=incident)
            else:
                t_emit = candidate_emit
                g = self.network.link_launch(sender, receiver, t_emit, "backbone", incident=incident)
            if not g.get("can_launch", False):
                return {
                    "route": route, "available": False, "blocked_hop": (sender, receiver),
                    "status": g.get("status"), "hop_results": hops,
                }
            prob *= g["success_probability"]
            hops.append(g)
            current = g["arrival_time_days"]
            if receiver in RELAYS and i < len(route) - 2:
                current += self.config.relay_processing_seconds / SECONDS_PER_DAY
        return {
            "route": route,
            "available": True,
            "arrival_time_days": current,
            "delay_minutes": (current - t_ready_days) * MINUTES_PER_DAY,
            "initial_success_probability": prob,
            "hop_results": hops,
            "known_wait_minutes": known_wait,
        }

    def best_route(self, sender: str, receiver: str, t_ready_days: float,
                   incident: Optional[Incident] = None,
                   objective: str = "delay", allow_known_wait: bool = False) -> Optional[dict]:
        feasible = []
        for r in self.candidate_routes(sender, receiver):
            result = self.evaluate_route(r, t_ready_days, incident=incident,
                                         allow_known_wait=allow_known_wait)
            if result["available"]:
                feasible.append(result)
        if not feasible:
            return None
        if objective == "reliability":
            return max(feasible, key=lambda x: (x["initial_success_probability"], -x["delay_minutes"]))
        return min(feasible, key=lambda x: (x["delay_minutes"], -x["initial_success_probability"]))

    def service_path(self, settlement: str, assigned_exchange: str, core: str,
                     t_days: float, *, objective: str = "reliability") -> dict:
        """S3 service path from client settlement to its financial authority."""
        client = self.network.direct_message_metrics(settlement, assigned_exchange, t_days)
        if not client.get("can_launch", False):
            return {"available": False, "settlement": settlement,
                    "exchange": assigned_exchange, "core": core,
                    "status": client.get("status", "CLIENT_BLOCKED")}
        client_delay = float(client.get("delay_minutes", 0.0))
        if assigned_exchange == core:
            return {
                "available": True, "settlement": settlement,
                "exchange": assigned_exchange, "core": core,
                "client_service": client.get("service"),
                "client_delay_min": client_delay,
                "backbone_route": [core], "backbone_delay_min": 0.0,
                "total_delay_min": client_delay,
            }
        route = self.best_route(assigned_exchange, core, t_days, objective=objective)
        if route is None:
            return {"available": False, "settlement": settlement,
                    "exchange": assigned_exchange, "core": core,
                    "status": "NO_BACKBONE_ROUTE"}
        return {
            "available": True, "settlement": settlement,
            "exchange": assigned_exchange, "core": core,
            "client_service": client.get("service"),
            "client_delay_min": client_delay,
            "backbone_route": route["route"],
            "backbone_delay_min": route["delay_minutes"],
            "total_delay_min": client_delay + route["delay_minutes"],
            "backbone_initial_success_probability": route["initial_success_probability"],
        }

    def service_availability(self, settlement: str, assigned_exchange: str, core: str,
                             start_days: float, window_hours: float = 24.0,
                             step_minutes: float = 30.0) -> float:
        """Availability of the route chosen at start_days over the following window.

        This matches S3's wording: report a best route and the fraction of the next
        24 h in which every link on *that route* can launch. New sessions at later
        times could choose another route, but that is a different service decision.
        """
        start = self.service_path(settlement, assigned_exchange, core, start_days)
        if not start.get("available", False):
            return 0.0
        fixed_route = start.get("backbone_route", [core])
        n = max(1, int(round(window_hours * 60.0 / step_minutes)))
        available = 0
        for k in range(n):
            t = start_days + (k * step_minutes) / MINUTES_PER_DAY
            client = self.network.direct_message_metrics(settlement, assigned_exchange, t)
            if not client.get("can_launch", False):
                continue
            if assigned_exchange == core:
                available += 1
                continue
            rr = self.evaluate_route(fixed_route, t)
            if rr.get("available", False):
                available += 1
        return available / n
