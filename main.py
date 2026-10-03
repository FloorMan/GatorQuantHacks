from pathlib import Path
import json
import argparse

from interplanetary_exchange.config import default_config
from interplanetary_exchange.data import load_project_data
from interplanetary_exchange.orbital import OrbitalModel
from interplanetary_exchange.network import NetworkModel
from interplanetary_exchange.routing import Router
from interplanetary_exchange.placement import PlacementOptimizer
from interplanetary_exchange.simulator import FederatedExchangeSimulator
from interplanetary_exchange.scenarios import run_required_demo_scenarios
from interplanetary_exchange.encoding import FixedWidthApplicationEncoding
from interplanetary_exchange.workload import WorkloadPlanner

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
OUTPUT.mkdir(exist_ok=True)

parser = argparse.ArgumentParser()
parser.add_argument("--reuse-baseline", action="store_true",
                    help="Reuse output/baseline_topology.json instead of rerunning placement search")
args = parser.parse_args()

config = default_config()
orbital_data, network_data = load_project_data(ROOT / "Alpha_Orbital_Data.zip")
orbital = OrbitalModel(orbital_data, network_data)
network = NetworkModel(orbital, network_data, config)
router = Router(network, config)

validation = orbital.validate_epoch()
print("Epoch validation (must be <= 1e-5 AU):")
for name, result in validation.items():
    print(f"  {name:8s} error={result['error_au']:.3e} pass={result['passes_1e-5']}")

baseline_path = OUTPUT / "baseline_topology.json"
if args.reuse_baseline and baseline_path.exists():
    baseline = json.loads(baseline_path.read_text())
    exchanges = tuple(baseline["exchanges"])
    core = baseline["core"]
    assignment = baseline["assignment"]
    print("\nReusing baseline topology:", exchanges, "core=", core)
else:
    optimizer = PlacementOptimizer(network, router, config)
    best, table = optimizer.optimize()
    table.to_csv(OUTPUT / "placement_screen.csv", index=False)
    exchanges, core, assignment = best.exchanges, best.core, best.assignment
    print("\nSelected baseline federation:")
    print("  exchanges:", exchanges)
    print("  core:", core)
    print("  assignment:", assignment)
    print("  feasible:", best.feasible)
    print("  score:", best.score)
    print("  mean client delay (min):", best.mean_client_delay_min)
    print("  mean client loss:", best.mean_client_loss)
    print("  unavailable fraction:", best.unavailable_fraction)
    print("  est direct packets/day:", best.estimated_direct_packets_per_day)
    print("  est backbone originations/day:", best.estimated_backbone_originations_per_day)

workload = WorkloadPlanner(config, router)
quota_tracker, workload_df, workload_summary = workload.build(assignment, core, exchanges, horizon_days=3)
workload_df.to_csv(OUTPUT / "final_workload_packet_schedule.csv", index=False)
quota_tracker.dataframe().to_csv(OUTPUT / "final_workload_quota_ledger.csv", index=False)

baseline = {
    "exchanges": list(exchanges),
    "core": core,
    "assignment": assignment,
    "application_encoding": {
        "network_header_bytes": FixedWidthApplicationEncoding.NETWORK_HEADER_BYTES,
        "application_payload_bytes": FixedWidthApplicationEncoding.PAYLOAD_BYTES,
        "application_envelope_bytes": FixedWidthApplicationEncoding.ENVELOPE_BYTES,
        "financial_record_bytes": FixedWidthApplicationEncoding.RECORD_BYTES,
        "records_per_application_packet": FixedWidthApplicationEncoding.RECORDS_PER_PACKET,
        "padding_bytes_at_full_record_capacity": FixedWidthApplicationEncoding.PADDING_BYTES,
    },
    "workload": {
        "equal_daily_client_instructions_per_principal": config.daily_client_instructions_per_principal,
        "batch_interval_hours": config.batch_interval_hours,
        "three_day_schedule_feasible": workload_summary.feasible,
        "max_backbone_rolling_24h": workload_summary.max_backbone_rolling_24h,
        "max_direct_rolling_24h": workload_summary.max_direct_rolling_24h,
    },
    "assumptions": {
        "reliability_target": config.reliability_target,
        "products": ["equity", "cash-settled futures"],
        "financial_guarantees_are_hard_constraints": True,
    },
}
baseline_path.write_text(json.dumps(baseline, indent=2))

sim = FederatedExchangeSimulator(config, network, router, core, assignment, exchanges)
scenario_results = run_required_demo_scenarios(sim, OUTPUT / "scenarios")
(OUTPUT / "scenario_results.json").write_text(json.dumps(scenario_results, indent=2, default=str))
print("\nDemo scenario results:")
print(json.dumps(scenario_results, indent=2, default=str))
print(f"\nOutputs written to {OUTPUT}")
