from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import ceil, log
from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd

from .config import ModelConfig, SETTLEMENTS
from .network import NetworkModel
from .routing import Router
from .encoding import FixedWidthApplicationEncoding
from .workload import WorkloadPlanner


@dataclass
class PlacementResult:
    exchanges: tuple[str, ...]
    core: str
    assignment: Dict[str, str]
    feasible: bool
    score: float
    mean_client_delay_min: float
    mean_client_loss: float
    unavailable_fraction: float
    estimated_direct_packets_per_day: float
    estimated_backbone_originations_per_day: float


class PlacementOptimizer:
    """Coarse topology screen. Financial guarantees are hard constraints; score ranks feasible designs."""

    def __init__(self, network: NetworkModel, router: Router, config: ModelConfig):
        self.network = network
        self.router = router
        self.config = config
        self._client_cache = {}
        self._route_cache = {}
        # Placement needs exact quota timestamps but not exact acknowledgement flight times;
        # the final baseline workload is regenerated with the Router after selection.
        self.workload = WorkloadPlanner(config, router=None)
        self.encoding = FixedWidthApplicationEncoding

    @staticmethod
    def copies_for_target(p_loss: float, target: float, max_copies: int = 12) -> int:
        if p_loss <= 0.0:
            return 1
        if p_loss >= 1.0:
            return max_copies + 1
        n = int(ceil(log(1.0 - target) / log(p_loss)))
        return max(1, n)

    def _client_metric(self, settlement: str, exchange: str, t_days: float) -> dict:
        key = (settlement, exchange, round(float(t_days), 9))
        if key in self._client_cache:
            return self._client_cache[key]
        if settlement == exchange:
            out = {"available": True, "delay": self.config.local_access_seconds / 60.0, "loss": 0.0, "copies": 0}
            self._client_cache[key] = out
            return out
        m = self.network.direct_message_metrics(settlement, exchange, t_days)
        if not m.get("can_launch", False):
            out = {"available": False, "delay": np.inf, "loss": 1.0, "copies": self.config.direct_packets_per_principal_rolling_24h + 1}
            self._client_cache[key] = out
            return out
        p = float(m["loss_probability"])
        copies = self.copies_for_target(p, self.config.reliability_target,
                                       self.config.direct_packets_per_principal_rolling_24h)
        out = {"available": True, "delay": float(m["delay_minutes"]), "loss": p, "copies": copies}
        self._client_cache[key] = out
        return out

    def _best_route_cached(self, sender: str, receiver: str, t_days: float):
        key = (sender, receiver, round(float(t_days), 9))
        if key not in self._route_cache:
            self._route_cache[key] = self.router.best_route(sender, receiver, float(t_days), objective="reliability")
        return self._route_cache[key]

    def _assign_at_time(self, exchanges: tuple[str, ...], t_days: float) -> Dict[str, str]:
        assignment = {}
        for s in SETTLEMENTS:
            demand = self.config.demand_per_day[s]
            max_copies = max(1, self.config.direct_packets_per_principal_rolling_24h // max(1, demand))
            candidates = []
            for e in exchanges:
                m = self._client_metric(s, e, t_days)
                if s == e:
                    candidates.append((0, 0.0, 0, m["delay"], e))
                    continue
                planned = min(m["copies"], max_copies)
                effective_failure = m["loss"] ** planned if m["available"] else 1.0
                # Geometry first, then achieved reliability under the direct quota, then packet burden and latency.
                candidates.append((0 if m["available"] else 1, effective_failure, planned, m["delay"], e))
            assignment[s] = min(candidates)[-1]
        return assignment

    def evaluate(self, exchanges: tuple[str, ...], core: str, sample_days: np.ndarray) -> PlacementResult:
        # Stable epoch assignment; exact recurring quota workload is evaluated separately.
        assignment = self._assign_at_time(exchanges, 0.0)
        _, workload_df, workload_summary = self.workload.build(assignment, core, exchanges, horizon_days=3)
        quota_violation = not workload_summary.feasible

        total_weight = 0.0
        delay_sum = 0.0
        loss_sum = 0.0
        unavailable = 0.0
        checks = 0.0
        backbone_delay_cost = 0.0
        backbone_loss_cost = 0.0

        batches_per_day = int(round(24.0 / self.config.batch_interval_hours))
        for t in sample_days:
            edge_volume = {e: 0 for e in exchanges}
            for settlement in SETTLEMENTS:
                exchange = assignment[settlement]
                demand = self.config.demand_per_day[settlement]
                edge_volume[exchange] += demand
                m = self._client_metric(settlement, exchange, float(t))
                checks += 1
                if not m["available"]:
                    unavailable += 1
                    continue
                total_weight += demand
                delay_sum += demand * m["delay"]
                loss_sum += demand * (m["loss"] if settlement != exchange else 0.0)

            for exchange, volume in edge_volume.items():
                if exchange == core or volume == 0:
                    continue
                route = self._best_route_cached(exchange, core, float(t))
                if route is None:
                    unavailable += 1
                    checks += 1
                    continue
                records_per_batch = int(ceil(volume / batches_per_day))
                packets_per_batch = self.encoding.packet_count(records_per_batch)
                # Data batch + application-level clearing confirmation.
                batch_packets = batches_per_day * packets_per_batch * 2
                backbone_delay_cost += batch_packets * route["delay_minutes"]
                backbone_loss_cost += batch_packets * (1.0 - route["initial_success_probability"])

        unavailable_fraction = unavailable / max(1.0, checks)
        mean_delay = delay_sum / max(1.0, total_weight)
        mean_loss = loss_sum / max(1.0, total_weight)
        horizon_days = 3.0
        mean_direct = workload_summary.direct_packets / horizon_days
        mean_backbone = workload_summary.backbone_originations / horizon_days

        feasible = not quota_violation
        score = (
            self.config.availability_weight * unavailable_fraction
            + self.config.quota_weight * (0.0 if feasible else 1.0)
            + self.config.latency_weight * (mean_delay + backbone_delay_cost / max(1.0, len(sample_days)))
            + self.config.loss_weight * (mean_loss + backbone_loss_cost / max(1.0, len(sample_days)))
            + self.config.packet_weight * (mean_direct + mean_backbone)
            + self.config.institution_weight * len(exchanges)
        )
        return PlacementResult(
            exchanges=exchanges, core=core, assignment=assignment, feasible=feasible, score=score,
            mean_client_delay_min=mean_delay, mean_client_loss=mean_loss,
            unavailable_fraction=unavailable_fraction,
            estimated_direct_packets_per_day=mean_direct,
            estimated_backbone_originations_per_day=mean_backbone,
        )

    def optimize(self, sample_days: Optional[np.ndarray] = None) -> tuple[PlacementResult, pd.DataFrame]:
        if sample_days is None:
            sample_days = np.arange(
                0.0,
                self.config.placement_scan_years * 365.25 + 1e-9,
                self.config.placement_scan_step_days,
            )
        results: list[PlacementResult] = []
        for k in range(self.config.min_exchanges, self.config.max_exchanges + 1):
            for exchanges in combinations(SETTLEMENTS, k):
                for core in exchanges:
                    results.append(self.evaluate(exchanges, core, sample_days))
        results.sort(key=lambda r: (not r.feasible, r.score))
        best = results[0]
        df = pd.DataFrame([
            {
                "exchanges": ", ".join(r.exchanges), "core": r.core,
                "feasible": r.feasible, "score": r.score,
                "mean_client_delay_min": r.mean_client_delay_min,
                "mean_client_loss": r.mean_client_loss,
                "unavailable_fraction": r.unavailable_fraction,
                "estimated_direct_packets_per_day": r.estimated_direct_packets_per_day,
                "estimated_backbone_originations_per_day": r.estimated_backbone_originations_per_day,
                "assignment": repr(r.assignment),
            }
            for r in results
        ])
        return best, df
