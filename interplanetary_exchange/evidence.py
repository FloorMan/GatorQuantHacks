from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict, Optional

import numpy as np
import pandas as pd

from .config import SETTLEMENTS, ModelConfig
from .network import NetworkModel
from .routing import Router
from .scenarios import run_shifted_epoch_scenarios


class EvidenceBuilder:
    """S3, E4 and E5 evidence directly from the network/router/simulator rules."""

    def __init__(self, network: NetworkModel, router: Router, config: ModelConfig,
                 core: str, assignment: Dict[str, str],
                 simulator_factory: Optional[Callable[[], object]] = None):
        self.network = network
        self.router = router
        self.config = config
        self.core = core
        self.assignment = assignment
        self.simulator_factory = simulator_factory

    # ------------------------------------------------------------------
    # S3
    # ------------------------------------------------------------------
    def access_table(self, start_hour: float, availability_step_minutes: float = 30.0) -> pd.DataFrame:
        """S3 hour-h table generated through Router.service_path/service_availability."""
        rows = []
        start_days = start_hour / 24.0
        for s in SETTLEMENTS:
            exchange = self.assignment[s]
            path = self.router.service_path(s, exchange, self.core, start_days)
            availability = self.router.service_availability(
                s, exchange, self.core, start_days,
                window_hours=24.0, step_minutes=availability_step_minutes,
            )
            for product in ("EQUITY", "FUTURE"):
                rows.append({
                    "settlement": s,
                    "transaction_type": product,
                    "assigned_exchange": exchange,
                    "authoritative_clearing": self.core,
                    "price_source": self.config.price_source_settlement if product == "FUTURE" else None,
                    "route_at_start": (
                        f"{s} => {exchange} | " + " -> ".join(path.get("backbone_route", []))
                        if path.get("available") else path.get("status")
                    ),
                    "one_way_delay_minutes_at_start": path.get("total_delay_min"),
                    "availability_next_24h": availability,
                    "availability_step_minutes": availability_step_minutes,
                    "available_at_start": path.get("available", False),
                })
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------
    # E4
    # ------------------------------------------------------------------
    @staticmethod
    def _candidate_directed_edges() -> list[tuple[str, str]]:
        """All 38 directed backbone links implied by the 19 two-way candidates.

        Forward and reverse light time, solar geometry, and loss are not assumed equal
        because the receiver moves during flight.  E4 therefore scans both directions.
        """
        undirected = []
        for s in SETTLEMENTS:
            undirected.extend([(s, "Relay A"), (s, "Relay B")])
        undirected.append(("Relay A", "Relay B"))
        directed = []
        for a, b in undirected:
            directed.append((a, b))
            directed.append((b, a))
        return directed

    def full_e4_scan(self, years: float | None = None,
                     link_step_days: float | None = None,
                     route_step_days: float | None = None) -> dict:
        years = float(years or self.config.e4_scan_years)
        link_step_days = float(link_step_days or self.config.e4_link_step_days)
        route_step_days = float(route_step_days or self.config.e4_route_step_days)
        horizon = years * 365.25

        link_times = np.append(np.arange(0.0, horizon, link_step_days), horizon)
        link_rows = []
        first_closed_open_bracket = None

        for a, b in self._candidate_directed_edges():
            states = []
            delays = []
            current_closed = 0
            longest_closed = 0
            for t in link_times:
                g = self.network.link_launch(a, b, float(t), "backbone")
                is_open = bool(g.get("can_launch", False))
                states.append(is_open)
                if is_open:
                    delays.append(float(g["light_time_minutes"]))
                    longest_closed = max(longest_closed, current_closed)
                    current_closed = 0
                else:
                    current_closed += 1
            longest_closed = max(longest_closed, current_closed)
            for i in range(len(states) - 1):
                if not states[i] and states[i + 1] and link_times[i] > 30.0:
                    if first_closed_open_bracket is None:
                        first_closed_open_bracket = (a, b, float(link_times[i]), float(link_times[i + 1]))
                    break
            link_rows.append({
                "directed_edge": f"{a} -> {b}",
                "sender": a, "receiver": b,
                "years": years,
                "step_days": link_step_days,
                "samples": len(link_times),
                "sampled_availability": float(np.mean(states)),
                "min_light_time_minutes_when_open": min(delays) if delays else None,
                "max_light_time_minutes_when_open": max(delays) if delays else None,
                "longest_sampled_closed_run_days_lower_bound": max(0, longest_closed - 1) * link_step_days,
                "shortest_closure_that_could_be_missed_days": link_step_days,
            })
        link_df = pd.DataFrame(link_rows)

        refined = None
        if first_closed_open_bracket is not None:
            a, b, left, right = first_closed_open_bracket
            refined = self.network.refine_availability_boundary(
                a, b, left, right, service="backbone",
                tolerance_seconds=self.config.e4_boundary_tolerance_seconds,
            )
            refined["effect_on_packets_in_flight"] = (
                "A packet already emitted on a path that passed the emission-to-arrival solar test "
                "continues to its computed arrival; the natural closure prevents subsequent launches."
            )

        # Tier-3 route/service scan. Cache the repeated exchange->core route for settlements
        # assigned to the same regional exchange.
        route_times = np.append(np.arange(0.0, horizon, route_step_days), horizon)
        per_settlement = {s: {"open": 0, "delays": []} for s in SETTLEMENTS}
        difficult = {"time_days": 0.0, "unavailable_count": -1, "worst_delay_min": -1.0,
                     "settlement": None}
        for t in route_times:
            route_cache = {}
            unavailable_count = 0
            worst_delay = -1.0
            worst_s = None
            for s in SETTLEMENTS:
                e = self.assignment[s]
                client = self.network.direct_message_metrics(s, e, float(t))
                if not client.get("can_launch", False):
                    unavailable_count += 1
                    continue
                if e == self.core:
                    total = float(client.get("delay_minutes", 0.0))
                    available = True
                else:
                    key = (e, self.core)
                    if key not in route_cache:
                        route_cache[key] = self.router.best_route(e, self.core, float(t), objective="reliability")
                    r = route_cache[key]
                    available = r is not None
                    total = (float(client.get("delay_minutes", 0.0)) + float(r["delay_minutes"])) if r else None
                if available:
                    per_settlement[s]["open"] += 1
                    per_settlement[s]["delays"].append(total)
                    if total > worst_delay:
                        worst_delay, worst_s = total, s
                else:
                    unavailable_count += 1
            if (unavailable_count, worst_delay) > (difficult["unavailable_count"], difficult["worst_delay_min"]):
                difficult = {"time_days": float(t), "unavailable_count": unavailable_count,
                             "worst_delay_min": float(worst_delay), "settlement": worst_s}

        route_rows = []
        for s in SETTLEMENTS:
            d = per_settlement[s]["delays"]
            route_rows.append({
                "settlement": s,
                "assigned_exchange": self.assignment[s],
                "core": self.core,
                "years": years,
                "route_step_days": route_step_days,
                "sampled_availability": per_settlement[s]["open"] / len(route_times),
                "min_one_way_service_delay_minutes": min(d) if d else None,
                "max_one_way_service_delay_minutes": max(d) if d else None,
            })
        route_df = pd.DataFrame(route_rows)
        poorest = route_df.sort_values(
            ["sampled_availability", "max_one_way_service_delay_minutes"],
            ascending=[True, False]
        ).iloc[0].to_dict()

        summary = {
            "tier_claimed": 3,
            "years": years,
            "link_step_days": link_step_days,
            "route_step_days": route_step_days,
            "shortest_closure_that_could_be_missed_days": link_step_days,
            "difficult_epoch": difficult,
            "poorest_service": poorest,
            "refined_boundary": refined,
        }
        return {"link_scan": link_df, "route_scan": route_df, "summary": summary}

    # ------------------------------------------------------------------
    # E5
    # ------------------------------------------------------------------
    def shifted_epoch_table(self, offsets_years=(1, 10, 100)) -> pd.DataFrame:
        rows = []
        for y in offsets_years:
            t = y * 365.25
            for s in SETTLEMENTS:
                m = self.router.service_path(s, self.assignment[s], self.core, t)
                rows.append({
                    "offset_years": y,
                    "settlement": s,
                    "exchange": self.assignment[s],
                    "core": self.core,
                    "available": m.get("available", False),
                    "conditional_one_way_delay_minutes": m.get("total_delay_min"),
                    "availability_next_24h": self.router.service_availability(
                        s, self.assignment[s], self.core, t, window_hours=24.0, step_minutes=30.0
                    ),
                    "route": " -> ".join(m.get("backbone_route", [])) if m.get("available") else m.get("status"),
                })
        return pd.DataFrame(rows)

    def full_e5(self, difficult_epoch_days: float, offsets_years=(1, 10, 100)) -> dict:
        table = self.shifted_epoch_table(offsets_years)
        scenario_summaries = {}
        if self.simulator_factory is not None:
            # Tier 1: give a conditional completion result for the value-move scenario at each fixed offset.
            for y in offsets_years:
                sim = self.simulator_factory()
                result = run_shifted_epoch_scenarios(sim, y * 365.25)
                scenario_summaries[f"offset_{y}y"] = {
                    "value_move": result["value_move"],
                }
            # Tier 2/3: difficult epoch value move + both price directions.
            sim = self.simulator_factory()
            difficult_results = run_shifted_epoch_scenarios(sim, difficult_epoch_days)
            scenario_summaries["difficult_epoch"] = {
                "t0_days": difficult_epoch_days,
                "t0_years": difficult_epoch_days / 365.25,
                **difficult_results,
            }
        return {
            "tier_claimed": 3,
            "table": table,
            "scenarios": scenario_summaries,
        }

    def export_all(self, directory: str | Path) -> dict:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        self.access_table(0.0).to_csv(d / "S3_access_hour_0.csv", index=False)
        self.access_table(300.0).to_csv(d / "S3_access_hour_300.csv", index=False)

        e4 = self.full_e4_scan()
        e4["link_scan"].to_csv(d / "E4_link_scan_200y.csv", index=False)
        e4["route_scan"].to_csv(d / "E4_route_service_200y.csv", index=False)
        (d / "E4_summary.json").write_text(json.dumps(e4["summary"], indent=2, default=str))

        diff = float(e4["summary"]["difficult_epoch"]["time_days"])
        e5 = self.full_e5(diff)
        e5["table"].to_csv(d / "E5_shifted_epochs.csv", index=False)
        (d / "E5_scenarios.json").write_text(json.dumps(e5["scenarios"], indent=2, default=str))
        return {"e4": e4["summary"], "e5_tier": e5["tier_claimed"]}
