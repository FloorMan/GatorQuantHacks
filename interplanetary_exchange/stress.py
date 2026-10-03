from __future__ import annotations

from dataclasses import asdict
from typing import Callable, Iterable
import math
import pandas as pd

from .config import SETTLEMENTS, RELAYS
from .network import Incident
from .scenarios import run_future_stress_scenario


def candidate_s2_incidents(nodes: Iterable[str], start_hours: Iterable[float]):
    for node in nodes:
        for h in start_hours:
            yield Incident("gateway_isolation", node, float(h), 72.0)
            yield Incident("forced_loss", node, float(h), 6.0)
            yield Incident("endpoint_reset", node, float(h), 0.0)


class S2StressSearcher:
    """Search the three legal S2 incident families against the future scenario."""

    def __init__(self, simulator_factory: Callable[[], object], exchanges: tuple[str, ...], core: str):
        self.simulator_factory = simulator_factory
        self.exchanges = tuple(exchanges)
        self.core = core

    @staticmethod
    def _recovery_time_hours(run: dict) -> float:
        r = run.get("result", {})
        settlement = r.get("settlement", {}) if isinstance(r, dict) else {}
        t = settlement.get("spendable_time_days")
        if t is None:
            return math.inf
        return float(t) * 24.0

    @staticmethod
    def _price_reception_times(run: dict) -> list[float]:
        r = run.get("result", {})
        marks = r.get("marks", []) if isinstance(r, dict) else []
        out = []
        for m in marks:
            t = m.get("observation_received_time_days") if isinstance(m, dict) else None
            if t is not None:
                out.append(float(t) * 24.0)
        return out

    def _run(self, incident: Incident | None) -> dict:
        sim = self.simulator_factory()
        if incident is None:
            # Scenario helper expects an Incident, so run the same path with a harmless expired incident.
            incident = Incident("forced_loss", "Relay A", -1000.0, 0.0)
        return run_future_stress_scenario(sim, incident)

    def search(self, start_hours: Iterable[float] | None = None) -> tuple[pd.DataFrame, dict]:
        # Six-hour grid can be supplied for an exhaustive temporal search.  The default
        # targets the scenario's price/maturity events plus scheduled-maintenance vicinity.
        if start_hours is None:
            start_hours = [24.0, 48.0, 96.0, 120.0, 144.0, 192.0, 216.0, 234.0, 240.0]
        starts = [float(x) for x in start_hours]

        baseline = self._run(None)
        base_recovery = self._recovery_time_hours(baseline)
        base_marks = self._price_reception_times(baseline)

        incidents = []
        for node in SETTLEMENTS:
            for h in starts:
                incidents.append(Incident("gateway_isolation", node, h, 72.0))
        for node in tuple(SETTLEMENTS) + tuple(RELAYS):
            for h in starts:
                incidents.append(Incident("forced_loss", node, h, 6.0))
        for node in sorted(set(self.exchanges) | {self.core}):
            for h in starts:
                incidents.append(Incident("endpoint_reset", node, h, 0.0))

        rows = []
        best_score = -1.0
        best_detail = None
        critical_hours = (120.0, 216.0, 240.0)
        # The future scenario uses only the long-side exchange, short-side exchange,
        # and clearing core as persistent backbone endpoints. Resets elsewhere cannot
        # affect this S2 transaction.
        baseline_run = baseline.get("result", {})
        used_endpoint_nodes = set(self.exchanges)  # conservative fallback
        try:
            # Model defaults: long on Mars, short on Neptune.
            sim_probe = self.simulator_factory()
            used_endpoint_nodes = {
                self.core,
                sim_probe.assignment[sim_probe.ledger.accounts["MARS_A"].settlement],
                sim_probe.assignment[sim_probe.ledger.accounts["NEPTUNE_A"].settlement],
            }
        except Exception:
            pass

        for inc in incidents:
            # Exact no-effect pruning: link incidents matter only when an application or
            # settlement communication is scheduled inside their interval. Endpoint
            # resets matter only at endpoints used by this scenario. Skipped candidates
            # are still retained in the search table with baseline score zero.
            if inc.kind in {"gateway_isolation", "forced_loss"}:
                affects_scheduled_event = any(inc.start_hours <= h < inc.end_hours for h in critical_hours)
                if not affects_scheduled_event:
                    rows.append({
                        **asdict(inc), "scenario_status": baseline_run.get("status"),
                        "recovery_time_hours": base_recovery,
                        "recovery_delay_vs_baseline_hours": 0.0,
                        "max_price_reception_delay_hours": 0.0,
                        "forced_loss_launches": 0, "incomplete": False,
                        "stress_score": 0.0, "pruned_no_effect": True,
                    })
                    continue
            elif inc.kind == "endpoint_reset" and inc.node not in used_endpoint_nodes:
                rows.append({
                    **asdict(inc), "scenario_status": baseline_run.get("status"),
                    "recovery_time_hours": base_recovery,
                    "recovery_delay_vs_baseline_hours": 0.0,
                    "max_price_reception_delay_hours": 0.0,
                    "forced_loss_launches": 0, "incomplete": False,
                    "stress_score": 0.0, "pruned_no_effect": True,
                })
                continue

            run = self._run(inc)
            recovery = self._recovery_time_hours(run)
            marks = self._price_reception_times(run)
            incomplete = not math.isfinite(recovery)
            recovery_delay = (recovery - base_recovery) if not incomplete else 1_000_000.0
            mark_delay = 0.0
            for i, b in enumerate(base_marks):
                if i >= len(marks):
                    mark_delay = max(mark_delay, 1_000_000.0)
                else:
                    mark_delay = max(mark_delay, marks[i] - b)
            forced = int(run.get("forced_loss_launches", 0))
            score = max(0.0, recovery_delay) + max(0.0, mark_delay) + 10.0 * forced
            if incomplete:
                score += 10_000_000.0
            row = {
                **asdict(inc),
                "scenario_status": run.get("result", {}).get("status"),
                "recovery_time_hours": None if incomplete else recovery,
                "recovery_delay_vs_baseline_hours": recovery_delay,
                "max_price_reception_delay_hours": mark_delay,
                "forced_loss_launches": forced,
                "incomplete": incomplete,
                "stress_score": score,
                "pruned_no_effect": False,
            }
            rows.append(row)
            if score > best_score:
                best_score = score
                best_detail = {"incident": asdict(inc), "run": run,
                               "baseline_recovery_hours": base_recovery,
                               "stress_score": score}

        df = pd.DataFrame(rows).sort_values(
            ["stress_score", "kind", "node", "start_hours"], ascending=[False, True, True, True]
        ).reset_index(drop=True)
        return df, best_detail
    def search_refined(self, coarse_start_hours: Iterable[float],
                       *, coarse_half_width_hours: float = 6.0,
                       medium_step_hours: float = 1.0,
                       fine_half_width_hours: float = 1.0,
                       fine_step_minutes: float = 10.0) -> tuple[pd.DataFrame, dict]:
        """Grid search followed by local start-time refinement of the worst family/node.

        The legal incident type and node are selected by the full coarse search.  The
        winner's start time is then refined first hourly and then every 10 minutes.
        This is still numerical evidence rather than an analytical proof over all real
        start times, but it removes the coarse six-hour quantization from the reported
        worst candidate.
        """
        coarse_df, coarse_best = self.search(start_hours=coarse_start_hours)
        coarse_df = coarse_df.copy()
        coarse_df["search_stage"] = "coarse"
        if not coarse_best:
            return coarse_df, coarse_best

        kind = coarse_best["incident"]["kind"]
        node = coarse_best["incident"]["node"]
        h0 = float(coarse_best["incident"]["start_hours"])
        duration = {"gateway_isolation": 72.0, "forced_loss": 6.0, "endpoint_reset": 0.0}[kind]

        baseline = self._run(None)
        base_recovery = self._recovery_time_hours(baseline)
        base_marks = self._price_reception_times(baseline)

        def evaluate_start(h: float, stage: str) -> tuple[dict, dict]:
            inc = Incident(kind, node, float(h), duration)
            run = self._run(inc)
            recovery = self._recovery_time_hours(run)
            marks = self._price_reception_times(run)
            incomplete = not math.isfinite(recovery)
            recovery_delay = (recovery - base_recovery) if not incomplete else 1_000_000.0
            mark_delay = 0.0
            for i, b in enumerate(base_marks):
                if i >= len(marks):
                    mark_delay = max(mark_delay, 1_000_000.0)
                else:
                    mark_delay = max(mark_delay, marks[i] - b)
            forced = int(run.get("forced_loss_launches", 0))
            score = max(0.0, recovery_delay) + max(0.0, mark_delay) + 10.0 * forced
            if incomplete:
                score += 10_000_000.0
            row = {
                **asdict(inc),
                "scenario_status": run.get("result", {}).get("status"),
                "recovery_time_hours": None if incomplete else recovery,
                "recovery_delay_vs_baseline_hours": recovery_delay,
                "max_price_reception_delay_hours": mark_delay,
                "forced_loss_launches": forced,
                "incomplete": incomplete,
                "stress_score": score,
                "pruned_no_effect": False,
                "search_stage": stage,
            }
            detail = {"incident": asdict(inc), "run": run,
                      "baseline_recovery_hours": base_recovery, "stress_score": score,
                      "search_stage": stage}
            return row, detail

        medium_starts = []
        x = max(0.0, h0 - coarse_half_width_hours)
        end = h0 + coarse_half_width_hours
        while x <= end + 1e-12:
            medium_starts.append(round(x, 10))
            x += medium_step_hours
        medium_rows = []
        best_detail = coarse_best
        best_score = float(coarse_best["stress_score"])
        best_h = h0
        for h in medium_starts:
            row, detail = evaluate_start(h, "medium")
            medium_rows.append(row)
            if detail["stress_score"] > best_score:
                best_score, best_detail, best_h = detail["stress_score"], detail, h

        fine_step_hours = fine_step_minutes / 60.0
        fine_rows = []
        x = max(0.0, best_h - fine_half_width_hours)
        end = best_h + fine_half_width_hours
        while x <= end + 1e-12:
            row, detail = evaluate_start(round(x, 10), "fine")
            fine_rows.append(row)
            if detail["stress_score"] > best_score:
                best_score, best_detail, best_h = detail["stress_score"], detail, x
            x += fine_step_hours

        refined = pd.concat(
            [coarse_df, pd.DataFrame(medium_rows), pd.DataFrame(fine_rows)],
            ignore_index=True, sort=False,
        ).sort_values(
            ["stress_score", "search_stage", "kind", "node", "start_hours"],
            ascending=[False, True, True, True, True],
        ).reset_index(drop=True)
        best_detail["refinement"] = {
            "coarse_half_width_hours": coarse_half_width_hours,
            "medium_step_hours": medium_step_hours,
            "fine_half_width_hours": fine_half_width_hours,
            "fine_step_minutes": fine_step_minutes,
        }
        return refined, best_detail

