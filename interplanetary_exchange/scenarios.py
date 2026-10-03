from __future__ import annotations

from pathlib import Path
from .simulator import FederatedExchangeSimulator
from .network import Incident


def _run_future_path(sim: FederatedExchangeSimulator, prices: list[tuple[float, float]],
                     *, t0_days: float = 0.0, incident: Incident | None = None,
                     output_dir: Path | None = None) -> dict:
    f = sim.open_future(
        long_id="MARS_A", short_id="NEPTUNE_A", quantity=23, multiplier=100.0,
        entry_price=100.0, maturity_hours=240.0, t0_days=t0_days, incident=incident,
    )
    if f.get("status") != "OPEN":
        return f
    cid = f["contract_id"]
    marks = []
    final_received = t0_days + 10.0
    final_price = prices[-1][1]
    source_ready = t0_days
    for offset_days, price in prices:
        release_time = t0_days + offset_days
        send_ready = max(release_time, source_ready)
        m = sim.mark_future(cid, price, send_ready, incident=incident)
        m["declared_release_time_days"] = release_time
        marks.append(m)
        if m.get("status") == "PRICE_NOT_RECEIVED":
            return {"status": "PRICE_PATH_INCOMPLETE", "contract_id": cid, "marks": marks}
        pm = m.get("price_message", {})
        attempts = pm.get("attempts", []) if isinstance(pm, dict) else []
        if attempts:
            last_origin = max(a.get("origination_time_days", send_ready) for a in attempts)
            source_ready = last_origin + (sim.config.direct_min_launch_spacing_seconds + 0.001) / 86400.0
        else:
            source_ready = send_ready
        if offset_days == prices[-1][0]:
            final_received = m["observation_received_time_days"]
    settlement = sim.settle_future(cid, final_price, final_received, incident=incident)
    return {"status": settlement.get("status"), "contract_id": cid,
            "open": f, "marks": marks, "settlement": settlement}


def run_required_demo_scenarios(sim: FederatedExchangeSimulator,
                                output_dir: str | Path | None = None) -> dict:
    """Required value move, >=240 h price paths, and a binding constraint."""
    results = {}
    out = Path(output_dir) if output_dir is not None else None

    sim.reset_financial_state()
    results["equity_value_move"] = sim.open_equity_trade(
        buyer_id="EARTH_A", seller_id="NEPTUNE_A", quantity=50, price=100.0, t0_days=0.0
    )
    if out:
        sim.export(out / "equity_value_move")

    sim.reset_financial_state()
    results["future_rising"] = _run_future_path(
        sim, [(5.0, 110.0), (9.0, 118.0), (10.0, 120.0)],
        output_dir=(out / "future_rising") if out else None,
    )
    if out:
        sim.export(out / "future_rising")

    sim.reset_financial_state()
    results["future_falling"] = _run_future_path(
        sim, [(5.0, 90.0), (9.0, 82.0), (10.0, 80.0)],
        output_dir=(out / "future_falling") if out else None,
    )
    if out:
        sim.export(out / "future_falling")

    # Deliberately oversized opening position: initial margin itself must reject it.
    sim.reset_financial_state()
    try:
        results["constraint_bind"] = sim.open_future(
            long_id="MARS_A", short_id="NEPTUNE_A", quantity=50, multiplier=100.0,
            entry_price=100.0, maturity_hours=240.0, t0_days=0.0,
        )
    except ValueError as e:
        results["constraint_bind"] = {
            "status": "REJECTED_INSUFFICIENT_MARGIN", "reason": str(e)
        }
    if out:
        sim.export(out / "constraint_bind")

    return results


def run_shifted_epoch_scenarios(sim: FederatedExchangeSimulator, t0_days: float,
                                output_dir: str | Path | None = None) -> dict:
    out = Path(output_dir) if output_dir is not None else None
    results = {}

    sim.reset_financial_state()
    results["value_move"] = sim.open_equity_trade(
        buyer_id="EARTH_A", seller_id="NEPTUNE_A", quantity=50, price=100.0,
        t0_days=t0_days,
    )
    if out:
        sim.export(out / "value_move")

    sim.reset_financial_state()
    results["future_rising"] = _run_future_path(
        sim, [(5.0, 110.0), (9.0, 118.0), (10.0, 120.0)], t0_days=t0_days
    )
    if out:
        sim.export(out / "future_rising")

    sim.reset_financial_state()
    results["future_falling"] = _run_future_path(
        sim, [(5.0, 90.0), (9.0, 82.0), (10.0, 80.0)], t0_days=t0_days
    )
    if out:
        sim.export(out / "future_falling")
    return results


def run_future_stress_scenario(sim: FederatedExchangeSimulator, incident: Incident) -> dict:
    """S2 price-dependent stress run with the incident layered onto the normal rules."""
    sim.reset_financial_state()
    result = _run_future_path(
        sim, [(5.0, 110.0), (9.0, 118.0), (10.0, 120.0)],
        t0_days=0.0, incident=incident,
    )
    obligations = sim.ledger.obligations_df().to_dict("records")
    packet_rows = sim.trace.packets_df()
    forced_losses = int(packet_rows["outcome"].astype(str).str.contains("INCIDENT").sum()) if not packet_rows.empty and "outcome" in packet_rows else 0
    return {
        "result": result,
        "obligations": obligations,
        "forced_loss_launches": forced_losses,
        "metrics": sim.metrics(),
    }
